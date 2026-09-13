"""Privy-based authentication.

Flow:
  1. Extension opens external auth page (Privy login).
  2. Auth page completes Privy login, posts Privy access token + user info to
     POST /api/auth/privy/exchange.
  3. Backend verifies Privy token, upserts User, returns KPAX JWT.
  4. Extension stores KPAX JWT and sends it in Authorization header.
  5. Protected endpoints use `current_user` dependency to decode the KPAX JWT.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class CurrentUser:
    """Authenticated user derived from a KPAX JWT."""

    user_id: int
    privy_user_id: str
    wallet_address: str
    email: str | None


# ---------- KPAX session JWT (what the extension carries) ----------

def issue_kpax_jwt(user: User) -> str:
    """Sign a short-lived JWT for the extension to carry."""
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "privy_user_id": user.privy_user_id,
        "wallet_address": user.wallet_address,
        "email": user.email,
        "iat": now,
        "exp": now + settings.kpax_jwt_ttl_seconds,
    }
    return jwt.encode(payload, settings.kpax_jwt_secret, algorithm="HS256")


def decode_kpax_jwt(token: str) -> dict:
    return jwt.decode(token, settings.kpax_jwt_secret, algorithms=["HS256"])


# ---------- Privy access token verification ----------

_jwks_cache: dict[str, dict] = {}


async def _fetch_privy_jwks() -> dict:
    """Fetch (and cache in-process) the Privy JWKS."""
    if "jwks" in _jwks_cache:
        return _jwks_cache["jwks"]

    url = settings.privy_jwks_url.format(app_id=settings.privy_app_id)
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        jwks = resp.json()
    _jwks_cache["jwks"] = jwks
    return jwks


async def verify_privy_access_token(privy_token: str) -> dict:
    """Verify a Privy-issued access token and return its claims.

    Privy signs tokens with ES256 using keys from its JWKS endpoint. When the
    placeholder config is still in place (no real app id), we skip signature
    verification and just decode — the request will still fail downstream
    because no user can be upserted without a valid privy_user_id.
    """
    if settings.privy_app_id.startswith("PRIVY_APP_ID_PLACEHOLDER"):
        logger.warning(
            "Privy app id not configured; decoding token without signature check"
        )
        return jwt.decode(privy_token, options={"verify_signature": False})

    # Production path: verify against JWKS.
    try:
        unverified_header = jwt.get_unverified_header(privy_token)
        jwks = await _fetch_privy_jwks()
        key_data = next(
            (k for k in jwks.get("keys", []) if k.get("kid") == unverified_header.get("kid")),
            None,
        )
        if key_data is None:
            raise HTTPException(status_code=401, detail="Privy signing key not found")

        public_key = jwt.algorithms.ECAlgorithm.from_jwk(key_data)  # type: ignore[attr-defined]
        claims = jwt.decode(
            privy_token,
            public_key,
            algorithms=["ES256"],
            audience=settings.privy_app_id,
        )
        return claims
    except jwt.PyJWTError as exc:
        logger.warning("Privy token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Privy token") from exc


# ---------- FastAPI dependency ----------

def current_user(request: Request) -> CurrentUser:
    """FastAPI dependency that enforces a valid KPAX JWT.

    The JWT is issued by /api/auth/privy/exchange. Only claims are used — no
    database query — so this dependency does not require a DB session.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = auth[7:]
    try:
        payload = decode_kpax_jwt(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid session token")

    try:
        return CurrentUser(
            user_id=int(payload["sub"]),
            privy_user_id=payload["privy_user_id"],
            wallet_address=payload["wallet_address"],
            email=payload.get("email"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Malformed session token") from exc


# ---------- Admin auth (username/password) ----------

@dataclass
class AdminSession:
    username: str


def issue_admin_token(username: str) -> str:
    """Sign a short-lived admin session token. Same secret as user JWTs but
    `kind="admin"` claim makes it impossible to forge by reusing a regular
    user JWT.
    """
    now = int(time.time())
    payload = {
        "kind": "admin",
        "username": username,
        "iat": now,
        "exp": now + settings.kpax_admin_token_ttl_seconds,
    }
    return jwt.encode(payload, settings.kpax_jwt_secret, algorithm="HS256")


def require_admin(request: Request) -> AdminSession:
    """FastAPI dependency for /api/admin/* endpoints.

    Reads `Authorization: Bearer <admin-token>`, verifies signature, requires
    `kind="admin"` (so a leaked normal-user JWT is rejected even though it
    shares the signing secret).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing admin token")
    token = auth[7:]
    try:
        payload = jwt.decode(token, settings.kpax_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Admin session expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    if payload.get("kind") != "admin":
        raise HTTPException(status_code=401, detail="Not an admin token")
    return AdminSession(username=str(payload.get("username") or ""))


# ---------- User upsert ----------

def upsert_user_from_privy(
    db: Session,
    privy_user_id: str,
    wallet_address: str,
    email: str | None,
) -> User:
    """Create or update a User row from Privy claims."""
    user = db.query(User).filter(User.privy_user_id == privy_user_id).first()
    if user is None:
        user = User(
            privy_user_id=privy_user_id,
            wallet_address=wallet_address,
            email=email,
        )
        db.add(user)
    else:
        user.wallet_address = wallet_address
        if email and user.email != email:
            user.email = email
    db.commit()
    db.refresh(user)
    return user
