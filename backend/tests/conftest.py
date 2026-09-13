"""Shared fixtures for the lending worker test suite.

Each test gets a fresh in-memory SQLite engine. The conftest also wires
`AsyncSessionLocal` references in the worker modules to point at the test
engine so `_tick()` etc. picks up our session factory rather than the
production one. `price_watcher.get_current_price` and the vault client
singleton are similarly patched.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import all models so they register on Base.metadata before create_all.
from app.db import Base
from app.models.analysis import Analysis  # noqa: F401
from app.models.lending_alert import LendingAlert  # noqa: F401
from app.models.lending_event import LendingEvent  # noqa: F401
from app.models.lending_tos import TosAcceptance  # noqa: F401
from app.models.loan import Loan
from app.models.market import Market, MarketSnapshot  # noqa: F401
from app.models.user import User
from app.models.verification import MatchResult, VerificationRecord  # noqa: F401


# ---------- engine + session ----------


@pytest_asyncio.fixture
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def async_session_factory(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(async_session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as s:
        yield s


@pytest.fixture
def patch_session_factory(monkeypatch, async_session_factory):
    """Point all module-level AsyncSessionLocal references at the test factory.
    Each module that imports `from app.db import AsyncSessionLocal` keeps its
    own name binding, so we patch them individually."""
    import app.db as db_mod
    import app.services.lending.alert_engine as ae
    import app.services.lending.event_indexer as ei
    import app.services.lending.keeper as kp

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_session_factory)
    monkeypatch.setattr(ei, "AsyncSessionLocal", async_session_factory)
    monkeypatch.setattr(kp, "AsyncSessionLocal", async_session_factory)
    monkeypatch.setattr(ae, "AsyncSessionLocal", async_session_factory)
    return async_session_factory


# ---------- seed helpers ----------


@pytest_asyncio.fixture
async def user(session) -> User:
    u = User(
        privy_user_id="did:privy:test",
        wallet_address="0x" + "ca" * 20,
        email=None,
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


@pytest.fixture
def seed_loan(user):
    """Returns an async callable producing a Loan row with sane defaults.
    Overrides as kwargs."""
    counter = {"n": 0}

    async def _seed(session: AsyncSession, **overrides) -> Loan:
        counter["n"] += 1
        n = counter["n"]
        kickoff = overrides.pop(
            "match_kickoff_at", datetime.utcnow() + timedelta(days=7)
        )
        loan = Loan(
            user_id=user.id,
            wallet_address=user.wallet_address,
            # Numeric string default — step 2 of the keeper does int(token_id),
            # so non-numeric placeholders break the V3 5-step flow.
            ctf_token_id=overrides.pop("ctf_token_id", str(1_000_000_000 + n)),
            market_slug=overrides.pop("market_slug", f"slug-{n}"),
            collateral_shares=Decimal(str(overrides.pop("collateral_shares", 1000))),
            collateral_value_at_open=Decimal(
                str(overrides.pop("collateral_value_at_open", 800))
            ),
            entry_price=Decimal(str(overrides.pop("entry_price", "0.8"))),
            principal=Decimal(str(overrides.pop("principal", 400))),
            apr_bps=overrides.pop("apr_bps", 1200),
            league_tier=overrides.pop("league_tier", 1),
            opened_ltv=Decimal(str(overrides.pop("opened_ltv", "0.5"))),
            status=overrides.pop("status", "active"),
            onchain_loan_id=overrides.pop("onchain_loan_id", n),
            open_tx_hash=overrides.pop("open_tx_hash", f"0xtx{n}"),
            match_kickoff_at=kickoff,
        )
        for k, v in overrides.items():
            setattr(loan, k, v)
        session.add(loan)
        await session.commit()
        await session.refresh(loan)
        return loan

    return _seed


# ---------- vault client fake (V2 — 4-step) ----------


class FakeTxReceipt:
    def __init__(self, tx_hash: str, attempts: int = 1, block_number: int = 999):
        self.tx_hash = tx_hash
        self.attempts = attempts
        self.block_number = block_number
        self.success = True


class FakeVaultClient:
    """V2 4-step liquidation fake. Records calls per method on `.calls`
    (chronological list of dicts) so tests can assert the keeper hit each
    step in the right order with the right args.

    Independent failure flags per step let tests model partial-failure modes:
        FakeVaultClient(fail_step="withdraw")
        FakeVaultClient(fail_step="settle")
    """

    def __init__(
        self,
        *,
        no_key: bool = False,
        fail_step: str | None = None,  # one of: withdraw / settle / return / transfer_usdc
    ):
        self.calls: list[dict] = []
        self.no_key = no_key
        self.fail_step = fail_step

    def _maybe_fail(self, step: str) -> None:
        if self.no_key:
            from app.services.lending.vault_client import KeeperKeyMissing

            raise KeeperKeyMissing("fake")
        if self.fail_step == step:
            from app.services.lending.vault_client import LiquidationFailed

            raise LiquidationFailed(f"fake {step} fail")

    async def withdraw_ctf_for_liquidation(self, loan_id: int, current_price_e6: int):
        self.calls.append(
            {"step": "withdraw", "loan_id": loan_id, "current_price_e6": current_price_e6}
        )
        self._maybe_fail("withdraw")
        return FakeTxReceipt(tx_hash=f"0xfake-withdraw-{loan_id}")

    async def settle_liquidation(self, loan_id: int, reason: str, actual_proceeds_e6: int):
        self.calls.append(
            {
                "step": "settle",
                "loan_id": loan_id,
                "reason": reason,
                "actual_proceeds_e6": actual_proceeds_e6,
            }
        )
        self._maybe_fail("settle")
        return FakeTxReceipt(tx_hash=f"0xfake-settle-{loan_id}-{reason}")

    async def return_ctf_from_keeper(self, loan_id: int):
        self.calls.append({"step": "return", "loan_id": loan_id})
        self._maybe_fail("return")
        return FakeTxReceipt(tx_hash=f"0xfake-return-{loan_id}")

    async def transfer_usdc(self, to: str, amount_e6: int):
        self.calls.append({"step": "transfer_usdc", "to": to, "amount_e6": amount_e6})
        self._maybe_fail("transfer_usdc")
        return FakeTxReceipt(tx_hash=f"0xfake-usdc-{amount_e6}")

    async def transfer_ctf_to_proxy(
        self, *, token_id: int, amount_e6: int, proxy_address: str
    ):
        self.calls.append(
            {
                "step": "ctf_to_proxy",
                "token_id": token_id,
                "amount_e6": amount_e6,
                "proxy_address": proxy_address,
            }
        )
        self._maybe_fail("ctf_to_proxy")
        return FakeTxReceipt(tx_hash=f"0xfake-ctf-{amount_e6}")

    @property
    def account(self):
        # vault_client uses .account.address for EOA references on rollback paths.
        class _Acct:
            address = "0x" + "ee" * 20
        return _Acct()


@pytest.fixture
def fake_vault(monkeypatch):
    fake = FakeVaultClient()
    from app.services.lending import vault_client

    monkeypatch.setattr(vault_client, "_vault_client", fake)
    return fake


# ---------- PM Relayer fake (V3 — step 4 + rollback) ----------


class _FakeRelayerReceipt:
    def __init__(self, tx_hash: str):
        self.transaction_id = f"id-{tx_hash}"
        self.state = "STATE_MINED"
        self.transaction_hash = tx_hash
        self.success = True


class FakePMRelayer:
    """Minimal stand-in for `PMRelayerClient`. Records every transfer so
    tests can assert keeper hit step 4 (pUSD proxy → vault) with the right
    args, and also covers the rollback path (CTF proxy → keeper EOA)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_step: str | None = None

    async def transfer_erc20(self, token: str, to: str, amount: int):
        self.calls.append(
            {"step": "relayer_erc20", "token": token, "to": to, "amount": amount}
        )
        if self.fail_step == "relayer_erc20":
            raise RuntimeError("fake relayer erc20 fail")
        return _FakeRelayerReceipt(tx_hash=f"0xfake-relayer-{amount}")

    async def transfer_erc1155(
        self, token: str, to: str, token_id: int, amount: int
    ):
        self.calls.append(
            {
                "step": "relayer_erc1155",
                "token": token,
                "to": to,
                "token_id": token_id,
                "amount": amount,
            }
        )
        if self.fail_step == "relayer_erc1155":
            raise RuntimeError("fake relayer erc1155 fail")
        return _FakeRelayerReceipt(tx_hash=f"0xfake-relayer-1155-{amount}")

    # On-chain variants (keeper EOA pays gas, calls wallet.execute directly).
    # The keeper now uses these by default — PM Relayer V2 rejects API-key
    # auth, so we bypass it. Tests treat them the same as the relayer-routed
    # versions: same args, same behaviour, same failure injection.
    async def transfer_erc20_onchain(self, token: str, to: str, amount: int):
        return await self.transfer_erc20(token, to, amount)

    async def transfer_erc1155_onchain(
        self, token: str, to: str, token_id: int, amount: int,
    ):
        return await self.transfer_erc1155(token, to, token_id, amount)


