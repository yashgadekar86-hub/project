"""
Central configuration for the AI Forex Command Center.

All secrets are loaded from environment variables / .env file.
Defaults are intentionally conservative. Live trading is NEVER enabled by default.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent  # repo root
BACKEND_DIR = Path(__file__).resolve().parent.parent


class TradingMode:
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"
    ALL = {BACKTEST, PAPER, LIVE}


VALID_TRADING_MODES = {TradingMode.BACKTEST, TradingMode.PAPER, TradingMode.LIVE}


class Settings(BaseSettings):
    """Application settings — see .env.example for documentation."""

    model_config = SettingsConfigDict(
        env_file=[".env", os.path.join(BASE_DIR, ".env")],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== App =====
    APP_NAME: str = "AI Forex Command Center"
    APP_ENV: str = "development"
    APP_SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    APP_DEBUG: bool = False
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ===== JWT =====
    JWT_SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440   # 24 hours
    JWT_REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    # ===== Database =====
    DATABASE_URL: str = "postgresql+asyncpg://aifx_user:aifx_pass@localhost:5432/aifx_trading"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "aifx_user"
    POSTGRES_PASSWORD: str = "aifx_pass"
    POSTGRES_DB: str = "aifx_trading"

    # ===== Redis =====
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None

    # ===== Celery =====
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ===== MT5 =====
    MT5_TERMINAL_PATH: Optional[str] = None
    MT5_DEFAULT_SERVER: Optional[str] = None
    MT5_DEFAULT_LOGIN: Optional[int] = None
    MT5_DEFAULT_PASSWORD: Optional[str] = None
    CREDENTIAL_ENCRYPTION_KEY: Optional[str] = None

    # ===== Default safety settings (conservative) =====
    DEFAULT_RISK_PER_TRADE_PCT: float = 0.5
    DEFAULT_MAX_DAILY_LOSS_PCT: float = 2.0
    DEFAULT_MAX_DRAWDOWN_PCT: float = 5.0
    DEFAULT_MAX_SIMULTANEOUS_TRADES: int = 3
    DEFAULT_MIN_AI_CONFIDENCE: int = 70
    DEFAULT_MIN_RISK_REWARD: float = 2.0
    DEFAULT_MAX_SPREAD_PIPS_MAJORS: float = 3.0
    DEFAULT_MAX_CONSECUTIVE_LOSSES: int = 3
    DEFAULT_TRADING_MODE: str = TradingMode.PAPER  # NEVER default to LIVE
    LIVE_TRADING_REQUIRES_EXPLICIT_ACTIVATION: bool = True

    # ===== News =====
    NEWS_API_KEY: Optional[str] = None
    NEWS_FILTER_ENABLED: bool = False
    NEWS_BLOCK_MINUTES_BEFORE: int = 60
    NEWS_BLOCK_MINUTES_AFTER: int = 30

    # ===== Alerts =====
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    ALERT_EMAIL_FROM: Optional[str] = None
    ALERT_EMAIL_TO: Optional[str] = None

    # ===== Logging =====
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # ===== Derived =====
    @property
    def cors_origins_list(self) -> List[str]:
        origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        if self.FRONTEND_URL and self.FRONTEND_URL not in origins:
            origins.append(self.FRONTEND_URL)
        # Allow all hosts in development (sandbox previews, etc.)
        if self.APP_ENV == "development":
            origins.append("*")
        return origins

    @field_validator("DEFAULT_TRADING_MODE")
    @classmethod
    def validate_trading_mode(cls, v: str) -> str:
        if v not in VALID_TRADING_MODES:
            raise ValueError(f"Invalid trading mode: {v}")
        # Safety: never allow env to set LIVE at boot — live requires explicit UI activation
        if v == TradingMode.LIVE and os.environ.get("AIFX_FORCE_LIVE_AT_BOOT", "0") != "1":
            return TradingMode.PAPER
        return v


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
