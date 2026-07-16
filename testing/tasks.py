"""Celery tasks for asynchronous test question and image generation."""

from __future__ import annotations

import logging
import time

from celery import shared_task
from django.conf import settings

logger = logging.getLogger("dbt_platform.testing")


def _provider_max_retries() -> int:
    return int(getattr(settings, "IMAGE_PROVIDER_MAX_RETRIES", 3))


@shared_task(bind=True, max_retries=2, default_retry_delay=10, queue="questions")
def generate_test_questions_async(self, test_id: str):
    from .models import Test
    from .services import generate_and_save_questions

    try:
        test = Test.objects.get(test_id=test_id)
    except Test.DoesNotExist:
        logger.error("Test not found for async question generation: %s", test_id)
        return

    try:
        generate_and_save_questions(test, test.user, test.session)
    except Exception as exc:
        test.status = Test.Status.USER_TERMINATED
        test.save(update_fields=["status"])
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=5, queue="interactive-images")
def generate_test_question_image_async(
    self,
    question_id: str,
    slot_kind: str = "interactive",
    wait_started_at: float | None = None,
):
    from media_app.concurrency import (
        clear_image_status,
        mark_image_failed,
        release_image_slot,
        run_with_image_slot,
        set_image_status,
        slot_wait_deadline_passed,
        slot_wait_interval_seconds,
        try_acquire_image_slot,
    )
    from media_app.services import generate_image as generate_img
    from .models import TestQuestion

    kind = slot_kind if slot_kind in ("interactive", "batch") else "interactive"
    queue_name = "batch-images" if kind == "batch" else "interactive-images"
    priority = 1 if kind == "batch" else 7

    if not try_acquire_image_slot(kind):
        set_image_status(question_id, "queued")
        if slot_wait_deadline_passed(wait_started_at):
            mark_image_failed(question_id, "配图排队超时，请稍后重试")
            return
        generate_test_question_image_async.apply_async(
            args=[question_id],
            kwargs={
                "slot_kind": kind,
                "wait_started_at": wait_started_at or time.time(),
            },
            queue=queue_name,
            countdown=slot_wait_interval_seconds(),
            priority=priority,
        )
        return

    try:
        question = TestQuestion.objects.get(question_id=question_id)
    except TestQuestion.DoesNotExist:
        release_image_slot(kind)
        return

    if not question.image_prompt:
        release_image_slot(kind)
        return

    if question.temporary_image_url:
        release_image_slot(kind)
        clear_image_status(question_id)
        return

    try:
        result = run_with_image_slot(
            question_id,
            lambda: generate_img(question.image_prompt),
            kind=kind,
        )
        image_url = result["urls"][0] if result["urls"] else ""
        if image_url:
            question.temporary_image_url = image_url
            question.image_model = result.get("model", "doubao-seedream-5.0-lite")
            from django.utils import timezone
            question.image_generated_at = timezone.now()
            question.save(update_fields=["temporary_image_url", "image_model", "image_generated_at"])
            clear_image_status(question_id)
        else:
            mark_image_failed(question_id, "配图未返回有效图片")
    except Exception as exc:
        if self.request.retries >= _provider_max_retries():
            mark_image_failed(question_id, "配图服务暂时不可用，请稍后重试")
            return
        set_image_status(question_id, "queued")
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries)) from exc
