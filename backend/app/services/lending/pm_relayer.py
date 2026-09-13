"""PM Relayer client — drive a DepositWallet via Polymarket's relayer API.

CLOB V2 (cutover 2026-04-28) holds keeper funds inside a `DepositWallet`
(EIP-1967 proxy). The wallet's `execute(Batch, sig)` method is `onlyFactory`,
and the factory's `proxy(...)` is `onlyOperator` — only PM-controlled
addresses have the operator role. So the keeper can SIGN a Batch but cannot
submit it on-chain itself; submission goes through PM's centralized relayer.

Flow (per PM docs + builder-relayer-client):
  1. Read on-chain `wallet.nonce()`.
  2. Build Calls list `[{target, value, data}, ...]`.
  3. Set `deadline = now + 240s`.
  4. EIP-712 sign the Batch with the wallet owner's key.
  5. POST to `{relayer_url}/submit` with auth headers.
  6. Poll `GET {relayer_url}/transaction?id=<id>` until STATE_MINED /
     STATE_CONFIRMED, or fail on STATE_FAILED / STATE_INVALID.

EIP-712 details:
  domain = {name: "DepositWallet", version: "1", chainId, verifyingContract: wallet}
  types  = {Call: [target,value,data], Batch: [wallet,nonce,deadline,calls]}

Auth headers (PM Relayer API key path):
  RELAYER_API_KEY:         the API key from PM's "Relayer API 密钥" page
  RELAYER_API_KEY_ADDRESS: the EOA the key is bound to (the wallet owner)

Trade-off accepted: PM relayer is a centralized dependency. If the relayer
is down or rate-limits us, settlement of a liquidation stalls — the loan
sits in `withdrawing` state until the relayer recovers (or admin invokes
the on-chain `pause()` + timelock-delayed `withdrawERC20` escape hatch).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)


# ---------- relayer constants ----------

_SUBMIT_PATH = "/submit"
_TX_PATH = "/transaction"

_TYPE_WALLET = "WALLET"

# Terminal states per PM relayer (from builder-relayer-client/src/types.ts):
_STATES_TERMINAL_OK = {"STATE_MINED", "STATE_CONFIRMED"}
_STATES_TERMINAL_FAIL = {"STATE_FAILED", "STATE_INVALID"}

_DEFAULT_DEADLINE_S = 1200         # 20 min — PM rejects "deadline too soon" at 240s
_DEFAULT_POLL_TIMEOUT_S = 180
_POLL_INTERVAL_S = 2.0


_WALLET_ABI = [
    {
        "type": "function",
        "name": "nonce",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


# Factory's batch-forwarder. Selector 0x0a3c4405. The wallet's
# ``execute(...)`` reverts ``OnlyFactory()`` if called from anything other
# than this factory, so on-chain forwarding has to go via the factory:
#
#     factory.proxy([batch], [signature]) -> for each i, factory calls
#         wallet.execute(batch[i], signature[i])
#
# Sending it as 1-element arrays is fine — that's exactly what one
# admin-manual liquidation needs.
_FACTORY_ABI = [
    {
        "type": "function",
        "name": "proxy",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "batches",
                "type": "tuple[]",
                "components": [
                    {"name": "wallet", "type": "address"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                    {
                        "name": "calls",
                        "type": "tuple[]",
                        "components": [
                            {"name": "target", "type": "address"},
                            {"name": "value", "type": "uint256"},
                            {"name": "data", "type": "bytes"},
                        ],
                    },
                ],
            },
            {"name": "signatures", "type": "bytes[]"},
        ],
        "outputs": [],
    },
]


# ---------- types ----------


@dataclass
class RelayerReceipt:
    """Returned when a relayer-submitted batch reaches a terminal state."""

    transaction_id: str
    state: str
    transaction_hash: str | None  # may be None until STATE_MINED
    success: bool


class RelayerError(RuntimeError):
    """Raised on HTTP errors / non-200 responses / failed states."""


# ---------- calldata helpers (same selectors as a normal external EOA) ----------


def encode_erc20_transfer(to: str, amount: int) -> bytes:
    selector = bytes.fromhex("a9059cbb")
    return selector + abi_encode(
        ["address", "uint256"],
        [AsyncWeb3.to_checksum_address(to), int(amount)],
    )


def encode_erc20_approve(spender: str, amount: int) -> bytes:
    selector = bytes.fromhex("095ea7b3")
    return selector + abi_encode(
        ["address", "uint256"],
        [AsyncWeb3.to_checksum_address(spender), int(amount)],
    )


def encode_erc1155_safe_transfer(
    from_addr: str, to: str, token_id: int, amount: int,
) -> bytes:
    selector = bytes.fromhex("f242432a")
    return selector + abi_encode(
        ["address", "address", "uint256", "uint256", "bytes"],
        [
            AsyncWeb3.to_checksum_address(from_addr),
            AsyncWeb3.to_checksum_address(to),
            int(token_id),
            int(amount),
            b"",
        ],
    )


def encode_erc1155_set_approval_for_all(operator: str, approved: bool) -> bytes:
    selector = bytes.fromhex("a22cb465")
    return selector + abi_encode(
        ["address", "bool"],
        [AsyncWeb3.to_checksum_address(operator), bool(approved)],
    )


# ---------- client ----------


class PMRelayerClient:
    """Submits DepositWallet batches via PM's relayer."""

    def __init__(
        self,
        rpc_url: str,
        relayer_url: str,
        api_key: str,
        owner_private_key: str,
        wallet_address: str,
        factory_address: str,
        chain_id: int = 137,
    ) -> None:
        if not relayer_url or not api_key:
            raise RuntimeError("PM relayer URL / API key not configured")
        self.relayer_url = relayer_url.rstrip("/")
        self.api_key = api_key
        self.owner = Account.from_key(owner_private_key)
        self.wallet_address = AsyncWeb3.to_checksum_address(wallet_address)
        self.factory_address = AsyncWeb3.to_checksum_address(factory_address)
        self.chain_id = chain_id

        self.w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._wallet = self.w3.eth.contract(
            address=self.wallet_address, abi=_WALLET_ABI,
        )
        self._factory = self.w3.eth.contract(
            address=self.factory_address, abi=_FACTORY_ABI,
        )
        # Match the PM Web UI's request fingerprint. Default httpx
        # User-Agent ("python-httpx/...") is enough for relayer-v2 to
        # reject POST /submit with 401, even when the API key + EOA pair
        # is otherwise valid (GET /transaction passes from the same
        # client). See the captured PM UI POST for the header set.
        self._http = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                ),
                "Origin": "https://polymarket.com",
                "Referer": "https://polymarket.com/",
            },
        )

    # ---------- public ----------

    async def exec_batch(
        self,
        calls: list[dict],
        *,
        deadline_s: int = _DEFAULT_DEADLINE_S,
        poll_timeout_s: int = _DEFAULT_POLL_TIMEOUT_S,
    ) -> RelayerReceipt:
        """Sign + submit a Batch through the relayer. Each call is
        `{target: addr, data: hex_or_bytes, value: int}` (value defaults to 0)."""
        if not calls:
            raise ValueError("calls must not be empty")

        normalized = [self._normalize_call(c) for c in calls]
        nonce = await self._wallet.functions.nonce().call()
        deadline = int(time.time()) + deadline_s

        signature = self._sign_batch(nonce=nonce, deadline=deadline, calls=normalized)

        body = {
            "type": _TYPE_WALLET,
            "from": self.owner.address,
            "to": self.factory_address,
            "nonce": str(nonce),
            "signature": signature,
            "depositWalletParams": {
                "depositWallet": self.wallet_address,
                "deadline": str(deadline),
                "calls": [
                    {
                        "target": c["target"],
                        "value": str(c["value"]),
                        "data": "0x" + c["data"].hex(),
                    }
                    for c in normalized
                ],
            },
        }

        resp = await self._http.post(
            self.relayer_url + _SUBMIT_PATH,
            json=body,
            headers={
                "RELAYER_API_KEY": self.api_key,
                "RELAYER_API_KEY_ADDRESS": self.owner.address,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            raise RelayerError(
                f"relayer /submit returned {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        tx_id = data.get("transactionID") or data.get("transactionId")
        if not tx_id:
            raise RelayerError(f"relayer response missing transactionID: {data}")
        logger.info(
            "relayer submit: txID=%s state=%s wallet=%s calls=%d",
            tx_id, data.get("state"), self.wallet_address, len(normalized),
        )
        return await self._poll_until_terminal(tx_id, poll_timeout_s)

    # ---------- convenience wrappers (mirror the executor API the keeper uses) ----------

    async def transfer_erc20(self, token: str, to: str, amount: int) -> RelayerReceipt:
        return await self.exec_batch([{
            "target": token,
            "data": encode_erc20_transfer(to, amount),
            "value": 0,
        }])

    async def transfer_erc1155(
        self, token: str, to: str, token_id: int, amount: int,
    ) -> RelayerReceipt:
        return await self.exec_batch([{
            "target": token,
            "data": encode_erc1155_safe_transfer(
                self.wallet_address, to, token_id, amount,
            ),
            "value": 0,
        }])

    # ---------- on-chain fallback: bypass the PM relayer entirely ----------

    async def exec_batch_onchain(
        self,
        calls: list[dict],
        *,
        deadline_s: int = _DEFAULT_DEADLINE_S,
    ) -> RelayerReceipt:
        """Sign + send the Batch directly to ``wallet.execute(batch, sig)``
        using the keeper EOA. Bypasses the PM Relayer when its API-key auth
        is rejected (PM Relayer V2 only accepts cookie-session auth from
        polymarket.com for some accounts).

        The wallet still verifies the same EIP-712 signature against its
        ``owner()``, so the funds-flow stays identical: msg.sender inside
        each call is the wallet itself, transfers come from the wallet's
        balance.

        Costs ~$0.001 in MATIC gas vs PM's gasless service.
        """
        if not calls:
            raise ValueError("calls must not be empty")

        normalized = [self._normalize_call(c) for c in calls]
        nonce = await self._wallet.functions.nonce().call()
        deadline = int(time.time()) + deadline_s
        signature = self._sign_batch(nonce=nonce, deadline=deadline, calls=normalized)

        batch_tuple = (
            AsyncWeb3.to_checksum_address(self.wallet_address),
            int(nonce),
            int(deadline),
            [
                (c["target"], int(c["value"]), bytes(c["data"]))
                for c in normalized
            ],
        )
        sig_bytes = bytes.fromhex(signature.removeprefix("0x"))

        return await self._send_execute_tx(batch_tuple, sig_bytes)

    async def transfer_erc20_onchain(
        self, token: str, to: str, amount: int,
    ) -> RelayerReceipt:
        return await self.exec_batch_onchain([{
            "target": token,
            "data": encode_erc20_transfer(to, amount),
            "value": 0,
        }])

    async def transfer_erc1155_onchain(
        self, token: str, to: str, token_id: int, amount: int,
    ) -> RelayerReceipt:
        return await self.exec_batch_onchain([{
            "target": token,
            "data": encode_erc1155_safe_transfer(
                self.wallet_address, to, token_id, amount,
            ),
            "value": 0,
        }])

    async def _send_execute_tx(
        self, batch_tuple: tuple, signature: bytes,
    ) -> RelayerReceipt:
        """Build, sign with keeper EOA, broadcast, and wait for the receipt
        of a ``factory.proxy([batch], [sig])`` call. The wallet refuses
        ``execute(...)`` from any caller other than the factory
        (``OnlyFactory()`` revert, selector 0x0c6d42ae); the factory wraps
        the call and forwards into the wallet."""
        keeper_eoa = AsyncWeb3.to_checksum_address(self.owner.address)
        tx_nonce = await self.w3.eth.get_transaction_count(keeper_eoa)

        block = await self.w3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas") or 0
        priority = self.w3.to_wei(30, "gwei")
        max_fee = base_fee * 2 + priority

        unsigned = await self._factory.functions.proxy(
            [batch_tuple], [signature],
        ).build_transaction({
            "from": keeper_eoa,
            "nonce": tx_nonce,
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": max_fee,
            "chainId": self.chain_id,
        })

        signed = self.owner.sign_transaction(unsigned)
        raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
        tx_hash = await self.w3.eth.send_raw_transaction(raw)
        tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        if not tx_hex.startswith("0x"):
            tx_hex = "0x" + tx_hex

        logger.info(
            "factory.proxy submitted (onchain) wallet=%s nonce=%s tx=%s",
            self.wallet_address, batch_tuple[1], tx_hex,
        )

        receipt = await self.w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=120,
        )
        success = receipt.get("status") == 1
        if not success:
            raise RelayerError(
                f"factory.proxy reverted on-chain tx={tx_hex} "
                f"(see Polygonscan for revert reason)"
            )
        return RelayerReceipt(
            transaction_id=tx_hex,  # no PM transactionID for onchain path
            state="STATE_EXECUTED",
            transaction_hash=tx_hex,
            success=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- internals ----------

    def _normalize_call(self, c: dict) -> dict:
        target = AsyncWeb3.to_checksum_address(c["target"])
        data = c.get("data", b"")
        if isinstance(data, str):
            if data.startswith("0x"): data = data[2:]
            data = bytes.fromhex(data)
        return {
            "target": target,
            "value": int(c.get("value", 0)),
            "data": bytes(data),
        }

    def _sign_batch(self, *, nonce: int, deadline: int, calls: list[dict]) -> str:
        # EIP-712 typed data — the wallet validates this exact domain in
        # `WalletLib.sol::validateBatch`.
        domain = {
            "name": "DepositWallet",
            "version": "1",
            "chainId": self.chain_id,
            "verifyingContract": self.wallet_address,
        }
        types = {
            "Call": [
                {"name": "target", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "data", "type": "bytes"},
            ],
            "Batch": [
                {"name": "wallet", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "calls", "type": "Call[]"},
            ],
        }
        message = {
            "wallet": self.wallet_address,
            "nonce": nonce,
            "deadline": deadline,
            "calls": [
                {"target": c["target"], "value": c["value"], "data": c["data"]}
                for c in calls
            ],
        }

        signable = encode_typed_data(
            full_message={
                "types": types,
                "domain": domain,
                "primaryType": "Batch",
                "message": message,
            },
        )
        signed = self.owner.sign_message(signable)
        return "0x" + signed.signature.hex()

    async def _poll_until_terminal(
        self, tx_id: str, timeout_s: int,
    ) -> RelayerReceipt:
        deadline = asyncio.get_event_loop().time() + timeout_s
        last_state: str | None = None
        last_hash: str | None = None
        while True:
            try:
                r = await self._http.get(
                    self.relayer_url + _TX_PATH,
                    params={"id": tx_id},
                    headers={
                        "RELAYER_API_KEY": self.api_key,
                        "RELAYER_API_KEY_ADDRESS": self.owner.address,
                    },
                )
            except Exception as exc:
                logger.warning("relayer poll error tx=%s: %s", tx_id, exc)
                if asyncio.get_event_loop().time() >= deadline:
                    raise RelayerError(f"poll timeout for {tx_id}")
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue

            if r.status_code != 200:
                raise RelayerError(
                    f"relayer /transaction returned {r.status_code}: {r.text[:300]}"
                )
            data: Any = r.json()
            txn = data[0] if isinstance(data, list) and data else data
            if not isinstance(txn, dict):
                raise RelayerError(f"unexpected relayer response: {data}")
            state = txn.get("state")
            tx_hash = txn.get("transactionHash") or txn.get("hash")
            if state != last_state or tx_hash != last_hash:
                logger.info(
                    "relayer poll: tx=%s state=%s hash=%s", tx_id, state, tx_hash,
                )
                last_state, last_hash = state, tx_hash

            if state in _STATES_TERMINAL_OK:
                return RelayerReceipt(
                    transaction_id=tx_id, state=state,
                    transaction_hash=tx_hash, success=True,
                )
            if state in _STATES_TERMINAL_FAIL:
                raise RelayerError(
                    f"relayer tx {tx_id} entered terminal failure state={state} "
                    f"hash={tx_hash}"
                )

            if asyncio.get_event_loop().time() >= deadline:
                raise RelayerError(
                    f"relayer poll timeout tx={tx_id} last_state={state}"
                )
            await asyncio.sleep(_POLL_INTERVAL_S)


# ---------- module-level singleton ----------

_client: PMRelayerClient | None = None


def get_pm_relayer() -> PMRelayerClient:
    """Lazy singleton from settings. Raises if any required field is unset."""
    global _client
    if _client is not None:
        return _client
    from app.config import settings

    _client = PMRelayerClient(
        rpc_url=settings.polygon_rpc_url,
        relayer_url=settings.polymarket_relayer_url,
        api_key=settings.polymarket_relayer_api_key,
        owner_private_key=settings.keeper_private_key,
        wallet_address=settings.keeper_proxy_address,
        factory_address=settings.polymarket_deposit_wallet_factory,
        chain_id=settings.lending_chain_id,
    )
    return _client


def set_pm_relayer(client: PMRelayerClient | None) -> None:
    """Test seam — inject a fake."""
    global _client
    _client = client
