"""Auth router: exchange Privy access token for KPAX session JWT."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    current_user,
    issue_kpax_jwt,
    upsert_user_from_privy,
    verify_privy_access_token,
)
from app.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PrivyExchangeRequest(BaseModel):
    privy_access_token: str
    wallet_address: str
    email: str | None = None


class PrivyExchangeResponse(BaseModel):
    kpax_jwt: str
    user: dict


@router.post("/privy/exchange", response_model=PrivyExchangeResponse)
async def exchange_privy_token(
    body: PrivyExchangeRequest,
    db: Session = Depends(get_db),
):
    """Verify a Privy access token and return a KPAX session JWT.

    The frontend obtains `privy_access_token` + `wallet_address` from the Privy
    SDK and posts them here. We verify the token, upsert the user, and return
    a KPAX-signed JWT that the extension will carry in subsequent requests.
    """
    claims = await verify_privy_access_token(body.privy_access_token)

    privy_user_id = claims.get("sub") or claims.get("user_id")
    if not privy_user_id:
        raise HTTPException(status_code=400, detail="Privy token missing subject")

    user = upsert_user_from_privy(
        db,
        privy_user_id=privy_user_id,
        wallet_address=body.wallet_address,
        email=body.email,
    )

    kpax_jwt = issue_kpax_jwt(user)
    return PrivyExchangeResponse(
        kpax_jwt=kpax_jwt,
        user={
            "id": user.id,
            "privy_user_id": user.privy_user_id,
            "wallet_address": user.wallet_address,
            "email": user.email,
        },
    )


@router.get("/me")
def get_me(user: CurrentUser = Depends(current_user)):
    """Return the current user (validates the KPAX JWT)."""
    return {
        "user_id": user.user_id,
        "privy_user_id": user.privy_user_id,
        "wallet_address": user.wallet_address,
        "email": user.email,
    }
