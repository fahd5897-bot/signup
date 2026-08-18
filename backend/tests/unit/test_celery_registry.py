"""The worker must actually know the tasks the API sends it.

This is the cheapest possible test and it guards the most expensive silent
failure in the system. `autodiscover_tasks(["app.workers.tasks"])` looks for
`app.workers.tasks.tasks`, which does not exist — so the worker booted with an
empty registry and rejected every message as NotRegistered, while the API
carried on accepting uploads and queueing them. Nothing failed on the request
path. The only symptom was documents that stayed at UPLOADED forever, which is
indistinguishable from a slow queue.
"""

from __future__ import annotations

import pytest

#: Every task name the application sends, by the name it sends it under.
#: A task renamed on one side and not the other fails the same silent way.
EXPECTED = {
    "ingest.parse_document",
    "sweep.requeue_stalled_uploads",
}


@pytest.fixture
def registry(settings):
    from app.workers.celery_app import celery_app

    # Exactly what `celery worker` does at boot.
    celery_app.loader.import_default_modules()
    return celery_app.tasks


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_worker_registers_the_task(registry, name) -> None:
    assert name in registry


def test_the_beat_schedule_only_names_registered_tasks(registry) -> None:
    """A scheduled name with no task behind it fails once every interval, in
    the beat log, where nobody is looking."""
    from app.workers.celery_app import celery_app

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert scheduled <= set(registry)


def test_the_task_retry_ceiling_follows_the_setting(settings) -> None:
    """Celery's ceiling and the task body's check must be the same number.

    The body retries while `request.retries < settings.ingest_max_retries`. If
    Celery's own ceiling were lower, `self.retry` would raise
    MaxRetriesExceededError from inside the branch meant to mark the document
    FAILED — and the document would sit in a transient status forever, which
    the interface renders as "still processing".
    """
    from app.workers.tasks.ingest import parse_document_task

    assert parse_document_task.max_retries == settings.ingest_max_retries
