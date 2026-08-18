"""Celery application."""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("rfp", broker=str(settings.redis_url), backend=str(settings.redis_url))

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # One task per worker process at a time. Ingestion is CPU- and memory-heavy
    # (OCR, layout models); prefetching several 400-page tenders onto one worker
    # is how a pod gets OOM-killed mid-document.
    worker_prefetch_multiplier=1,
    task_acks_late=True,  # redeliver if a worker dies mid-document
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_time_limit=3600,  # hard ceiling for a very large scanned tender
    task_soft_time_limit=3300,  # soft first, so the task can record FAILED
)

# Every five minutes. The sweep exists because an upload commits its row
# before the ingestion job is queued, so a broker outage strands documents at
# UPLOADED with nothing coming for them — a silent failure that looks to the
# customer like a slow upload rather than a lost one.
celery_app.conf.beat_schedule = {
    "requeue-stalled-uploads": {
        "task": "sweep.requeue_stalled_uploads",
        "schedule": 300.0,
    },
}

# Explicit, not `autodiscover_tasks`. Autodiscovery looks for a module named
# `tasks` inside each package it is given, so `autodiscover_tasks(
# ["app.workers.tasks"])` searched for `app.workers.tasks.tasks` — which does
# not exist. The worker therefore started with an empty registry and rejected
# every message as NotRegistered, while the API kept accepting uploads and
# queueing them. Nothing errored on the request path, so the only symptom was
# documents that stayed at UPLOADED forever.
celery_app.conf.imports = (
    "app.workers.tasks.ingest",
    "app.workers.tasks.sweep",
)
