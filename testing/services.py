"""Testing orchestration services.

Implements the test lifecycle:
  1. Test creation (auto-generates 5 questions via RAG)
  2. Per-question answering (immediate explanation)
  3. Test completion (pass/fail determination)
  4. Unlimited retesting (new questions each attempt)
  5. User termination
  6. Risk detection
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import models
from django.shortcuts import get_object_or_404
from django.utils import timezone

from risk.services import check_keyword_risk  # noqa: F401 — re-exported for callers

logger = logging.getLogger("dbt_platform.testing")


def create_test(session: models.Model, user: models.Model, attempt_no: int = 1) -> models.Model:
    """Create a new test for a completed teaching session.

    Creates the Test record immediately.  Question generation is dispatched
    asynchronously via Celery — the caller should call
    ``generate_test_questions_async.delay(test.test_id)`` after this returns.
    """
    from .models import Test

    test = Test.objects.create(
        session=session,
        user=user,
        attempt_no=attempt_no,
        status=Test.Status.ONGOING,
    )
    logger.info("Created test %s (attempt %d) for session %s",
                test.test_id, attempt_no, session.session_id)
    return test


def get_test_or_404(test_id: str, user: models.Model) -> models.Model:
    from .models import Test
    return get_object_or_404(Test, test_id=test_id, user=user)


# ═══════════════════════════════════════════════════════════════
# Question generation
# ═══════════════════════════════════════════════════════════════

def generate_and_save_questions(
    test: models.Model,
    user: models.Model,
    session: models.Model,
) -> list[models.Model]:
    """Generate 5 test questions via RAG and persist them."""
    from .models import TestQuestion
    from knowledge_base.rag.chains import generate_test_questions
    from knowledge_base.rag.retriever import get_retriever

    profile = getattr(user, "profile", None)

    # Gather previous test correct rates for the retriever
    from .models import Test as TestModel
    previous_tests = TestModel.objects.filter(
        user=user, session=session,
    ).exclude(test_id=test.test_id).order_by("-created_at")[:5]
    previous_rates = [
        t.correct_count / max(t.total_questions, 1)
        for t in previous_tests if t.status == TestModel.Status.COMPLETED
    ]

    retrieval_query = _build_retrieval_query(test, session)

    retriever = get_retriever(k=8, user=user, session=session, use_case="test_generation")

    if profile:
        # Gather previously used question stems for this session to avoid repeats
        existing_questions = TestQuestion.objects.filter(
            test__session=session,
            test__status__in=(TestModel.Status.COMPLETED, TestModel.Status.ONGOING),
        ).exclude(test_id=test.test_id)

        # Collect previous answer patterns for the retriever
        _ = list(existing_questions.values_list("question_text", flat=True))

    result = generate_test_questions(
        profile=profile,
        skill=session.selected_skill or "",
        module=session.selected_module or "",
        teaching_summary_key_points=_extract_key_points(session),
        previous_tests_correct_rates=previous_rates if previous_rates else None,
        retriever=retriever,
        retrieval_query=retrieval_query,
    )

    questions_data = result.model_dump()
    rag_chunk_ids = _collect_rag_chunk_ids(questions_data.get("questions", []))

    # Update test RAG context
    if rag_chunk_ids:
        existing_ids = list(test.rag_context_ids or [])
        for cid in rag_chunk_ids:
            if cid not in existing_ids:
                existing_ids.append(cid)
        test.rag_context_ids = existing_ids
        test.save(update_fields=["rag_context_ids"])

    saved_questions = []
    for q_data in questions_data.get("questions", []):
        correct_int = q_data.get("correct_option", 0)
        image_prompt = q_data.get("image_prompt", "").strip()
        saved = TestQuestion.objects.create(
            test=test,
            question_text=q_data.get("question_text", ""),
            options=q_data.get("options", []),
            correct_option=str(correct_int),
            explanation=q_data.get("explanation", ""),
            source_chunk_ids=q_data.get("source_chunk_ids", []),
            image_prompt=image_prompt,
        )
        saved_questions.append(saved)

    logger.info("Generated %d questions for test %s", len(saved_questions), test.test_id)
    return saved_questions


def _extract_key_points(session: models.Model) -> list[str] | None:
    """Extract key points from the teaching session summary."""
    summary = getattr(session, "teaching_summary", None)
    if not summary:
        return None
    if isinstance(summary, str):
        return [s.strip() for s in summary.split(";") if s.strip()]
    return None


def _build_retrieval_query(test: models.Model, session: models.Model) -> str:
    """Build a retrieval query informed by attempt history."""
    query_parts = [
        session.selected_module or "",
        session.selected_skill or "",
        "测试题",
        "情景选择题",
    ]
    if test.attempt_no > 1:
        query_parts.append("不同角度")
        query_parts.append("新题型")
    return " ".join(p for p in query_parts if p)


def _collect_rag_chunk_ids(questions: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for q in questions:
        for cid in q.get("source_chunk_ids", []):
            if cid not in ids:
                ids.append(cid)
    return ids


# ═══════════════════════════════════════════════════════════════
# Answer handling
# ═══════════════════════════════════════════════════════════════

def answer_question(question: models.Model, user_answer: str) -> dict[str, Any]:
    """Save the user's answer to a question and return the result.

    user_answer is a letter (A-D), correct_option is stored as index "0"-"3".
    Converts the letter to index for comparison.
    """
    _LETTER_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}

    answer_letter = user_answer.strip().upper()
    answer_idx = _LETTER_TO_INDEX.get(answer_letter, -1)

    question.user_answer = answer_letter
    question.is_correct = (str(answer_idx) == str(question.correct_option).strip())
    question.save(update_fields=["user_answer", "is_correct"])

    options = question.options or []
    correct_idx = int(question.correct_option) if question.correct_option.isdigit() else -1
    correct_text = options[correct_idx] if 0 <= correct_idx < len(options) else ""

    return {
        "question_id": question.question_id,
        "is_correct": question.is_correct,
        "correct_option": question.correct_option,
        "correct_text": correct_text,
        "user_answer": question.user_answer,
        "explanation": question.explanation,
        "options": options,
        "question_text": question.question_text,
        "temporary_image_url": getattr(question, "temporary_image_url", ""),
    }


# ═══════════════════════════════════════════════════════════════
# Test completion
# ═══════════════════════════════════════════════════════════════

def finish_test(test: models.Model) -> dict[str, Any]:
    """Calculate final results and mark the test as completed.

    Returns a summary dict with pass/fail, correct count, and per-question results.
    """
    from .models import TestQuestion

    questions = list(TestQuestion.objects.filter(test=test).order_by("created_at"))
    correct = sum(1 for q in questions if q.is_correct)
    total = len(questions)
    passed = correct >= 4

    test.correct_count = correct
    test.passed = passed
    test.status = test.Status.COMPLETED
    test.save(update_fields=["correct_count", "passed", "status"])

    logger.info("Test %s completed: %d/%d correct, passed=%s",
                test.test_id, correct, total, passed)

    return {
        "test_id": test.test_id,
        "correct_count": correct,
        "total_questions": total,
        "passed": passed,
        "attempt_no": test.attempt_no,
        "questions": [
            {
                "question_id": q.question_id,
                "question_text": q.question_text,
                "user_answer": q.user_answer,
                "correct_option": q.correct_option,
                "is_correct": q.is_correct,
                "explanation": q.explanation,
                "options": q.options,
            }
            for q in questions
        ],
    }


def terminate_test(test: models.Model) -> None:
    """User-initiated test termination."""
    test.status = test.Status.USER_TERMINATED
    test.save(update_fields=["status"])
    logger.info("Test %s terminated by user", test.test_id)


# ═══════════════════════════════════════════════════════════════
# Risk detection
# ═══════════════════════════════════════════════════════════════

def check_test_risk(text: str) -> tuple[bool, list[str]]:
    """Check if the user's answer text contains high-risk keywords.

    Delegates to the centralized risk.services.check_keyword_risk.
    """
    from risk.services import check_keyword_risk as _check
    return _check(text)


def process_test_risk(
    test: models.Model,
    user: models.Model,
    text: str,
    recent_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Run risk assessment on a test answer. Returns None if no risk.

    Delegates to the centralized risk.services.process_test_risk_check.
    """
    from risk.services import process_test_risk_check as _do_check
    return _do_check(test, user, text, recent_answers)


# ═══════════════════════════════════════════════════════════════
# Retest
# ═══════════════════════════════════════════════════════════════

def get_retest_attempt_no(session: models.Model) -> int:
    """Get the next attempt number for a retest on this session.

    Uses count rather than max(attempt_no) to avoid duplicate attempt_no
    from orphan tests left behind by failed synchronous generation.
    """
    from .models import Test
    return Test.objects.filter(session=session).count() + 1
