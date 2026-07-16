"""Celery tasks for asynchronous test question and image generation."""

import logging

from celery import shared_task

logger = logging.getLogger("dbt_platform.testing")


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


@shared_task(bind=True, max_retries=5, default_retry_delay=5, queue="interactive-images")
def generate_test_question_image_async(self, question_id: str, slot_kind: str = "interactive"):
    from media_app.concurrency import (
        clear_image_status,
        release_image_slot,
        run_with_image_slot,
        set_image_status,
        try_acquire_image_slot,
    )
    from .models import TestQuestion
    from media_app.services import generate_image as generate_img

    kind = slot_kind if slot_kind in ("interactive", "batch") else "interactive"
    if not try_acquire_image_slot(kind):
        set_image_status(question_id, "queued")
        raise self.retry(countdown=5)

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
            question.image_model = result.get("model", "doubao-seedream-5-0-lite-260128")
            from django.utils import timezone
            question.image_generated_at = timezone.now()
            question.save(update_fields=["temporary_image_url", "image_model", "image_generated_at"])
            clear_image_status(question_id)
    except Exception as exc:
        set_image_status(question_id, "failed")
        raise self.retry(exc=exc) from exc
