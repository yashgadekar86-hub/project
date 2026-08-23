"""MT5 connection & broker endpoints."""
from __future__ import annotations

import asyncio
import platform
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.schemas import MT5ConnectRequest, MT5ConnectionStatus, SymbolOut
from backend.core.security import encrypt_credential, decrypt_credential
from backend.core.state import (
    STATE, disconnect_connector, get_or_create_connector, get_state,
)
from backend.database.session import get_db
from backend.models.market import Symbol as DBSymbol
from backend.models.user import Broker, TradingAccount, User

router = APIRouter(prefix="/mt5", tags=["MT5 Connection"])


def _spec_to_dict(spec) -> dict:
    return {
        "name": spec.name, "digits": spec.digits, "point": spec.point,
        "pip_size": spec.pip_size, "contract_size": spec.contract_size,
        "lot_min": spec.lot_min, "lot_max": spec.lot_max,
        "lot_step": spec.lot_step, "tick_size": spec.tick_size,
        "tick_value": spec.tick_value, "currency_base": spec.currency_base,
        "currency_profit": spec.currency_profit,
        "currency_margin": spec.currency_margin,
        "spread": spec.spread, "spread_float": spec.spread_float,
        "stops_level": spec.stops_level, "trade_mode": spec.trade_mode,
        "trade_exemode": spec.trade_exemode, "trade_calc_mode": spec.trade_calc_mode,
    }


def _major_pair(name: str) -> bool:
    majors = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"}
    return name.upper().replace(".", "").replace("-", "") in majors


@router.post("/connect", response_model=MT5ConnectionStatus)
async def connect(req: MT5ConnectRequest, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    if platform.system() not in ("Windows", "Linux", "Darwin"):
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform.system()}")

    try:
        from trading_engine.mt5.connector import is_mt5_available, discover_terminal_path, MT5ConnectionError
        if not is_mt5_available():
            import platform as _pf
            sys_name = _pf.system()
            hint = ""
            if sys_name == "Linux":
                hint = (
                    " This sandbox is running on Linux. MT5 requires Windows (or Wine). "
                    "The backend must run on the same Windows machine where your MetaTrader 5 terminal "
                    "(FortressFX or other broker) is installed. Paper-trading mode works without MT5."
                )
            elif sys_name == "Darwin":
                hint = " MT5 on macOS requires Wine/CrossOver or a Windows VM."
            raise HTTPException(
                status_code=400,
                detail=(
                    "MetaTrader5 is not available in this environment. "
                    "Install the MetaTrader5 Python package on the Windows machine where MT5 is installed."
                    + hint
                )
            )
        path = req.terminal_path or discover_terminal_path()

        def _do_connect():
            return get_or_create_connector(
                f"user:{user.id}",
                login=req.login, password=req.password,
                server=req.server, terminal_path=path,
            )
        conn = await asyncio.to_thread(_do_connect)

        def _info():
            return conn.get_account_info(), conn.list_symbols()
        acct_info, syms = await asyncio.to_thread(_info)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MT5 connection failed: {e}")

    # Upsert broker + account
    broker = (await db.execute(select(Broker).where(Broker.name == acct_info.broker))).scalar_one_or_none()
    if not broker:
        broker = Broker(name=acct_info.broker, server_name=acct_info.server, mt5_compatible=True)
        db.add(broker); await db.flush()

    existing = (await db.execute(
        select(TradingAccount).where(
            TradingAccount.user_id == user.id,
            TradingAccount.mt5_login == acct_info.login,
            TradingAccount.mt5_server == acct_info.server,
        )
    )).scalar_one_or_none()
    if existing:
        existing.broker_id = broker.id
        existing.mt5_password_encrypted = encrypt_credential(req.password)
        existing.mt5_path = path
        existing.currency = acct_info.currency
        existing.leverage = acct_info.leverage
        existing.balance_snapshot = acct_info.balance
        existing.equity_snapshot = acct_info.equity
        existing.account_type = acct_info.account_type
        if req.label:
            existing.label = req.label
    else:
        account = TradingAccount(
            user_id=user.id, broker_id=broker.id,
            label=req.label or f"MT5 {acct_info.login}@{acct_info.server}",
            mt5_login=acct_info.login, mt5_server=acct_info.server,
            mt5_password_encrypted=encrypt_credential(req.password),
            mt5_path=path, account_type=acct_info.account_type,
            currency=acct_info.currency, leverage=acct_info.leverage,
            balance_snapshot=acct_info.balance, equity_snapshot=acct_info.equity,
        )
        db.add(account); await db.flush()

    # Cache symbol specs (best-effort: first N majors, then others)
    state = get_state()
    await _cache_symbol_specs(conn, syms, db, account_id=getattr(existing, "id", None))
    state.paper_balance[str(user.id)] = acct_info.balance
    state.paper_start_balance[str(user.id)] = acct_info.balance

    await db.commit()
    return MT5ConnectionStatus(
        connected=True, broker=acct_info.broker, server=acct_info.server,
        login=acct_info.login, balance=acct_info.balance, equity=acct_info.equity,
        margin_free=acct_info.margin_free, margin_level=acct_info.margin_level,
        leverage=acct_info.leverage, currency=acct_info.currency,
        account_type=acct_info.account_type, symbols_count=len(syms),
    )


