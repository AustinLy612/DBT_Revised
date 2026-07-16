"""Celery tasks for media_app — async image generation."""

from __future__ import annotations

import logging
import time
import uuid

from celery import shared_task
from django.conf import settings

from media_app.concurrency import (
    clear_image_status,
    get_image_status,
    mark_image_failed,
    run_with_image_slot,
    set_image_status,
    slot_wait_deadline_passed,
    slot_wait_interval_seconds,
    try_acquire_image_slot,
)

logger = logging.getLogger("dbt_platform.media_app")

SCENE_IMAGE_CACHE_PREFIX = "dbt:teaching:scene_image:"
SCENE_ACTIVE_JOB_PREFIX = "dbt:teaching:scene_active_job:"
SCENE_PROMPT_PREFIX = "dbt:teaching:scene_prompt:"
SCENE_IMAGE_CACHE_TTL = 3600


def _redis_client():
    from media_app.concurrency import _redis_client as get_client
    return get_client()


def _scene_cache_key(session_id: str, job_id: str) -> str:
    return f"{SCENE_IMAGE_CACHE_PREFIX}{session_id}:{job_id}"


def _provider_max_retries() -> int:
    return int(getattr(settings, "IMAGE_PROVIDER_MAX_RETRIES", 3))


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


def get_scene_prompt(session_id: str, job_id: str) -> str:
    client = _redis_client()
    if not client:
        return ""
    try:
        raw = client.get(f"{SCENE_PROMPT_PREFIX}{session_id}:{job_id}")
        if not raw:
            return ""
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception:
        return ""


def _store_scene_prompt(session_id: str, job_id: str, image_prompt: str) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        client.setex(
            f"{SCENE_PROMPT_PREFIX}{session_id}:{job_id}",
            SCENE_IMAGE_CACHE_TTL,
            image_prompt,
        )
    except Exception:
        pass


def _store_scene_image_url(session_id: str, job_id: str, image_url: str) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        client.setex(_scene_cache_key(session_id, job_id), SCENE_IMAGE_CACHE_TTL, image_url)
        client.setex(f"{SCENE_ACTIVE_JOB_PREFIX}{session_id}", SCENE_IMAGE_CACHE_TTL, job_id)
    except Exception:
        pass


def _redispatch_for_slot_wait(
    task,
    args: list,
    kwargs: dict,
    *,
    queue: str = "interactive-images",
    priority: int = 5,
) -> None:
    """Re-queue without consuming Celery provider-retry budget."""
    wait_started_at = kwargs.get("wait_started_at")
    if wait_started_at is None:
        kwargs = {**kwargs, "wait_started_at": time.time()}
    task.apply_async(
        args=args,
        kwargs=kwargs,
        countdown=slot_wait_interval_seconds(),
        queue=queue,
        priority=priority,
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=5, queue="interactive-images")
def generate_image_async(
    self,
    session_id: str,
    image_prompt: str,
    message_id: str | None = None,
    wait_started_at: float | None = None,
):
    from media_app.services import generate_image
    from teaching.models import ChatMessage

    resource_id = message_id or session_id
    if not try_acquire_image_slot("interactive"):
        set_image_status(resource_id, "queued")
        if slot_wait_deadline_passed(wait_started_at):
            mark_image_failed(resource_id, "配图排队超时，请稍后重试")
            return
        _redispatch_for_slot_wait(
            generate_image_async,
            [session_id, image_prompt],
            {"message_id": message_id, "wait_started_at": wait_started_at or time.time()},
            queue="interactive-images",
            priority=8,
        )
        return

    try:
        img_result = run_with_image_slot(
            resource_id,
            lambda: generate_image(image_prompt),
            kind="interactive",
        )
    except Exception as exc:
        if self.request.retries >= _provider_max_retries():
            mark_image_failed(resource_id, "配图服务暂时不可用，请稍后重试")
            return
        set_image_status(resource_id, "queued")
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries)) from exc

    if not img_result.get("urls"):
        mark_image_failed(resource_id, "配图未返回有效图片")
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
        mark_image_failed(resource_id, "配图保存失败")


@shared_task(bind=True, max_retries=3, default_retry_delay=5, queue="interactive-images")
def generate_scene_image_async(
    self,
    session_id: str,
    image_prompt: str,
    job_id: str,
    wait_started_at: float | None = None,
):
    from media_app.services import generate_image

    resource_id = f"scene:{session_id}:{job_id}"
    if not try_acquire_image_slot("interactive"):
        set_image_status(resource_id, "queued")
        if slot_wait_deadline_passed(wait_started_at):
            mark_image_failed(resource_id, "配图排队超时，请稍后重试")
            return
        _redispatch_for_slot_wait(
            generate_scene_image_async,
            [session_id, image_prompt, job_id],
            {"wait_started_at": wait_started_at or time.time()},
            queue="interactive-images",
            priority=9,
        )
        return

    try:
        img_result = run_with_image_slot(
            resource_id,
            lambda: generate_image(image_prompt),
            kind="interactive",
        )
    except Exception as exc:
        if self.request.retries >= _provider_max_retries():
            mark_image_failed(resource_id, "配图服务暂时不可用，请稍后重试")
            return
        set_image_status(resource_id, "queued")
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries)) from exc

    if not img_result.get("urls"):
        mark_image_failed(resource_id, "配图未返回有效图片")
        return

    _store_scene_image_url(session_id, job_id, img_result["urls"][0])
    clear_image_status(resource_id)


def dispatch_teaching_image(session_id: str, image_prompt: str, message_id: str) -> bool:
    from teaching.models import ChatMessage
    from teaching.services_images import can_dispatch_teaching_image

    message = ChatMessage.objects.filter(message_id=message_id).first()
    if message and message.image_url:
        return False
    if get_image_status(message_id) in ("queued", "processing"):
        return False
    if not can_dispatch_teaching_image(session_id, getattr(message, "teaching_step", 0) or 0):
        logger.info("Teaching image quota exceeded for session=%s message=%s", session_id, message_id)
        return False
    set_image_status(message_id, "queued")
    generate_image_async.apply_async(
        args=[session_id, image_prompt],
        kwargs={"message_id": message_id},
        queue="interactive-images",
        priority=8,
    )
    return True


def dispatch_scene_image(session_id: str, image_prompt: str) -> str:
    job_id = str(uuid.uuid4())
    resource_id = f"scene:{session_id}:{job_id}"
    set_image_status(resource_id, "queued")
    _store_scene_prompt(session_id, job_id, image_prompt)
    generate_scene_image_async.apply_async(
        args=[session_id, image_prompt, job_id],
        queue="interactive-images",
        priority=9,
    )
    return job_id
