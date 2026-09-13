import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import Base, engine
from app.routers import admin, analysis, auth, graph, lending, market, verification

# Import all models so they register with Base.metadata
from app.models.analysis import Analysis  # noqa: F401
from app.models.indexer_cursor import IndexerCursor  # noqa: F401
from app.models.lending_alert import LendingAlert  # noqa: F401
from app.models.lending_event import LendingEvent  # noqa: F401
from app.models.lending_tos import TosAcceptance  # noqa: F401
from app.models.loan import Loan  # noqa: F401
from app.models.market import Market, MarketSnapshot  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.verification import MatchResult, VerificationRecord  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (dev convenience; production uses Alembic)
    Base.metadata.create_all(bind=engine)

    # Spawn the lending event indexer as a background task. It tails
    # LendingVault events (LoanOpened/Repaid/Liquidated/...) and writes
    # them to lending_events + transitions Loan rows to terminal states
    # ("liquidated_ltv", "liquidated_kickoff", "repaid"). Without this
    # task running, on-chain settles stay stuck in DB at "liquidating".
    import asyncio

    from app.services.lending.event_indexer import event_indexer_loop

    indexer_task = asyncio.create_task(
        event_indexer_loop(), name="lending_event_indexer",
    )
    try:
        yield
    finally:
        indexer_task.cancel()
        try:
            await indexer_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="KPAX Ball API",
    description="Polymarket football analysis engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(market.router)
app.include_router(analysis.router)
app.include_router(verification.router)
app.include_router(graph.router)
app.include_router(lending.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/privacy")
async def privacy_policy():
    return FileResponse("../docs/privacy-policy.html", media_type="text/html")


# Privy-backed auth page (built from /auth-page).
# When the hosted auth bundle has been built, expose it at /auth/. Otherwise
# skip the mount so /auth returns 404 instead of a server startup error.
_AUTH_PAGE_DIST = Path(__file__).resolve().parent.parent.parent / "auth-page" / "dist"
if _AUTH_PAGE_DIST.exists():
    app.mount(
        "/auth",
        StaticFiles(directory=str(_AUTH_PAGE_DIST), html=True),
        name="auth",
    )
else:
    logging.getLogger(__name__).warning(
        "auth-page/dist not found at %s — /auth will 404 until the auth page "
        "is built (cd auth-page && npm run build).",
        _AUTH_PAGE_DIST,
    )


# Admin dashboard (single-file static page checked into backend/admin-page/).
# Serves at /admin — gated client-side by KPAX JWT + server-side by
# `require_admin` on /api/admin/* endpoints (so a leaked HTML link does
# nothing for non-admin wallets).
_ADMIN_PAGE_DIR = Path(__file__).resolve().parent.parent / "admin-page"
if _ADMIN_PAGE_DIR.exists():
    app.mount(
        "/admin",
        StaticFiles(directory=str(_ADMIN_PAGE_DIR), html=True),
        name="admin",
    )
else:
    logging.getLogger(__name__).warning(
        "admin-page/ not found at %s — /admin will 404.",
        _ADMIN_PAGE_DIR,
    )
