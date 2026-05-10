"""Testing views — test-taking flow with per-question explanations.

Flow:
  1. Teaching completed → user clicks "开始测试"
  2. Test created with 5 AI-generated questions
  3. User answers one question at a time via HTMX
  4. After each answer: immediate correct/wrong + explanation
  5. After all 5: pass/fail determination
  6. If failed: "重测" button (unlimited retests)
  7. User can terminate at any time

All views are decorated with @profile_required.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from . import services
from .models import Test, TestQuestion
from knowledge_base.rag.llm_client import APIError, ConfigurationError
from questionnaire.decorators import profile_required
from teaching.models import TeachingSession

logger = logging.getLogger("dbt_platform.testing")

# ── Option letter mapping (0→A, 1→B, 2→C, 3→D) ──
_OPTION_LETTERS = ["A", "B", "C", "D"]
_LETTER_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}


# ═══════════════════════════════════════════════════════════════
# Test creation
# ═══════════════════════════════════════════════════════════════

@profile_required
def start_test_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Create a new test for a completed teaching session."""
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = get_object_or_404(TeachingSession, session_id=session_id, user=request.user)

    if session.status != TeachingSession.Status.COMPLETED:
        messages.warning(request, "教学未完成，无法开始测试。")
        return redirect("teaching:session", session_id=session_id)

    attempt_no = services.get_retest_attempt_no(session)

    try:
        test = services.create_test(session, request.user, attempt_no=attempt_no)
    except (ConfigurationError, APIError):
        messages.error(request, "AI 测试题生成暂时不可用，请稍后再试。")
        return redirect("teaching:session", session_id=session_id)

    messages.success(request, f"已生成 5 道测试题（第 {attempt_no} 次测试）。")
    return redirect("testing:test", test_id=test.test_id)


# ═══════════════════════════════════════════════════════════════
# Test page
# ═══════════════════════════════════════════════════════════════

@profile_required
def test_view(request: HttpRequest, test_id: str) -> HttpResponse:
    """Main test page — shows questions and handles the test flow."""
    test = services.get_test_or_404(test_id, request.user)
    questions = list(
        TestQuestion.objects.filter(test=test).order_by("created_at")
    )

    is_ongoing = test.status == Test.Status.ONGOING
    is_completed = test.status == Test.Status.COMPLETED
    is_terminated = test.status == Test.Status.USER_TERMINATED

    answered_count = sum(1 for q in questions if q.user_answer)
    all_answered = is_ongoing and len(questions) >= 5 and answered_count >= 5

    # Find the current unanswered question
    current_question = None
    if is_ongoing:
        for q in questions:
            if not q.user_answer:
                current_question = q
                break

    result_data = None
    if is_completed:
        result_data = {
            "correct_count": test.correct_count,
            "total_questions": test.total_questions,
            "passed": test.passed,
            "attempt_no": test.attempt_no,
        }

    return render(request, "testing/test.html", {
        "test": test,
        "questions": questions,
        "current_question": current_question,
        "answered_count": answered_count,
        "total_count": len(questions),
        "all_answered": all_answered,
        "is_completed": is_completed,
        "is_terminated": is_terminated,
        "is_ongoing": is_ongoing,
        "result_data": result_data,
        "option_letters": _OPTION_LETTERS,
        "session": test.session,
    })


# ═══════════════════════════════════════════════════════════════
# Answer submission (HTMX)
# ═══════════════════════════════════════════════════════════════

@profile_required
def answer_question_view(request: HttpRequest, test_id: str) -> HttpResponse:
    """Submit an answer for a question. Returns HTMX partial with result."""
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)

    test = services.get_test_or_404(test_id, request.user)

    if test.status != Test.Status.ONGOING:
        return _htmx_error("测试已结束，无法提交答案。")

    question_id = request.POST.get("question_id", "").strip()
    answer_letter = request.POST.get("answer", "").strip().upper()

    if not question_id:
        return _htmx_error("缺少题目ID。")
    if answer_letter not in ("A", "B", "C", "D"):
        return _htmx_error("请选择一个有效选项。")

    question = get_object_or_404(TestQuestion, question_id=question_id, test=test)

    if question.user_answer:
        return _htmx_error("本题已经作答。")

    # Convert letter to index for risk analysis text
    answer_idx = _LETTER_TO_INDEX.get(answer_letter, 0)
    selected_text = (question.options or [])[answer_idx] if answer_idx < len(question.options or []) else ""

    # Risk check on answer
    risk_context = _get_answer_context(test)
    risk_result = services.process_test_risk(test, request.user, selected_text, risk_context)
    if risk_result and risk_result.get("should_stop_session"):
        response = HttpResponse(status=HTTPStatus.NO_CONTENT)
        response["HX-Redirect"] = "/risk/popup/"
        return response

    result = services.answer_question(question, answer_letter)

    return render(request, "testing/answer_partial.html", {
        "question": question,
        "result": result,
        "option_letters": _OPTION_LETTERS,
    })


