"""Celery tasks for media_app — async image generation via Volcengine Jimeng."""

import logging

from celery import shared_task

logger = logging.getLogger("dbt_platform.media_app")


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def generate_image_async(self, session_id: str, image_prompt: str):
    """Generate an image via Jimeng and attach it to the latest assistant message.

    Runs as a Celery task so gunicorn workers are never blocked by the
    image API call. On success the image_url is written to the most recent
    ChatMessage with role=assistant in the given session.
    """
    from media_app.services import generate_image

    logger.info("Async image generation for session %s: %.80s...", session_id, image_prompt)

    try:
        img_result = generate_image(image_prompt, n=1, aspect_ratio="1:1")
    except Exception as exc:
        logger.exception("Image generation failed for session %s", session_id)
        raise self.retry(exc=exc)

    if not img_result.get("urls"):
        logger.warning("Image generation returned no URLs for session %s", session_id)
        return

    from teaching.models import ChatMessage

    latest = (
        ChatMessage.objects
        .filter(session_id=session_id, role=ChatMessage.Role.ASSISTANT)
        .order_by("-created_at")
        .first()
    )
    if latest:
        latest.image_url = img_result["urls"][0]
        latest.save(update_fields=["image_url"])
        logger.info("Image attached to message %s for session %s", latest.message_id, session_id)
    else:
        logger.warning("No assistant message found for session %s, discarding image", session_id)
