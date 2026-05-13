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
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import services
from .models import Test, TestQuestion
from .tasks import generate_test_questions_async, generate_test_question_image_async
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
    """Create a new test for a completed teaching session.

    Creates the Test record immediately and dispatches question generation
    to a Celery worker so the request never blocks on the LLM call.
    """
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = get_object_or_404(TeachingSession, session_id=session_id, user=request.user)

    if session.status != TeachingSession.Status.COMPLETED:
        messages.warning(request, "教学未完成，无法开始测试。")
        return redirect("teaching:session", session_id=session_id)

    attempt_no = services.get_retest_attempt_no(session)
    test = services.create_test(session, request.user, attempt_no=attempt_no)

    # Dispatch async question generation via Celery
    generate_test_questions_async.delay(test.test_id)

    messages.success(request, f"测试已创建，正在生成题目（第 {attempt_no} 次测试）。")
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

    # Detect orphan tests: created > 5min ago but with 0 questions
    from datetime import timedelta
    from django.utils import timezone
    is_stuck = is_ongoing and len(questions) == 0 and \
        (timezone.now() - test.created_at) > timedelta(minutes=5)

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
        "is_stuck": is_stuck,
        "result_data": result_data,
        "option_letters": _OPTION_LETTERS,
        "session": test.session,
    })


# ═══════════════════════════════════════════════════════════════
# Question generation polling (HTMX)
# ═══════════════════════════════════════════════════════════════

@profile_required
def poll_questions_view(request: HttpRequest, test_id: str) -> HttpResponse:
    """HTMX polling endpoint — checks if test questions are ready."""
    test = services.get_test_or_404(test_id, request.user)

    question_count = TestQuestion.objects.filter(test=test).count()

    if question_count >= 5:
        response = HttpResponse(status=HTTPStatus.NO_CONTENT)
        response["HX-Redirect"] = reverse("testing:test", kwargs={"test_id": test_id})
        return response

    if test.status == Test.Status.USER_TERMINATED:
        session_url = reverse("teaching:session", kwargs={"session_id": test.session_id})
        return HttpResponse(
            '<div class="bg-white border rounded-lg p-6 text-center">'
            '<p class="text-red-600 mb-3">题目生成失败，请重试。</p>'
            f'<a href="{session_url}" '
            'class="text-sm text-blue-600 hover:text-blue-800">返回教学会话</a>'
            "</div>"
        )

    poll_url = reverse("testing:poll", kwargs={"test_id": test_id})
    return HttpResponse(
        '<div class="bg-white border rounded-lg p-6 text-center" '
        f'hx-get="{poll_url}" hx-trigger="every 2s" hx-swap="outerHTML">'
        '<div class="inline-block w-8 h-8 border-4 border-blue-200 border-t-blue-600 '
        'rounded-full animate-spin mb-3"></div>'
        '<p class="text-gray-600">正在生成测试题，请稍候...</p>'
        '<p class="text-xs text-gray-400 mt-1">AI 正在根据教学内容为你出题</p>'
        "</div>"
    )


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
    new_test = services.create_test(session, request.user, attempt_no=attempt_no)

    generate_test_questions_async.delay(new_test.test_id)

    messages.success(request, f"测试已创建，正在生成题目（第 {attempt_no} 次测试）。")
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
# Image generation for test questions (async via Celery)
# ═══════════════════════════════════════════════════════════════

@profile_required
def generate_question_image_view(request: HttpRequest, question_id: str) -> HttpResponse:
    """Dispatch async image generation for a test question.

    POST only. Updates image_prompt if a custom prompt is provided
    (fallback for questions without LLM-generated image_prompt),
    then dispatches a Celery task. Returns a spinner that polls
    until the image is ready.
    """
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)

    question = get_object_or_404(TestQuestion, question_id=question_id)

    custom_prompt = request.POST.get("prompt", "").strip()
    if custom_prompt and not question.image_prompt:
        question.image_prompt = custom_prompt
        question.save(update_fields=["image_prompt"])

    # Fallback: construct a prompt from the question text
    if not question.image_prompt:
        truncated = question.question_text[:200] if question.question_text else ""
        if truncated:
            question.image_prompt = f"DBT正念技能教学情景配图：{truncated}，温暖插画风格"
            question.save(update_fields=["image_prompt"])
        else:
            return _htmx_error("无法生成配图：缺少图片描述。")

    # Dispatch Celery task if no image yet
    if not question.temporary_image_url:
        generate_test_question_image_async.delay(question_id)

    return _image_polling_html(question_id)


@profile_required
def question_image_status_view(request: HttpRequest, question_id: str) -> HttpResponse:
    """HTMX polling endpoint — returns image HTML when ready, or spinner."""
    question = get_object_or_404(TestQuestion, question_id=question_id)

    if question.temporary_image_url:
        return HttpResponse(
            '<div class="mb-4">'
            f'<img src="{question.temporary_image_url}" alt="题目配图" '
            'class="w-full max-w-md rounded-lg shadow" loading="lazy">'
            '<button onclick="DBT_Image.generate(\''
            f'{question.image_prompt.replace(chr(39), "&#39;")}'
            '\', \'question-image-area\', '
            '{source: \'test_illustration\', '
            f'test_question_id: \'{question_id}\'}})" '
            'class="mt-2 text-xs text-purple-500 hover:text-purple-700 underline">'
            '🔄 重新生成配图'
            '</button>'
            '</div>'
        )

    return _image_polling_html(question_id)


def _image_polling_html(question_id: str) -> HttpResponse:
    """Return a spinner div that polls the image-status endpoint."""
    poll_url = reverse("testing:question_image_status", kwargs={"question_id": question_id})
    return HttpResponse(
        '<div class="mb-4 p-4 bg-purple-50 border border-purple-200 rounded-lg text-center"'
        f' hx-get="{poll_url}" hx-trigger="every 3s" hx-swap="outerHTML">'
        '<div class="inline-block w-4 h-4 border-2 border-purple-200 '
        'border-t-purple-500 rounded-full animate-spin"></div>'
        '<span class="text-xs text-purple-600 ml-2">情景配图生成中...</span>'
        '</div>'
    )


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _htmx_error(message: str) -> HttpResponse:
    return HttpResponse(
        f'<div class="text-red-500 text-sm p-3 bg-red-50 rounded-lg">{message}</div>'
    )