@pytest.fixture
def fake_relayer(monkeypatch):
    fake = FakePMRelayer()
    from app.services.lending import pm_relayer

    monkeypatch.setattr(pm_relayer, "_client", fake)
    return fake


# ---------- polymarket seller fake ----------


@pytest.fixture
def fake_polymarket(monkeypatch):
    """Default: seller succeeds and returns proceeds = min_proceeds_e6
    (i.e. exactly at the slippage floor) plus a 1% buffer. Tests can replace
    by re-injecting via `set_polymarket_seller()` for failure modes."""
    from app.services.lending import polymarket_seller as ps_mod

    seller = ps_mod.FakePolymarketSeller(succeed=True, proceeds_e6=10**18)
    monkeypatch.setattr(ps_mod, "_seller", seller)
    return seller


# ---------- price fake ----------


class _PriceHandle:
    def __init__(self):
        self._value: float | None = 0.5

    def set(self, v: float | None):
        self._value = v

    @property
    def value(self):
        return self._value


@pytest.fixture
def fake_price(monkeypatch) -> _PriceHandle:
    """Patch get_current_price across all modules that import it. Default 0.5;
    override via fake_price.set(0.x) inside the test."""
    handle = _PriceHandle()

    async def fn(_token_id, _slug):
        return handle.value

    import app.services.lending.alert_engine as ae
    import app.services.lending.keeper as kp
    import app.services.lending.price_watcher as pw

    monkeypatch.setattr(pw, "get_current_price", fn)
    monkeypatch.setattr(kp, "get_current_price", fn)
    monkeypatch.setattr(ae, "get_current_price", fn)
    return handle