@router.get("/status", response_model=MT5ConnectionStatus)
async def status(user: User = Depends(get_current_user)):
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    if not conn or not conn.connected:
        return MT5ConnectionStatus(connected=False)
    try:
        info = await asyncio.to_thread(conn.get_account_info)
        syms = await asyncio.to_thread(conn.list_symbols)
        return MT5ConnectionStatus(
            connected=True, broker=info.broker, server=info.server,
            login=info.login, balance=info.balance, equity=info.equity,
            margin_free=info.margin_free, margin_level=info.margin_level,
            leverage=info.leverage, currency=info.currency,
            account_type=info.account_type, symbols_count=len(syms),
        )
    except Exception as e:
        return MT5ConnectionStatus(connected=False, broker=str(e))


@router.post("/disconnect")
async def disconnect(user: User = Depends(get_current_user)):
    disconnect_connector(f"user:{user.id}")
    return {"ok": True}


@router.get("/symbols", response_model=List[SymbolOut])
async def symbols(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                  watchlist_only: bool = False):
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    result = await db.execute(select(DBSymbol))
    rows = result.scalars().all()
    return [SymbolOut.model_validate(r) for r in rows]


async def _cache_symbol_specs(conn, syms: List[str], db: AsyncSession, account_id=None,
                               limit: int = 60):
    """Pull specs for a bounded set of symbols and persist/cache them."""
    state = get_state()
    # Prioritize majors
    majors = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
              "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURAUD"}
    ordered = sorted(syms, key=lambda s: (0 if s.upper().replace(".","").replace("-","") in majors else 1, s))[:limit]

    for sym_name in ordered:
        try:
            def _spec(n=sym_name):
                return conn.get_symbol_spec(n)
            spec = await asyncio.to_thread(_spec)
        except Exception:
            continue
        # Upsert DB record
        existing = (await db.execute(
            select(DBSymbol).where(DBSymbol.account_id == account_id, DBSymbol.name == spec.name)
        )).scalar_one_or_none()
        payload = dict(
            name=spec.name, broker_name=spec.name, digits=spec.digits, point=spec.point,
            pip_size=spec.pip_size, contract_size=spec.contract_size,
            lot_min=spec.lot_min, lot_max=spec.lot_max, lot_step=spec.lot_step,
            tick_size=spec.tick_size, tick_value=spec.tick_value, spread=spec.spread,
            spread_float=spec.spread_float, stops_level=spec.stops_level,
            trade_mode=spec.trade_mode, trade_exemode=spec.trade_exemode,
            currency_base=spec.currency_base, currency_profit=spec.currency_profit,
            currency_margin=spec.currency_margin, is_enabled=True, is_major=_major_pair(spec.name),
        )
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(DBSymbol(account_id=account_id, **payload))
        state.symbol_specs[spec.name] = spec
