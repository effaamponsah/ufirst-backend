"""
Celery beat schedule.

Each module registers its periodic tasks here.
Tasks are added in later phases; this file is intentionally sparse at Phase 0.
"""

from __future__ import annotations

from celery.schedules import crontab

from app.jobs.celery_app import celery_app

celery_app.conf.beat_schedule = {
    # ---------------------------------------------------------------------------
    # Wallet / Open Banking (Phase 3)
    # ---------------------------------------------------------------------------
    # "wallet.poll-pending-payments": {
    #     "task": "app.modules.wallet.tasks.poll_pending_payments",
    #     "schedule": 300,    # every 5 minutes
    #     "options": {"queue": "default"},
    # },
    # "wallet.expire-stale-authorizations": {
    #     "task": "app.modules.wallet.tasks.expire_stale_authorizations",
    #     "schedule": 60,     # every minute
    #     "options": {"queue": "default"},
    # },
    # "wallet.warn-expiring-consent": {
    #     "task": "app.modules.wallet.tasks.warn_expiring_bank_consent",
    #     "schedule": crontab(hour=8, minute=0),   # daily at 08:00 UTC
    #     "options": {"queue": "bulk"},
    # },
    # ---------------------------------------------------------------------------
    # Reporting / Reconciliation (Phase 9)
    # ---------------------------------------------------------------------------
    # "reporting.daily-reconciliation": {
    #     "task": "app.modules.reporting.tasks.run_daily_reconciliation",
    #     "schedule": crontab(hour=3, minute=0),   # 03:00 UTC
    #     "options": {"queue": "default"},
    # },
}
