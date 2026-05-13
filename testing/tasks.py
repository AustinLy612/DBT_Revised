"""Celery tasks for asynchronous test question and image generation."""

import logging

from celery import shared_task

logger = logging.getLogger("dbt_platform.testing")


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def generate_test_questions_async(self, test_id: str):
    """Generate 5 test questions via RAG + LLM in the background.

    After questions are saved, dispatches image generation tasks for
    any questions that have an image_prompt.
    """
    from .models import Test, TestQuestion
    from .services import generate_and_save_questions

    try:
        test = Test.objects.get(test_id=test_id)
    except Test.DoesNotExist:
        logger.error("Test not found for async question generation: %s", test_id)
        return

    try:
        saved_questions = generate_and_save_questions(test, test.user, test.session)
        logger.info("Async question generation complete for test %s", test_id)

        # Auto-dispatch image generation with staggered countdown to avoid
        # MiniMax API rate-limiting (5 simultaneous requests → HTTP 429).
        for i, q in enumerate(saved_questions):
            if q.image_prompt:
                generate_test_question_image_async.apply_async(
                    args=[q.question_id],
                    countdown=i * 3,
                )
                logger.info("Dispatched image gen for question %s (countdown=%ds)",
                           q.question_id, i * 3)

    except Exception as exc:
        logger.error("Async question generation failed for test %s: %s", test_id, exc)
        test.status = Test.Status.USER_TERMINATED
        test.save(update_fields=["status"])
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def generate_test_question_image_async(self, question_id: str):
    """Generate an illustration image for a test question via MiniMax API.

    Updates the TestQuestion with the image URL on success.
    """
    from .models import TestQuestion
    from media_app.services import generate_image as generate_img

    try:
        question = TestQuestion.objects.get(question_id=question_id)
    except TestQuestion.DoesNotExist:
        logger.error("TestQuestion not found for image gen: %s", question_id)
        return

    if not question.image_prompt:
        return

    try:
        result = generate_img(question.image_prompt)
        image_url = result["urls"][0] if result["urls"] else ""

        if image_url:
            question.temporary_image_url = image_url
            question.image_model = result.get("model", "image-01-live")
            from django.utils import timezone
            question.image_generated_at = timezone.now()
            question.save(update_fields=[
                "temporary_image_url", "image_model", "image_generated_at",
            ])
            logger.info("Image generated for question %s: %.80s...",
                        question_id, image_url)
    except Exception as exc:
        logger.error("Image generation failed for question %s: %s",
                     question_id, exc)
        raise self.retry(exc=exc)
