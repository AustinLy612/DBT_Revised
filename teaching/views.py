"""Teaching session views — full state machine.

Phases: pre_mood_recording → info_collection → skill_selection →
        rag_retrieval_for_teaching → teaching → completed/stopped_by_risk/user_terminated.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from . import services
from .models import ChatMessage, TeachingSession
from knowledge_base.rag.llm_client import APIError, ConfigurationError
from questionnaire.decorators import profile_required

logger = logging.getLogger("dbt_platform.teaching")


@profile_required
def teaching_home_view(request: HttpRequest) -> HttpResponse:
    """Teaching entry point — shows profile info and session history."""
    profile = request.user.profile
    recent_sessions = TeachingSession.objects.filter(
        user=request.user
    ).order_by("-started_at")[:10]
    return render(request, "teaching/home.html", {
        "profile": profile,
        "recent_sessions": recent_sessions,
    })


@profile_required
def start_session_view(request: HttpRequest) -> HttpResponse:
    """Create a new teaching session starting at pre_mood_recording phase."""
    if request.method != "POST":
        return redirect("teaching:home")

    session = services.create_session(request.user)
    return redirect("teaching:session", session_id=session.session_id)


@profile_required
def session_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Main session page — renders UI based on current phase."""
    session = services.get_session_or_404(session_id, request.user)
    conversation = services.get_conversation_history(session)

    is_terminal = session.status in (
        TeachingSession.Status.COMPLETED,
        TeachingSession.Status.STOPPED_BY_RISK,
        TeachingSession.Status.USER_TERMINATED,
    )

    return render(request, "teaching/session.html", {
        "session": session,
        "conversation": conversation,
        "is_terminal": is_terminal,
        "plan_steps": session.teaching_plan.get("plan_steps", []) if session.teaching_plan else [],
    })


# ═══════════════════════════════════════════════════════════════
# Phase 1: Pre-mood recording
# ═══════════════════════════════════════════════════════════════

@profile_required
def record_pre_mood_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Record the pre-teaching mood and advance to info_collection."""
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = services.get_session_or_404(session_id, request.user)

    # Allow "继续" from info_collection phase (retry after transient API failure)
    if session.phase == TeachingSession.Phase.INFO_COLLECTION:
        try:
            services.run_info_collection(session, request.user)
        except (ConfigurationError, APIError) as exc:
            logger.error("Info collection / skill selection failed for session %s: %s",
                         session.session_id, exc)
            messages.error(request, "AI 技能推荐暂时不可用，请稍后再试。")
            return redirect("teaching:session", session_id=session_id)
        messages.success(request, f"AI 已推荐技能「{session.selected_skill}」，请确认或修改。")
        return redirect("teaching:session", session_id=session_id)

    if session.phase != TeachingSession.Phase.PRE_MOOD_RECORDING:
        messages.warning(request, "当前不在心情记录阶段。")
        return redirect("teaching:session", session_id=session_id)

    try:
        mood_value = int(request.POST.get("mood_value", 3))
    except (ValueError, TypeError):
        mood_value = 3
    mood_value = max(1, min(5, mood_value))
    emoji = request.POST.get("emoji", "").strip()
    note = request.POST.get("note", "").strip()

    services.run_pre_mood(session, request.user, mood_value, emoji, note)

    # Auto-run info collection + skill selection
    try:
        services.run_info_collection(session, request.user)
    except (ConfigurationError, APIError) as exc:
        logger.error("Info collection / skill selection failed for session %s: %s",
                     session.session_id, exc)
        session.status = TeachingSession.Status.USER_TERMINATED
        session.save(update_fields=["status"])
        messages.error(request, "AI 技能推荐暂时不可用，请稍后再试。")
        return redirect("teaching:home")

    messages.success(request, f"AI 已推荐技能「{session.selected_skill}」，请确认或修改。")
    return redirect("teaching:session", session_id=session_id)


# ═══════════════════════════════════════════════════════════════
# Phase 2 → 3: Info collection → Skill selection
# ═══════════════════════════════════════════════════════════════

@profile_required
def confirm_skill_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Confirm or modify the selected skill, then run RAG retrieval + teaching plan."""
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = services.get_session_or_404(session_id, request.user)

    if session.phase != TeachingSession.Phase.SKILL_SELECTION:
        messages.warning(request, "当前不在技能选择阶段。")
        return redirect("teaching:session", session_id=session_id)

    custom_skill = request.POST.get("custom_skill", "").strip()
    if custom_skill:
        session.selected_skill = custom_skill
        session.save(update_fields=["selected_skill"])

    try:
        services.run_teaching_plan(session, request.user)
    except (ConfigurationError, APIError) as exc:
        logger.error("Teaching plan generation failed for session %s: %s", session.session_id, exc)
        messages.error(request, "教学计划生成暂时不可用，请稍后再试。")
        return redirect("teaching:session", session_id=session_id)

    messages.success(request, f"教学计划已生成，开始学习「{session.selected_skill}」。")
    return redirect("teaching:session", session_id=session_id)