def _get_answer_context(test: models.Model) -> list[dict[str, str]]:
    """Get recent answers as context for risk assessment."""
    questions = TestQuestion.objects.filter(
        test=test
    ).exclude(user_answer="").order_by("-created_at")[:3]
    return [{"role": "user", "content": q.question_text} for q in questions]


# ═══════════════════════════════════════════════════════════════
# Test completion
# ═══════════════════════════════════════════════════════════════

@profile_required
def finish_test_view(request: HttpRequest, test_id: str) -> HttpResponse:
    """Finish the test and calculate results."""
    if request.method != "POST":
        return redirect("testing:test", test_id=test_id)

    test = services.get_test_or_404(test_id, request.user)

    if test.status != Test.Status.ONGOING:
        messages.info(request, "测试已经结束。")
        return redirect("testing:test", test_id=test_id)

    # Check all questions answered
    unanswered = TestQuestion.objects.filter(test=test, user_answer="").count()
    if unanswered > 0:
        messages.warning(request, f"还有 {unanswered} 题未作答，请完成所有题目。")
        return redirect("testing:test", test_id=test_id)

    result = services.finish_test(test)

    if result["passed"]:
        messages.success(
            request,
            f"恭喜！你通过了测试（{result['correct_count']}/{result['total_questions']}）。"
        )
    else:
        messages.warning(
            request,
            f"未通过测试（{result['correct_count']}/{result['total_questions']}），"
            f"需要至少答对 4 题。你可以重测。"
        )

    # Trigger achievement check after test completion
    from mood.services import check_and_award_achievements
    ach_result = check_and_award_achievements(request.user, event="test_completed")
    if ach_result["newly_unlocked"]:
        messages.success(request, f"🏆 新成就解锁：{'、'.join(ach_result['newly_unlocked'])}")

    # Redirect to post-test mood recording (popup flow)
    if not test.post_mood_id:
        return redirect("mood:post_test", test_id=test_id)

    return redirect("testing:test", test_id=test_id)


# ═══════════════════════════════════════════════════════════════
# Retest
# ═══════════════════════════════════════════════════════════════

@profile_required
def retest_view(request: HttpRequest, test_id: str) -> HttpResponse:
    """Create a new test (retest) for the same session."""
    if request.method != "POST":
        return redirect("testing:test", test_id=test_id)

    test = services.get_test_or_404(test_id, request.user)
    session = test.session

    attempt_no = services.get_retest_attempt_no(session)

    try:
        new_test = services.create_test(session, request.user, attempt_no=attempt_no)
    except (ConfigurationError, APIError):
        messages.error(request, "AI 测试题生成暂时不可用，请稍后再试。")
        return redirect("testing:test", test_id=test_id)

    messages.success(request, f"已生成新的测试题（第 {attempt_no} 次测试）。")
    return redirect("testing:test", test_id=new_test.test_id)


# ═══════════════════════════════════════════════════════════════
# Termination
# ═══════════════════════════════════════════════════════════════

@profile_required
def terminate_test_view(request: HttpRequest, test_id: str) -> HttpResponse:
    """User-initiated test termination."""
    if request.method != "POST":
        return redirect("testing:test", test_id=test_id)

    test = services.get_test_or_404(test_id, request.user)

    if test.status != Test.Status.ONGOING:
        messages.info(request, "测试已经结束。")
        return redirect("testing:test", test_id=test_id)

    services.terminate_test(test)
    messages.info(request, "测试已终止。")
    return redirect("testing:test", test_id=test_id)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _htmx_error(message: str) -> HttpResponse:
    return HttpResponse(
        f'<div class="text-red-500 text-sm p-3 bg-red-50 rounded-lg">{message}</div>'
    )
