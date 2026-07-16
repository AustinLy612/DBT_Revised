"""Celery tasks for media_app — async image generation."""

from __future__ import annotations

import logging
import uuid

from celery import shared_task

from media_app.concurrency import (
    clear_image_status,
    get_image_status,
    run_with_image_slot,
    set_image_status,
    try_acquire_image_slot,
)

logger = logging.getLogger("dbt_platform.media_app")

SCENE_IMAGE_CACHE_PREFIX = "dbt:teaching:scene_image:"
SCENE_ACTIVE_JOB_PREFIX = "dbt:teaching:scene_active_job:"
SCENE_IMAGE_CACHE_TTL = 3600


def _redis_client():
    from media_app.concurrency import _redis_client as get_client
    return get_client()


def _scene_cache_key(session_id: str, job_id: str) -> str:
    return f"{SCENE_IMAGE_CACHE_PREFIX}{session_id}:{job_id}"


def get_scene_image_url(session_id: str, job_id: str | None = None) -> str:
    client = _redis_client()
    if not client:
        return ""
    try:
        if not job_id:
            raw_job = client.get(f"{SCENE_ACTIVE_JOB_PREFIX}{session_id}")
            if raw_job:
                job_id = raw_job.decode() if isinstance(raw_job, bytes) else str(raw_job)
        if job_id:
            raw = client.get(_scene_cache_key(session_id, job_id))
            if raw:
                return raw.decode() if isinstance(raw, bytes) else str(raw)
        # Legacy single-key cache (pre job_id)
        raw = client.get(f"{SCENE_IMAGE_CACHE_PREFIX}{session_id}")
        if not raw:
            return ""
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception:
        return ""


def _store_scene_image_url(session_id: str, job_id: str, image_url: str) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        client.setex(_scene_cache_key(session_id, job_id), SCENE_IMAGE_CACHE_TTL, image_url)
        client.setex(f"{SCENE_ACTIVE_JOB_PREFIX}{session_id}", SCENE_IMAGE_CACHE_TTL, job_id)
    except Exception:
        pass


@shared_task(bind=True, max_retries=5, default_retry_delay=5, queue="interactive-images")
def generate_image_async(self, session_id: str, image_prompt: str, message_id: str | None = None):
    from media_app.services import generate_image
    from teaching.models import ChatMessage

    resource_id = message_id or session_id
    if not try_acquire_image_slot("interactive"):
        set_image_status(resource_id, "queued")
        raise self.retry(countdown=5)

    try:
        img_result = run_with_image_slot(
            resource_id,
            lambda: generate_image(image_prompt),
            kind="interactive",
        )
    except Exception as exc:
        set_image_status(resource_id, "failed")
        raise self.retry(exc=exc) from exc

    if not img_result.get("urls"):
        set_image_status(resource_id, "failed")
        return

    image_url = img_result["urls"][0]
    if message_id:
        message = ChatMessage.objects.filter(message_id=message_id, session_id=session_id).first()
    else:
        message = (
            ChatMessage.objects.filter(session_id=session_id, role=ChatMessage.Role.ASSISTANT)
            .order_by("-created_at").first()
        )

    if message:
        message.image_url = image_url
        message.save(update_fields=["image_url"])
        clear_image_status(resource_id)
    else:
        set_image_status(resource_id, "failed")


@shared_task(bind=True, max_retries=5, default_retry_delay=5, queue="interactive-images")
def generate_scene_image_async(self, session_id: str, image_prompt: str, job_id: str):
    from media_app.services import generate_image

    resource_id = f"scene:{session_id}:{job_id}"
    if not try_acquire_image_slot("interactive"):
        set_image_status(resource_id, "queued")
        raise self.retry(countdown=5)

    try:
        img_result = run_with_image_slot(
            resource_id,
            lambda: generate_image(image_prompt),
            kind="interactive",
        )
    except Exception as exc:
        set_image_status(resource_id, "failed")
        raise self.retry(exc=exc) from exc

    if not img_result.get("urls"):
        set_image_status(resource_id, "failed")
        return

    _store_scene_image_url(session_id, job_id, img_result["urls"][0])
    clear_image_status(resource_id)


def dispatch_teaching_image(session_id: str, image_prompt: str, message_id: str) -> None:
    from teaching.models import ChatMessage

    message = ChatMessage.objects.filter(message_id=message_id).first()
    if message and message.image_url:
        return
    if get_image_status(message_id) in ("queued", "processing"):
        return
    set_image_status(message_id, "queued")
    generate_image_async.delay(session_id, image_prompt, message_id=message_id)


def dispatch_scene_image(session_id: str, image_prompt: str) -> str:
    job_id = str(uuid.uuid4())
    resource_id = f"scene:{session_id}:{job_id}"
    set_image_status(resource_id, "queued")
    generate_scene_image_async.delay(session_id, image_prompt, job_id)
    return job_id
