"""Async client for the deployed LendingVault contract (V2 — Sprint 4).

Used by the keeper loop's 4-step liquidation:

  1. `withdraw_ctf_for_liquidation(loanId, priceE6)`  — vault → keeper CTF
  2. (off-chain) sell CTF on Polymarket — `polymarket_seller`
  3. `transfer_usdc(vault, actualProceeds)`           — USDC ERC20
  4. `settle_liquidation(loanId, reason, actualProceeds)` — vault distributes

If step 2 fails (slippage / market closed / Polymarket down) the keeper calls
`return_ctf_from_keeper(loanId)` to push CTF back into the vault, which
resets `loan.withdrawn` so the next tick can retry.

Frozen interface contract — see plan v0.3 §2 K15. The V1 atomic
`liquidate(uint256, string, uint256)` is REMOVED (the V2 contract no longer
exposes it).

Gas: EIP-1559 with `priority_fee_gwei` tip + replacement-on-stuck up to
`max_retries`. Same pattern as Sprint 3.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from eth_account import Account
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)


# Mini-ABIs. Events are decoded by hand in `event_decoder.py`; keep both files
# in sync with the V2 contract source.
_VAULT_ABI = [
    {
        "type": "function",
        "name": "withdrawCtfForLiquidation",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "loanId", "type": "uint256"},
            {"name": "currentPriceE6", "type": "uint256"},
        ],
        "outputs": [{"name": "expectedProceeds", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "settleLiquidation",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "loanId", "type": "uint256"},
            {"name": "reason", "type": "string"},
            {"name": "actualProceeds", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "returnCtfFromKeeper",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "loanId", "type": "uint256"}],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "lpPoolBalance",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# Polymarket Conditional Tokens Framework on Polygon mainnet. Used for the
# new V2 step where keeper EOA forwards withdrawn CTF to its PM Safe proxy
# (which is the only address allowed to place CLOB V2 orders).
POLYMARKET_CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

_CTF_ABI = [
    {
        "type": "function",
        "name": "safeTransferFrom",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "id", "type": "uint256"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

_USDC_ABI = [
    {
        "type": "function",
        "name": "transfer",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

LIQUIDATE_REASONS = ("ltv_breach", "kickoff_due")


class KeeperKeyMissing(RuntimeError):
    """Raised when keeper_private_key is unset and a state-changing call is attempted."""


class LiquidationFailed(RuntimeError):
    """Raised when an on-chain step reverted or never confirmed within retries."""


@dataclass
class TxReceipt:
    tx_hash: str
    block_number: int
    success: bool
    attempts: int


class VaultClient:
    def __init__(
        self,
        rpc_url: str,
        vault_address: str,
        usdc_address: str,
        chain_id: int,
        *,
        private_key: str | None = None,
        priority_fee_gwei: int = 30,
        replacement_after_s: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        # Polygon is a PoA chain — `extraData` is 97 bytes (vs Ethereum's 32).
        # Without this middleware, every getBlock/getTransactionReceipt that
        # touches a Polygon block raises ExtraDataLengthError.
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self.vault_address = AsyncWeb3.to_checksum_address(vault_address)
        self.usdc_address = AsyncWeb3.to_checksum_address(usdc_address)
        self.chain_id = chain_id
        self.account = Account.from_key(private_key) if private_key else None
        self.vault_contract = self.w3.eth.contract(
            address=self.vault_address, abi=_VAULT_ABI
        )
        self.usdc_contract = self.w3.eth.contract(
            address=self.usdc_address, abi=_USDC_ABI
        )
        self.ctf_contract = self.w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(POLYMARKET_CTF_ADDRESS),
            abi=_CTF_ABI,
        )
        self._priority_fee_wei = priority_fee_gwei * 10**9
        self._replacement_after_s = replacement_after_s
        self._max_retries = max_retries

    # ---------- public: 4-step liquidation ----------

    async def withdraw_ctf_for_liquidation(
        self,
        loan_id: int,
        current_price_e6: int,
    ) -> TxReceipt:
        """Step 1: vault → keeper. Pulls CTF out of the vault into the keeper EOA
        so the keeper can sell it on Polymarket. The vault marks
        `loan.withdrawn = true`."""
        self._require_account()
        self._require_price(current_price_e6)

        async def builder(nonce: int, tip: int) -> dict:
            overrides = await self._tx_overrides(nonce, tip)
            return await self.vault_contract.functions.withdrawCtfForLiquidation(
                int(loan_id), int(current_price_e6)
            ).build_transaction(overrides)

        return await self._send_with_replacement(
            builder, label=f"withdrawCtf loan={loan_id}"
        )

    async def settle_liquidation(
        self,
        loan_id: int,
        reason: str,
        actual_proceeds_e6: int,
    ) -> TxReceipt:
        """Step 4: vault distributes actualProceeds USDC to LP / treasury /
        borrower per V2 rules. Caller must have already transferred ≥
        `actual_proceeds_e6` USDC into the vault (step 3)."""
        self._require_account()
        if reason not in LIQUIDATE_REASONS:
            raise ValueError(f"reason must be one of {LIQUIDATE_REASONS}, got {reason!r}")
        if actual_proceeds_e6 < 0:
            raise ValueError("actualProceeds must be non-negative")

        async def builder(nonce: int, tip: int) -> dict:
            overrides = await self._tx_overrides(nonce, tip)
            return await self.vault_contract.functions.settleLiquidation(
                int(loan_id), reason, int(actual_proceeds_e6)
            ).build_transaction(overrides)

        return await self._send_with_replacement(
            builder, label=f"settle loan={loan_id} reason={reason}"
        )

    async def return_ctf_from_keeper(self, loan_id: int) -> TxReceipt:
        """Recovery path. Keeper still holds the CTF (sell failed, slippage
        etc.) and wants to roll back. Vault pulls the CTF back via ERC1155
        `safeTransferFrom`, so the keeper EOA must have done
        `setApprovalForAll(vault, true)` once at setup."""
        self._require_account()

        async def builder(nonce: int, tip: int) -> dict:
            overrides = await self._tx_overrides(nonce, tip)
            return await self.vault_contract.functions.returnCtfFromKeeper(
                int(loan_id)
            ).build_transaction(overrides)

        return await self._send_with_replacement(
            builder, label=f"returnCtf loan={loan_id}"
        )

    # ---------- public: USDC ----------

    async def transfer_usdc(self, to: str, amount_e6: int) -> TxReceipt:
        """Step 3: keeper → vault USDC transfer. Amount is in 6-decimal base
        units. The keeper EOA must hold ≥ amount_e6 USDC (proceeds from the
        Polymarket sell in step 2)."""
        self._require_account()
        if amount_e6 <= 0:
            raise ValueError("amount must be positive")

        async def builder(nonce: int, tip: int) -> dict:
            overrides = await self._tx_overrides(nonce, tip)
            return await self.usdc_contract.functions.transfer(
                AsyncWeb3.to_checksum_address(to), int(amount_e6)
            ).build_transaction(overrides)

        return await self._send_with_replacement(
            builder, label=f"usdc.transfer to={to} amount={amount_e6}"
        )

    # ---------- public: CTF (V2 deposit-wallet flow) ----------

    async def transfer_ctf_to_proxy(
        self, token_id: int, amount_e6: int, proxy_address: str,
    ) -> TxReceipt:
        """Step 1b: keeper EOA → keeper PM Safe proxy (CTF).

        Inserted between vault.withdrawCtfForLiquidation (step 1) and the CLOB
        sell (step 2), because PM CLOB V2 only accepts orders from the proxy.
        Plain ERC-1155 safeTransferFrom — no approval needed (the EOA is the
        token holder, calling on its own behalf).
        """
        self._require_account()
        if amount_e6 <= 0:
            raise ValueError("amount must be positive")

        async def builder(nonce: int, tip: int) -> dict:
            overrides = await self._tx_overrides(nonce, tip)
            return await self.ctf_contract.functions.safeTransferFrom(
                self.account.address,
                AsyncWeb3.to_checksum_address(proxy_address),
                int(token_id),
                int(amount_e6),
                b"",
            ).build_transaction(overrides)

        return await self._send_with_replacement(
            builder,
            label=f"ctf.safeTransferFrom keeper→proxy token={token_id}",
        )

    async def keeper_ctf_balance(self, token_id: int) -> int:
        """Read keeper EOA's balance of one CTF token. Used by tests + admin
        sanity checks; not on the hot liquidation path."""
        self._require_account()
        bal = await self.ctf_contract.functions.balanceOf(
            self.account.address, int(token_id),
        ).call()
        return int(bal)

    async def keeper_usdc_balance(self) -> int:
        """Read keeper EOA USDC balance (6-decimal base units). Used by the
        worker's monitoring tick — alerts when low."""
        self._require_account()
        bal = await self.usdc_contract.functions.balanceOf(
            self.account.address
        ).call()
        return int(bal)

    async def keeper_native_balance(self) -> int:
        """Read keeper EOA MATIC balance (wei). Worker monitors gas runway."""
        self._require_account()
        return int(await self.w3.eth.get_balance(self.account.address))

    async def lp_pool_balance(self) -> int:
        """Read the vault's `lpPoolBalance` (USDC.e 6-decimal base units).
        This is the contract's authoritative view of how much LP capital is
        free to lend — `openLoan` reverts with InsufficientLiquidity when
        principal exceeds it."""
        bal = await self.vault_contract.functions.lpPoolBalance().call()
        return int(bal)

    # ---------- internals ----------

    def _require_account(self) -> None:
        if self.account is None:
            raise KeeperKeyMissing("keeper_private_key is empty")

    @staticmethod
    def _require_price(current_price_e6: int) -> None:
        if not (0 < current_price_e6 <= 1_000_000):
            raise ValueError(
                f"currentPriceE6 must be in (0, 1_000_000], got {current_price_e6}"
            )

    async def _tx_overrides(self, nonce: int, tip_wei: int) -> dict:
        # Set BOTH EIP-1559 fee fields up-front. Without `maxFeePerGas`,
        # web3.py's auto-fill (via gas price strategy) sometimes returns 0 on
        # public Polygon RPCs, which the node then rejects with
        # "maxPriorityFeePerGas higher than maxFeePerGas". Using
        # `base_fee * 2 + tip` gives us ~1 block of headroom on top of the tip.
        latest = await self.w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas") or 0
        max_fee = base_fee * 2 + tip_wei
        return {
            "from": self.account.address,
            "nonce": nonce,
            "chainId": self.chain_id,
            "maxPriorityFeePerGas": tip_wei,
            "maxFeePerGas": max_fee,
        }

    async def _send_with_replacement(self, build_unsigned, *, label: str) -> TxReceipt:
        """Generic: build → sign → send → wait; on stuck-pending, bump tip
        with the same nonce, up to `max_retries`. Used for every state-changing
        tx the keeper sends."""
        nonce = await self.w3.eth.get_transaction_count(self.account.address)
        tip = self._priority_fee_wei

        for attempt in range(1, self._max_retries + 1):
            tx_hash = await self._sign_and_send(build_unsigned, nonce, tip)
            logger.info(
                "%s sent tip_gwei=%s nonce=%s tx=%s attempt=%s/%s",
                label, tip // 10**9, nonce, tx_hash, attempt, self._max_retries,
            )
            receipt = await self._wait_for_receipt(tx_hash, self._replacement_after_s)
            if receipt is not None:
                success = receipt.get("status") in ("0x1", 1, "0x01")
                if not success:
                    raise LiquidationFailed(f"{label} reverted tx={tx_hash}")
                block = receipt.get("blockNumber", 0)
                if isinstance(block, str):
                    block = int(block, 16)
                return TxReceipt(
                    tx_hash=tx_hash,
                    block_number=int(block),
                    success=True,
                    attempts=attempt,
                )
            tip *= 2
            logger.warning(
                "%s stuck nonce=%s; bumping tip to %s gwei",
                label, nonce, tip // 10**9,
            )

        raise LiquidationFailed(f"{label} never confirmed after {self._max_retries} attempts")

    async def _sign_and_send(self, build_unsigned, nonce: int, tip_wei: int) -> str:
        # Both fee fields are pre-set by `_tx_overrides` (which reads the
        # latest base fee). No post-build patch needed.
        unsigned = await build_unsigned(nonce, tip_wei)
        signed = self.account.sign_transaction(unsigned)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        sent = await self.w3.eth.send_raw_transaction(raw)
        return sent.hex() if isinstance(sent, bytes) else str(sent)

    async def _wait_for_receipt(self, tx_hash: str, timeout_s: int) -> dict | None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            try:
                receipt = await self.w3.eth.get_transaction_receipt(tx_hash)
            except Exception:
                receipt = None
            if receipt is not None:
                return dict(receipt) if not isinstance(receipt, dict) else receipt
            if asyncio.get_event_loop().time() >= deadline:
                return None
            await asyncio.sleep(2.0)


# ---------- module-level singleton ----------

# Polygon mainnet USDC. Same constant as the lending router. Hardcoded here
# rather than imported to keep this module standalone for tests.
POLYGON_USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

_vault_client: VaultClient | None = None


def get_vault_client() -> VaultClient:
    """Lazy module singleton built from `settings`. Tests inject via
    `set_vault_client(FakeVaultClient(...))`."""
    global _vault_client
    if _vault_client is None:
        from app.config import settings
        from app.services.lending.config import LENDING_VAULT_ADDRESS

        addr = settings.kpax_vault_address or LENDING_VAULT_ADDRESS
        _vault_client = VaultClient(
            rpc_url=settings.polygon_rpc_url,
            vault_address=addr,
            usdc_address=POLYGON_USDC_ADDRESS,
            chain_id=settings.lending_chain_id,
            private_key=settings.keeper_private_key or None,
            priority_fee_gwei=settings.keeper_priority_fee_gwei,
            replacement_after_s=settings.keeper_replacement_after_seconds,
            max_retries=settings.keeper_gas_max_retries,
        )
    return _vault_client


def set_vault_client(client) -> None:
    """Test seam: inject a fake."""
    global _vault_client
    _vault_client = client
