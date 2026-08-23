"""Authentication endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.schemas import TokenOut, UserCreate, UserLogin, UserOut
from backend.core.config import settings
from backend.core.security import (
    create_access_token, create_refresh_token, decode_token,
    hash_password, verify_password,
)
from backend.database.session import get_db
from backend.models.user import (
    TradingMode, User, UserRole, UserSettings,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


def _make_tokens(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user.id, {"role": user.role.value}),
        refresh_token=create_refresh_token(user.id),
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(
        select(User).where((User.email == body.email.lower()) | (User.username == body.username))
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email or username already registered")
    user = User(
        email=body.email.lower(),
        username=body.username,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=UserRole.TRADER,
    )
    db.add(user)
    await db.flush()
    db.add(UserSettings(user_id=user.id, trading_mode=TradingMode.PAPER))
    await db.commit()
    await db.refresh(user)
    return _make_tokens(user)


@router.post("/login", response_model=TokenOut)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    q = select(User).where(
        (User.email == body.email_or_username.lower()) |
        (User.username == body.email_or_username)
    )
    user = (await db.execute(q)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return _make_tokens(user)


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise creds_exc
        user_id = payload.get("sub")
    except Exception:
        raise creds_exc
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise creds_exc
    return _make_tokens(user)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


# ---- DEV ONLY: no-password login for sandbox/demo use ----
# Disabled automatically in non-development environments.
class DevLoginRequest(BaseModel):
    username: Optional[str] = "demo"


@router.post("/dev-login", response_model=TokenOut, include_in_schema=False)
async def dev_login(body: DevLoginRequest, db: AsyncSession = Depends(get_db)):
    if settings.APP_ENV != "development":
        raise HTTPException(status_code=404, detail="Not found")
    target = body.username or "demo"
    q = select(User).where(
        (User.username == target) | (User.email == target.lower())
    )
    user = (await db.execute(q)).scalar_one_or_none()
    if not user:
        # fall back to any active TRADER user
        user = (await db.execute(
            select(User).where(User.is_active == True).limit(1)  # noqa: E712
        )).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No dev user available")
    if not user.is_active:
        user.is_active = True
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return _make_tokens(user)