# ═══════════════════════════════════════════════════════════════
# Phase 5: Teaching dialogue
# ═══════════════════════════════════════════════════════════════

@profile_required
def send_message_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Receive student message, check risk, generate AI response.

    Returns an HTMX partial for the message exchange.
    """
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)

    session = services.get_session_or_404(session_id, request.user)

    if session.status != TeachingSession.Status.ONGOING:
        return _htmx_error("会话已结束，无法发送消息。")

    student_text = request.POST.get("message", "").strip()
    if not student_text:
        return _htmx_error("消息不能为空。")

    conversation = services.get_conversation_history(session)

    # Risk check
    risk_result = services.process_risk_check(session, request.user, student_text, conversation)
    if risk_result and risk_result.get("should_stop_session"):
        response = HttpResponse(status=HTTPStatus.NO_CONTENT)
        response["HX-Redirect"] = "/risk/popup/"
        return response

    # Generate AI response
    try:
        response_data = services.generate_teaching_response(
            session, request.user, student_text, conversation
        )
    except (ConfigurationError, APIError) as exc:
        logger.error("Teaching response failed for session %s: %s", session.session_id, exc)
        return _htmx_error("AI 教学响应暂时不可用，请稍后再试。")

    # Auto-generate image if the AI provided an image_prompt.
    # Run in background thread to avoid blocking the response (and gunicorn timeouts).
    image_prompt = response_data.get("image_prompt", "").strip()
    if image_prompt:
        _start_image_generation(session, image_prompt)

    # Auto-detect session completion: LLM sent a summary AND all plan
    # steps have been covered, so end the session automatically.
    plan_steps = session.teaching_plan.get("plan_steps", []) if session.teaching_plan else []
    plan_step_count = len(plan_steps)

    if _should_auto_complete(
        response_data.get("message_type", ""),
        response_data.get("content", ""),
        len(conversation),
        plan_step_count,
    ):
        logger.info(
            "Auto-completing session %s: AI sent summary, step %d/%d, %d msgs",
            session.session_id,
            _calc_current_step(len(conversation), plan_step_count),
            plan_step_count,
            len(conversation),
        )
        try:
            full_conversation = services.get_conversation_history(session)
            services.generate_session_summary(session, request.user, full_conversation)
        except (ConfigurationError, APIError) as exc:
            logger.error("Auto-summary generation failed for session %s: %s", session.session_id, exc)
            services.terminate_session(session)

        from mood.services import check_and_award_achievements
        check_and_award_achievements(request.user, event="session_completed")

        response = HttpResponse(status=HTTPStatus.NO_CONTENT)
        response["HX-Redirect"] = str(reverse("teaching:session", kwargs={"session_id": session_id}))
        return response

    new_conversation = services.get_conversation_history(session)

    # If HTMX didn't intercept the request (e.g. CDN blocked), redirect to session page
    if not getattr(request, "htmx", None):
        return redirect("teaching:session", session_id=session_id)

    return render(request, "teaching/messages_partial.html", {
        "conversation": new_conversation,
        "is_terminal": False,
    })


# ═══════════════════════════════════════════════════════════════
# Session end / termination
# ═══════════════════════════════════════════════════════════════

@profile_required
def end_session_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """End the teaching session and generate a summary."""
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = services.get_session_or_404(session_id, request.user)

    if session.status != TeachingSession.Status.ONGOING:
        messages.info(request, "会话已经结束。")
        return redirect("teaching:session", session_id=session_id)

    conversation = services.get_conversation_history(session)

    try:
        services.generate_session_summary(session, request.user, conversation)
        messages.success(request, "教学已完成，摘要已生成。")
    except (ConfigurationError, APIError) as exc:
        logger.error("Summary generation failed for session %s: %s", session.session_id, exc)
        services.terminate_session(session)
        messages.warning(request, "教学摘要生成失败，但会话已结束。")

    # Trigger achievement check after session completion
    from mood.services import check_and_award_achievements
    result = check_and_award_achievements(request.user, event="session_completed")
    if result["newly_unlocked"]:
        messages.success(request, f"🏆 新成就解锁：{'、'.join(result['newly_unlocked'])}")

    # Redirect to post-mood recording (popup flow)
    if not session.post_mood_id:
        return redirect("mood:post_teaching", session_id=session_id)

    return redirect("teaching:session", session_id=session_id)


@profile_required
def terminate_session_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """User-initiated session termination."""
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = services.get_session_or_404(session_id, request.user)

    if session.status != TeachingSession.Status.ONGOING:
        messages.info(request, "会话已经结束。")
        return redirect("teaching:session", session_id=session_id)

    services.terminate_session(session)
    messages.info(request, "教学已终止。")
    return redirect("teaching:session", session_id=session_id)


def _htmx_error(message: str) -> HttpResponse:
    return HttpResponse(f'<div class="text-red-500 text-sm p-3">{message}</div>')


def _calc_current_step(conversation_msg_count: int, plan_step_count: int) -> int:
    """Estimate the current teaching plan step from conversation length.

    Mirrors the logic in services.generate_teaching_response().
    """
    if plan_step_count <= 0:
        return 1
    return min(conversation_msg_count // 2 + 1, plan_step_count)


def _should_auto_complete(
    message_type: str,
    content: str,
    conversation_msg_count: int,
    plan_step_count: int,
) -> bool:
    """Determine whether the session should auto-complete."""
    # Require at least 4 messages (2 exchanges) to avoid premature completion
    if conversation_msg_count < 4:
        return False
    if plan_step_count <= 0:
        # No plan steps: rely on message_type or farewell content
        if message_type in ("总结", "反馈"):
            return True
        return _has_farewell(content)
    current_step = min(conversation_msg_count // 2 + 1, plan_step_count)
    if current_step < plan_step_count:
        return False
    # All steps covered: check for concluding signal
    if message_type in ("总结", "反馈"):
        return True
    return _has_farewell(content)


def _has_farewell(content: str) -> bool:
    """Check if content contains farewell/conclusion patterns."""
    if not content:
        return False
    patterns = [
        "再见", "下周见", "明天见", "下次见",
        "今天就到这里", "今天就到这",
        "我们下次", "期待下次",
        "加油",  # often paired with goodbye
    ]
    # Require at least one goodbye + one closure pattern
    hits = sum(1 for p in patterns if p in content)
    return hits >= 2


def _start_image_generation(session, image_prompt: str) -> None:
    """Generate image in background thread — never blocks the response."""
    import threading

    def _run():
        try:
            from media_app.services import generate_image
            img_result = generate_image(image_prompt, n=1, size="1024x1024")
            if img_result.get("urls"):
                from .models import ChatMessage
                latest = (
                    ChatMessage.objects
                    .filter(session=session, role=ChatMessage.Role.ASSISTANT)
                    .order_by("-created_at")
                    .first()
                )
                if latest:
                    latest.image_url = img_result["urls"][0]
                    latest.save(update_fields=["image_url"])
        except Exception:
            logger.exception("Background image generation failed for session %s", session.session_id)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
