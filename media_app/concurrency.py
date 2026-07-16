"""Redis-backed concurrency guards and image job status for Celery workers."""

from __future__ import annotations

import logging
import time
from typing import Callable, Literal, TypeVar

from django.conf import settings

logger = logging.getLogger("dbt_platform.media_app")

SlotKind = Literal["interactive", "batch"]

IMAGE_SLOT_KEYS: dict[SlotKind, str] = {
    "interactive": "dbt:image:active_slots:interactive",
    "batch": "dbt:image:active_slots:batch",
}
IMAGE_STATUS_PREFIX = "dbt:image:status:"
IMAGE_ERROR_PREFIX = "dbt:image:error:"
IMAGE_FAIL_COUNT_KEY = "dbt:image:fail_count"
IMAGE_SLOT_SATURATED_SINCE_PREFIX = "dbt:image:slot_saturated_since:"
IMAGE_STATUS_TTL_SECONDS = 600

T = TypeVar("T")


def _redis_client():
    try:
        import redis

        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        return client
    except Exception as exc:
        logger.warning("Redis unavailable for image concurrency guard: %s", exc)
        return None


def _slot_max(kind: SlotKind) -> int:
    if kind == "batch":
        return int(getattr(settings, "IMAGE_BATCH_MAX_CONCURRENT", 1))
    return int(getattr(settings, "IMAGE_INTERACTIVE_MAX_CONCURRENT", 3))


def image_max_concurrent() -> int:
    """Legacy total — sum of interactive + batch slot budgets."""
    return _slot_max("interactive") + _slot_max("batch")


def slot_wait_timeout_seconds() -> int:
    return int(getattr(settings, "IMAGE_SLOT_WAIT_TIMEOUT_SECONDS", 120))


def slot_wait_interval_seconds() -> int:
    return int(getattr(settings, "IMAGE_SLOT_WAIT_INTERVAL_SECONDS", 3))


def set_image_status(resource_id: str, status: str) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        client.setex(f"{IMAGE_STATUS_PREFIX}{resource_id}", IMAGE_STATUS_TTL_SECONDS, status)
        if status != "failed":
            client.delete(f"{IMAGE_ERROR_PREFIX}{resource_id}")
    except Exception:
        pass


def get_image_status(resource_id: str) -> str:
    client = _redis_client()
    if not client:
        return ""
    try:
        raw = client.get(f"{IMAGE_STATUS_PREFIX}{resource_id}")
        if not raw:
            return ""
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception:
        return ""


def set_image_error(resource_id: str, message: str) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        client.setex(
            f"{IMAGE_ERROR_PREFIX}{resource_id}",
            IMAGE_STATUS_TTL_SECONDS,
            (message or "配图失败")[:200],
        )
        client.incr(IMAGE_FAIL_COUNT_KEY)
        client.expire(IMAGE_FAIL_COUNT_KEY, 86400)
    except Exception:
        pass


def get_image_error(resource_id: str) -> str:
    client = _redis_client()
    if not client:
        return ""
    try:
        raw = client.get(f"{IMAGE_ERROR_PREFIX}{resource_id}")
        if not raw:
            return ""
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception:
        return ""


def mark_image_failed(resource_id: str, message: str = "配图失败，请稍后重试") -> None:
    set_image_status(resource_id, "failed")
    set_image_error(resource_id, message)


def clear_image_status(resource_id: str) -> None:
    client = _redis_client()
    if not client:
        return
    try:
        client.delete(f"{IMAGE_STATUS_PREFIX}{resource_id}")
        client.delete(f"{IMAGE_ERROR_PREFIX}{resource_id}")
    except Exception:
        pass


def get_active_slot_count(kind: SlotKind = "interactive") -> int:
    client = _redis_client()
    if not client:
        return 0
    try:
        raw = client.get(IMAGE_SLOT_KEYS[kind])
        if not raw:
            return 0
        return max(0, int(raw))
    except Exception:
        return 0


def get_image_fail_count() -> int:
    client = _redis_client()
    if not client:
        return 0
    try:
        raw = client.get(IMAGE_FAIL_COUNT_KEY)
        return int(raw or 0)
    except Exception:
        return 0


def track_slot_saturation(kind: SlotKind = "interactive") -> float | None:
    """Return seconds since interactive/batch slots have been continuously full."""
    client = _redis_client()
    if not client:
        return None
    key = f"{IMAGE_SLOT_SATURATED_SINCE_PREFIX}{kind}"
    try:
        active = get_active_slot_count(kind)
        max_slots = _slot_max(kind)
        if active < max_slots:
            client.delete(key)
            return None
        raw = client.get(key)
        now = time.time()
        if not raw:
            client.setex(key, 600, str(now))
            return 0.0
        started = float(raw.decode() if isinstance(raw, bytes) else raw)
        return max(0.0, now - started)
    except Exception:
        return None


def try_acquire_image_slot(kind: SlotKind = "interactive") -> bool:
    client = _redis_client()
    if client is None:
        return True

    max_slots = _slot_max(kind)
    key = IMAGE_SLOT_KEYS[kind]
    try:
        active = int(client.incr(key))
        client.expire(key, 300)
        if active <= max_slots:
            return True
        client.decr(key)
        return False
    except Exception as exc:
        logger.warning("Image slot acquire failed, allowing task: %s", exc)
        return True


def release_image_slot(kind: SlotKind = "interactive") -> None:
    client = _redis_client()
    if client is None:
        return
    key = IMAGE_SLOT_KEYS[kind]
    try:
        remaining = int(client.decr(key))
        if remaining < 0:
            client.set(key, 0)
    except Exception:
        pass


def run_with_image_slot(
    resource_id: str,
    fn: Callable[[], T],
    *,
    kind: SlotKind = "interactive",
) -> T:
    set_image_status(resource_id, "processing")
    try:
        return fn()
    finally:
        release_image_slot(kind)


def wait_label_for_status(status: str) -> str:
    if status == "failed":
        return "配图失败，请稍后重试"
    if status == "queued":
        return "排队中，请稍候..."
    return "情景配图生成中..."


def slot_wait_deadline_passed(wait_started_at: float | None) -> bool:
    if wait_started_at is None:
        return False
    return (time.time() - float(wait_started_at)) >= slot_wait_timeout_seconds()
