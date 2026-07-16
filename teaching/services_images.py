"""Teaching image quota helpers — keep text teaching independent of image capacity."""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger("dbt_platform.teaching")


def _max_per_step() -> int:
    return int(getattr(settings, "TEACHING_IMAGE_MAX_PER_STEP", 1))


def _max_per_session() -> int:
    return int(getattr(settings, "TEACHING_IMAGE_MAX_PER_SESSION", 3))


def _count_active_or_ready(session_id: str, teaching_step: int | None = None) -> int:
    from media_app.concurrency import get_image_status
    from teaching.models import ChatMessage

    qs = ChatMessage.objects.filter(
        session_id=session_id,
        role=ChatMessage.Role.ASSISTANT,
    ).exclude(image_prompt="")
    if teaching_step is not None and teaching_step > 0:
        qs = qs.filter(teaching_step=teaching_step)

    count = 0
    for msg in qs.only("message_id", "image_url"):
        if msg.image_url:
            count += 1
            continue
        if get_image_status(msg.message_id) in ("queued", "processing"):
            count += 1
    return count


def can_dispatch_teaching_image(session_id: str, teaching_step: int = 0) -> bool:
    """Return True if session/step image quotas still allow a new dispatch."""
    step = teaching_step or 0
    if step > 0 and _count_active_or_ready(session_id, step) >= _max_per_step():
        return False
    if _count_active_or_ready(session_id, None) >= _max_per_session():
        return False
    return True
