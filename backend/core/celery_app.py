"""Celery app for background tasks (backtests, ML training, alerts, etc.)."""
from __future__ import annotations

from celery import Celery

from backend.core.config import settings

celery_app = Celery(
    "aifx",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_routes={
        "backend.services.tasks.*": {"queue": "default"},
        "trading-engine.*": {"queue": "trading"},
        "ai-engine.*": {"queue": "ml"},
    },
)
