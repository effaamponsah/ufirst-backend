from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "ufirst",
    broker=settings.effective_celery_broker,
    backend=settings.effective_celery_backend,
    include=[
        # Task modules — add each module's tasks here as they are implemented
        # "app.modules.wallet.tasks",
        # "app.modules.compliance.tasks",
        # "app.modules.notification.tasks",
        # "app.modules.reporting.tasks",
    ],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Queues — tasks are routed to the appropriate queue by priority
    task_default_queue="default",
    task_queues={
        "critical": {"exchange": "critical", "routing_key": "critical"},
        "default": {"exchange": "default", "routing_key": "default"},
        "bulk": {"exchange": "bulk", "routing_key": "bulk"},
    },
    # Reliability
    task_acks_late=True,           # ack only after task completes
    task_reject_on_worker_lost=True,
    task_track_started=True,
    # Result TTL — results are transient, not used for business logic
    result_expires=3600,
)
