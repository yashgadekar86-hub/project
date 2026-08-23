"""FastAPI dependencies: DB session, current user, etc."""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from fastapi import Depends, HTTPException, status, WebSocket, WebSocketException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.security import decode_token
from backend.database.session import AsyncSessionLocal, get_db
from backend.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# Magic token the frontend uses in dev-bypass mode to skip real authentication.
# This ONLY works when APP_ENV=development and is hard-rejected elsewhere.
DEV_BYPASS_TOKEN = "dev-bypass-aifx-demo-user"


async def _get_dev_bypass_user(db: AsyncSession) -> Optional[User]:
    """Return the demo/dev user, creating one on the fly if needed."""
    if settings.APP_ENV != "development":
        return None
    user = (await db.execute(
        select(User).where(User.username == "demo")
    )).scalar_one_or_none()
    if user:
        if not user.is_active:
            user.is_active = True
            await db.commit()
        return user
    # Lazily create the demo user if it doesn't exist
    from backend.models.user import UserRole, UserSettings, TradingMode
    from backend.core.security import hash_password
    from datetime import datetime, timezone
    user = User(
        email="demo@example.com",
        username="demo",
        full_name="Demo Trader",
        hashed_password=hash_password("demo12345"),
        role=UserRole.TRADER,
        is_active=True,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()
    db.add(UserSettings(user_id=user.id, trading_mode=TradingMode.PAPER))
    await db.commit()
    await db.refresh(user)
    return user


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # DEV-BYPASS: accept the magic token without JWT verification.
    if token == DEV_BYPASS_TOKEN and settings.APP_ENV == "development":
        user = await _get_dev_bypass_user(db)
        if user:
            return user

    if not token:
        raise creds_exc
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise creds_exc
    except (JWTError, ValueError):
        raise creds_exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise creds_exc
    return user


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    if not token:
        return None
    if token == DEV_BYPASS_TOKEN and settings.APP_ENV == "development":
        return await _get_dev_bypass_user(db)
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
    except Exception:
        return None


async def get_current_admin(current: User = Depends(get_current_user)) -> User:
    from backend.models.user import UserRole
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    return current


async def get_ws_user(websocket: WebSocket, db: AsyncSession) -> Optional[User]:
    """Authenticate a WebSocket by token in query param `?token=...`."""
    token = websocket.query_params.get("token")
    if not token:
        auth = websocket.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
    if not token:
        return None
    if token == DEV_BYPASS_TOKEN and settings.APP_ENV == "development":
        return await _get_dev_bypass_user(db)
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        return user if user and user.is_active else None
    except Exception:
        return None
