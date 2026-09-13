"""Polymarket CLOB integration — keeper sells CTF shares for USDC.

The keeper's 4-step liquidation flow (plan v0.3 §0):

  1. vault → keeper  (CTF withdraw)         — vault_client.withdraw_ctf_for_liquidation
  2. keeper → market (sell CTF here)        — THIS MODULE
  3. keeper → vault  (USDC transfer)        — vault_client (USDC ERC20)
  4. vault.settleLiquidation(reason, …)     — vault_client.settle_liquidation

Wraps `py-clob-client` so the rest of the worker stays Polymarket-agnostic.
Returns the *actual* USDC proceeds (6-decimal base units) so the keeper can
hand that exact number to `settleLiquidation` — the contract's resulting LP /
treasury / residual split is computed against actualProceeds, not the
keeper's pre-trade estimate.

Slippage: `min_proceeds_e6` lets the caller refuse fills below an expected
floor. The keeper sets this to `expectedProceeds × (1 − slippage_bps / 10_000)`
where `expectedProceeds` came from the on-chain price snapshot. If the market
won't fill at that floor, the seller returns `success=False` and the keeper
calls `returnCtfFromKeeper` to roll back the loan to active for retry.

Network failures vs market refusal: both surface as `success=False` with
distinct error messages — keeper treats them the same (rollback + retry next
tick) but Sentry tags differ for triage.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


_CONTENTS_TYPE_V2_ORDER = (
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,"
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
)
# Cache of wallet EIP-712 domains keyed by lowercased address. Populated by the
# SDK patch's build_signed_order via fetch_wallet_domain() — first POLY_1271
# order placed against a given maker triggers one RPC; subsequent orders hit
# the cache.
_wallet_domain_cache: dict = {}


def _install_proxy_signer_patch() -> None:
    """Patch py_clob_client_v2 so POLY_1271 orders use the format PM CLOB V2
    actually accepts.

    Two issues with the stock SDK:

    1. ``ExchangeOrderBuilderV2.build_order`` forces
       ``order.signer == self.signer.address()`` (the EOA) and raises if the
       caller asks for anything else. PM CLOB V2 wants
       ``signer = maker = funder`` (the smart-contract wallet itself) — see
       a captured PM Web UI sell where MetaMask is asked to sign typed data
       whose ``signer`` field equals the proxy address. Without this patch,
       PM rejects with ``"the order signer address has to be the address of
       the API KEY"`` (PM's API key is bound to the trading account, i.e.
       the proxy, not the signing EOA).

    2. ``ExchangeOrderBuilderV2.build_signed_order`` produces a 65-byte
       ECDSA signature over the order hash. PM CLOB V2 rejects that with
       ``"invalid signature"`` because POLY_1271 wallets (PM V2
       DepositWallet / Safe / EIP-7702 EOA) implement ERC-7739 'Defensive
       Rebinding' — they expect a wrapped signature whose ECDSA component
       signs a parent ``TypedDataSign`` struct anchored in the wallet's own
       EIP-712 domain (e.g. name='DepositWallet', version='1' for V2 DW).
       See ``erc7739.wrap_signature_erc7739`` for the wrapper layout.
    """
    from dataclasses import asdict

    from eth_account.messages import encode_typed_data
    from py_clob_client_v2.constants import BYTES32_ZERO
    from py_clob_client_v2.order_utils.exchange_order_builder_v2 import (
        ExchangeOrderBuilderV2,
    )
    from py_clob_client_v2.order_utils.model.order_data_v2 import (
        OrderV2,
        SignedOrderV2,
    )
    from py_clob_client_v2.order_utils.model.signature_type_v2 import (
        SignatureTypeV2,
    )

    from app.services.lending.erc7739 import (
        fetch_wallet_domain,
        wrap_signature_erc7739,
    )

    if getattr(ExchangeOrderBuilderV2, "_kpax_proxy_signer_patched", False):
        return

    import time as _time

    def build_order(self, order_data):  # type: ignore[no-redef]
        if int(order_data.signatureType) == int(SignatureTypeV2.POLY_1271):
            signer_addr = order_data.maker
        else:
            signer_addr = order_data.signer or order_data.maker
            if signer_addr != self.signer.address():
                raise ValueError("signer does not match")

        return OrderV2(
            salt=self.generate_salt(),
            maker=order_data.maker,
            signer=signer_addr,
            tokenId=order_data.tokenId,
            makerAmount=order_data.makerAmount,
            takerAmount=order_data.takerAmount,
            side=order_data.side,
            signatureType=(
                order_data.signatureType
                if order_data.signatureType is not None
                else SignatureTypeV2.EOA
            ),
            timestamp=(
                order_data.timestamp
                if order_data.timestamp
                else str(_time.time_ns() // 1_000_000)
            ),
            metadata=order_data.metadata if order_data.metadata else BYTES32_ZERO,
            builder=order_data.builder if order_data.builder else BYTES32_ZERO,
            expiration=order_data.expiration if order_data.expiration else "0",
        )

    def _wallet_domain_for(maker_addr: str):
        from app.config import settings

        key = maker_addr.lower()
        cached = _wallet_domain_cache.get(key)
        if cached is not None:
            return cached
        domain = fetch_wallet_domain(settings.polygon_rpc_url, maker_addr)
        _wallet_domain_cache[key] = domain
        return domain

    def build_signed_order(self, order_data):  # type: ignore[no-redef]
        order = self.build_order(order_data)
        typed_data = self.build_order_typed_data(order)

        if int(order_data.signatureType) == int(SignatureTypeV2.POLY_1271):
            encoded = encode_typed_data(full_message=typed_data)
            wallet_domain = _wallet_domain_for(order_data.maker)
            sig_bytes = wrap_signature_erc7739(
                private_key=self.signer.private_key,
                contents_hash=encoded.body,
                contents_name="Order",
                contents_type=_CONTENTS_TYPE_V2_ORDER,
                app_domain_separator=encoded.header,
                wallet_domain=wallet_domain,
            )
            signature = "0x" + sig_bytes.hex()
        else:
            signature = self.build_order_signature(typed_data)

        return SignedOrderV2(**{**asdict(order), "signature": signature})

    ExchangeOrderBuilderV2.build_order = build_order
    ExchangeOrderBuilderV2.build_signed_order = build_signed_order
    ExchangeOrderBuilderV2._kpax_proxy_signer_patched = True


_install_proxy_signer_patch()


@dataclass
class SellResult:
    success: bool
    # USDC base units (6 decimals). On success this is what the keeper must
    # transfer to the vault and pass to settleLiquidation.
    actual_proceeds_e6: int
    # Best-effort list of on-chain fill tx hashes. Empty on failure.
    fill_tx_hashes: list[str]
    error: str | None = None


class PolymarketSeller(Protocol):
    """Test seam — `keeper.py` depends on this Protocol, not the concrete
    `ClobSeller`. Tests inject a `FakePolymarketSeller`."""

    async def sell_ctf_market(
        self,
        token_id: str,
        shares: int,
        *,
        min_proceeds_e6: int = 0,
    ) -> SellResult: ...


class ClobSeller:
    """Real `py-clob-client` integration.

    The CLOB SDK is sync (requests-based), so each sell runs in
    `asyncio.to_thread` to avoid blocking the keeper's event loop.

    One-time setup the keeper EOA must have done before this works:
      1. `ctf.setApprovalForAll(POLYMARKET_CTF_EXCHANGE_V2, true)` — exchange
         pulls CTF when it fills our sell order. After Polymarket's CLOB V2
         migration (2026-04-28) the Exchange addresses changed; the keeper
         must approve **both** the new mainnet exchange (
         `0xE111180000d2663C0091e4f400237545B87B996B`) and the new neg-risk
         exchange (`0xe2222d279d744050d28e00520010520000310F59`). Older V1
         approvals are no-ops against V2.
      2. Polymarket API credentials. Either:
         - Pre-provisioned: set `POLYMARKET_API_KEY`/`..._SECRET`/
           `..._PASSPHRASE` env, OR
         - Auto-derived on first call: `client.create_or_derive_api_key()`
           runs once and we log the resulting creds — operator should copy
           them into env so subsequent worker restarts skip the derivation.

    Sell semantics (FOK = fill-or-kill):
      - We pre-flight `calculate_market_price` to estimate proceeds and bail
        if estimate < `min_proceeds_e6` (slippage protection).
      - On `post_order` success the FOK guarantees full fill at the quoted
        price; we trust the SDK's price quote and use `expected_proceeds_e6`
        as `actual_proceeds_e6`. If reality diverges (CLOB matched at a
        different price), `keeper.transfer_usdc` will revert with
        "insufficient balance" and the keeper's step-3 error path triggers
        admin alerting (CTF gone, USDC stuck).
        TODO: tighten by polling `keeper_usdc_balance` post-fill and using
        the actual delta. Adds latency + a vault_client dependency, deferred
        until we hit a real mismatch in production.
    """

    _BALANCE_POLL_TIMEOUT_S = 60
    _BALANCE_POLL_INTERVAL_S = 2.0
    # ERC20 balanceOf(address) selector
    _BALANCE_OF_SELECTOR = "70a08231"

    def __init__(
        self,
        host: str,
        chain_id: int,
        keeper_private_key: str,
        funder_address: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
    ) -> None:
        self.host = host
        self.chain_id = chain_id
        self.keeper_private_key = keeper_private_key
        # V2 deposit-wallet address — the Safe proxy that holds CTF + USDC.
        # Required: PM CLOB V2 rejects orders whose `funder` is the EOA
        # signer ("maker address not allowed, please use the deposit wallet
        # flow"). Must be deployed on-chain and approved for both V2
        # exchanges before placing orders.
        self.funder_address = funder_address
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self._client: Any = None  # ClobClient, lazy

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.keeper_private_key:
            raise RuntimeError("keeper_private_key is empty; cannot sign Polymarket orders")
        # Imports kept inside the method so bare imports don't pull web3 etc.
        # at module load; `polymarket_seller` is imported by the worker
        # boot path even when the seller isn't actually used (tests).
        # Using py_clob_client_v2 — Polymarket's CLOB V2 SDK. The V1 package
        # (py_clob_client) is hardcoded to EIP-712 domain version "1" and
        # every order it signs is rejected by V2 with `order_version_mismatch`
        # since the 2026-04-28 V2 cutover.
        from py_clob_client_v2 import ApiCreds, ClobClient, SignatureTypeV2

        if not self.funder_address:
            raise RuntimeError(
                "funder_address (keeper PM Safe proxy) not set — "
                "CLOB V2 rejects EOA-direct orders"
            )

        # POLY_1271: orders are signed by the EOA owner; PM verifies the
        # signature against the Safe contract via EIP-1271. `funder` is the
        # Safe proxy that actually holds CTF/USDC.
        common = dict(
            host=self.host,
            chain_id=self.chain_id,
            key=self.keeper_private_key,
            signature_type=SignatureTypeV2.POLY_1271,
            funder=self.funder_address,
        )

        if self.api_key and self.api_secret and self.api_passphrase:
            creds = ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase,
            )
            client = ClobClient(**common, creds=creds)
        else:
            # First-time bring-up: derive creds from the keeper signer.
            # Persist the printed creds into env to skip this on next boot.
            client = ClobClient(**common)
            derived = client.create_or_derive_api_key()
            client = ClobClient(**common, creds=derived)
            logger.warning(
                "Polymarket API creds derived on the fly. Copy these to env "
                "for next worker restart — POLYMARKET_API_KEY=%s "
                "POLYMARKET_API_SECRET=*** POLYMARKET_API_PASSPHRASE=***",
                derived.api_key,
            )
        self._client = client
        return client

    async def sell_ctf_market(
        self,
        token_id: str,
        shares: int,
        *,
        min_proceeds_e6: int = 0,
    ) -> SellResult:
        try:
            return await asyncio.to_thread(
                self._sync_sell, token_id, shares, min_proceeds_e6
            )
        except Exception as exc:
            logger.exception("polymarket sell crashed token=%s", token_id)
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=f"{type(exc).__name__}: {exc}",
            )

    def _sync_sell(
        self,
        token_id: str,
        shares: int,
        min_proceeds_e6: int,
    ) -> SellResult:
        from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

        client = self._ensure_client()

        # Step 1: estimate proceeds at current book depth (slippage guard).
        try:
            market_price: float = client.calculate_market_price(
                token_id, Side.SELL, float(shares), OrderType.FOK,
            )
        except Exception as exc:
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=f"calculate_market_price failed: {exc}",
            )
        expected_e6 = int(round(shares * market_price * 1_000_000))
        if expected_e6 < min_proceeds_e6:
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=(
                    f"slippage: estimated proceeds {expected_e6} e6 "
                    f"< min {min_proceeds_e6} e6 (price={market_price})"
                ),
            )

        # Step 2a: snapshot pre-trade pUSD balance on the funder (keeper
        # proxy). After post_order succeeds we poll until it increases —
        # the delta is the *actual* sale proceeds. Replaces the old
        # "trust FOK quote" assumption that was 1-3% high in practice
        # because of micro-slippage between calculate_market_price and
        # the CLOB V2 settlement fill, which made step-4 (relayer
        # transfer) revert with "ERC20: transfer amount exceeds balance".
        try:
            pre_balance_e6 = self._read_funder_pusd_balance()
        except Exception as exc:
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=f"pre-trade pUSD balance read failed: {exc}",
            )

        # Step 2b: build + sign + post FOK market sell. V2 SDK auto-detects
        # neg-risk markets via `get_neg_risk(token_id)` internally and routes
        # to the correct Exchange contract — no explicit flag needed.
        try:
            order = client.create_market_order(
                MarketOrderArgs(
                    token_id=token_id,
                    amount=float(shares),
                    side=Side.SELL,
                    price=market_price,
                    order_type=OrderType.FOK,
                )
            )
            resp: dict = client.post_order(order, order_type=OrderType.FOK)
        except Exception as exc:
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=f"post_order failed: {exc}",
            )

        # Step 3: parse response.
        if not resp.get("success"):
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=resp.get("errorMsg") or f"post_order rejected: {resp}",
            )

        # Step 4: poll the funder's pUSD balance until the CLOB V2
        # settlement tx mines and credits the proceeds. Slippage means the
        # actual delta is usually a few % below `expected_e6`; the floor
        # is `min_proceeds_e6` (already enforced by the caller).
        try:
            post_balance_e6 = self._wait_for_balance_increase(
                pre_balance_e6, timeout_s=self._BALANCE_POLL_TIMEOUT_S,
            )
        except TimeoutError as exc:
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=resp.get("transactionsHashes") or [],
                error=f"funder pUSD never increased after post_order: {exc}",
            )
        actual_e6 = post_balance_e6 - pre_balance_e6
        if actual_e6 < min_proceeds_e6:
            return SellResult(
                success=False,
                actual_proceeds_e6=actual_e6,
                fill_tx_hashes=resp.get("transactionsHashes") or [],
                error=(
                    f"actual proceeds {actual_e6} < min {min_proceeds_e6} "
                    f"(expected {expected_e6}, slippage on fill)"
                ),
            )
        return SellResult(
            success=True,
            actual_proceeds_e6=actual_e6,
            fill_tx_hashes=resp.get("transactionsHashes") or [],
        )

    # ---------- balance-tracking helpers ----------

    def _read_funder_pusd_balance(self) -> int:
        """Sync ``balanceOf`` against the keeper proxy via JSON-RPC.
        Used to compute the actual fill proceeds after a CLOB V2 sell."""
        import httpx

        from app.config import settings

        addr = self.funder_address.lower().removeprefix("0x").rjust(40, "0")
        data = "0x" + self._BALANCE_OF_SELECTOR + ("0" * 24) + addr
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [
                {"to": settings.polymarket_pusd_address, "data": data},
                "latest",
            ],
        }
        with httpx.Client(timeout=10) as c:
            r = c.post(settings.polygon_rpc_url, json=payload)
            r.raise_for_status()
            body = r.json()
        if "error" in body:
            raise RuntimeError(f"eth_call balanceOf failed: {body['error']}")
        return int(body["result"], 16)

    def _wait_for_balance_increase(
        self, pre_balance_e6: int, *, timeout_s: int,
    ) -> int:
        """Poll the funder's pUSD balance until it strictly exceeds
        ``pre_balance_e6``. Returns the new balance. Raises
        :class:`TimeoutError` if no increase observed within ``timeout_s``."""
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            cur = self._read_funder_pusd_balance()
            if cur > pre_balance_e6:
                return cur
            time.sleep(self._BALANCE_POLL_INTERVAL_S)
        raise TimeoutError(
            f"funder pUSD balance unchanged for {timeout_s}s "
            f"(stayed at {pre_balance_e6})"
        )


class FakePolymarketSeller:
    """Test / dev double. Captures calls and returns whatever proceeds the
    test scenario asks for. The keeper's 4-step flow doesn't care which
    implementation it gets, as long as the shape matches the Protocol."""

    def __init__(
        self,
        *,
        proceeds_e6: int = 0,
        succeed: bool = True,
        error: str | None = None,
    ) -> None:
        self.proceeds_e6 = proceeds_e6
        self.succeed = succeed
        self.error = error
        self.calls: list[dict] = []

    async def sell_ctf_market(
        self,
        token_id: str,
        shares: int,
        *,
        min_proceeds_e6: int = 0,
    ) -> SellResult:
        self.calls.append(
            {
                "token_id": token_id,
                "shares": shares,
                "min_proceeds_e6": min_proceeds_e6,
            }
        )
        if not self.succeed:
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=self.error or "fake failure",
            )
        if self.proceeds_e6 < min_proceeds_e6:
            return SellResult(
                success=False,
                actual_proceeds_e6=0,
                fill_tx_hashes=[],
                error=f"slippage: {self.proceeds_e6} < {min_proceeds_e6}",
            )
        return SellResult(
            success=True,
            actual_proceeds_e6=self.proceeds_e6,
            fill_tx_hashes=[f"0xfake-fill-{token_id}"],
        )


# ---------- module singleton ----------

_seller: PolymarketSeller | None = None


def get_polymarket_seller() -> PolymarketSeller:
    """Lazy module singleton. The keeper grabs this once at startup. In tests
    `set_polymarket_seller(FakePolymarketSeller(...))` overrides."""
    global _seller
    if _seller is None:
        from app.config import settings

        _seller = ClobSeller(
            host=settings.polymarket_clob_url,
            chain_id=settings.lending_chain_id,
            keeper_private_key=settings.keeper_private_key,
            funder_address=settings.keeper_proxy_address,
            api_key=settings.polymarket_api_key or None,
            api_secret=settings.polymarket_api_secret or None,
            api_passphrase=settings.polymarket_api_passphrase or None,
        )
    return _seller


def set_polymarket_seller(seller: PolymarketSeller) -> None:
    """Test seam: inject a fake."""
    global _seller
    _seller = seller
