"""Teaching session views — full state machine.

Phases: pre_mood_recording → personal_inquiry → info_collection → skill_selection →
        rag_retrieval_for_teaching → teaching → completed/stopped_by_risk/user_terminated.
"""

from __future__ import annotations

import json
import logging
from http import HTTPStatus

from django.contrib import messages
from django.http import StreamingHttpResponse
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import services
from .models import ChatMessage, TeachingSession
from knowledge_base.rag.llm_client import APIError, ConfigurationError
from questionnaire.decorators import profile_required

logger = logging.getLogger("dbt_platform.teaching")


@profile_required
def teaching_home_view(request: HttpRequest) -> HttpResponse:
    """Teaching entry point — shows profile info and session history."""
    profile = getattr(request.user, "profile", None)
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

    # Generate inquiry question for personal_inquiry phase
    inquiry_data = None
    if not is_terminal and session.phase == TeachingSession.Phase.PERSONAL_INQUIRY:
        try:
            inquiry_data = services.generate_inquiry_question(session, request.user)
        except (ConfigurationError, APIError) as exc:
            logger.error("Inquiry question generation failed for session %s: %s",
                         session.session_id, exc)
            inquiry_data = {
                "greeting": "你好！在开始之前，我想先了解一下你的近况。",
                "question": "最近一周，有什么事情让你感到开心或者有压力吗？愿意和我聊聊吗？",
                "inquiry_focus": "近期状态",
            }

    # Generate AI opening message when first entering the teaching phase
    if not is_terminal and session.phase == TeachingSession.Phase.TEACHING and not conversation:
        try:
            services._generate_opening_message(session, request.user, getattr(request.user, "profile", None))
            conversation = services.get_conversation_history(session)
        except (ConfigurationError, APIError) as exc:
            logger.error("Opening message generation failed for session %s: %s",
                         session.session_id, exc)

    # Fetch test records for this session
    tests = []
    if is_terminal:
        from testing.models import Test, TestQuestion
        tests = list(
            Test.objects.filter(session=session).order_by("created_at")
        )
        # Annotate each test with its question count for the template
        for t in tests:
            t._question_count = TestQuestion.objects.filter(test=t).count()

    return render(request, "teaching/session.html", {
        "session": session,
        "conversation": conversation,
        "is_terminal": is_terminal,
        "plan_steps": session.teaching_plan.get("plan_steps", []) if session.teaching_plan else [],
        "inquiry_data": inquiry_data,
        "tests": tests,
    })


# ═══════════════════════════════════════════════════════════════
# Phase 1: Pre-mood recording
# ═══════════════════════════════════════════════════════════════

@profile_required
def record_pre_mood_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Record the pre-teaching mood and advance to personal_inquiry."""
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = services.get_session_or_404(session_id, request.user)

    # Allow retry from info_collection phase (error recovery)
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

    messages.success(request, "心情已记录，请和 AI 教练聊聊你最近的情况吧。")
    return redirect("teaching:session", session_id=session_id)


# ═══════════════════════════════════════════════════════════════
# Phase 2: Personal inquiry — understand recent experiences
# ═══════════════════════════════════════════════════════════════

@profile_required
def personal_inquiry_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Receive the student's personal context and run skill selection.

    GET: redirect to session page (question is shown via template).
    POST: store personal_context, then run info_collection + skill selection.
    """
    if request.method != "POST":
        return redirect("teaching:session", session_id=session_id)

    session = services.get_session_or_404(session_id, request.user)

    if session.phase != TeachingSession.Phase.PERSONAL_INQUIRY:
        messages.warning(request, "当前不在个人情况了解阶段。")
        return redirect("teaching:session", session_id=session_id)

    personal_context = request.POST.get("personal_context", "").strip()
    if not personal_context:
        messages.warning(request, "请分享一些你最近的经历或感受。")
        return redirect("teaching:session", session_id=session_id)

    try:
        services.run_personal_inquiry(session, request.user, personal_context)
    except (ConfigurationError, APIError) as exc:
        logger.error("Info collection / skill selection failed for session %s: %s",
                     session.session_id, exc)
        session.phase = TeachingSession.Phase.INFO_COLLECTION
        session.save(update_fields=["phase"])
        messages.error(request, "AI 技能推荐暂时不可用，请稍后再试。")
        return redirect("teaching:session", session_id=session_id)

    messages.success(request, f"AI 已根据你的情况推荐技能「{session.selected_skill}」，请确认或修改。")
    return redirect("teaching:session", session_id=session_id)


# ═══════════════════════════════════════════════════════════════
# Phase 3 → 4: Info collection → Skill selection
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

    # ── Risk check ──
    # Fast keyword check always runs first.
    # If keywords trigger → separate AI risk call (safety-critical path).
    # If keywords don't trigger → risk assessment is merged into the teaching
    #   LLM call below, saving one API round-trip per message.
    from risk.services import check_keyword_risk

    keyword_triggered, _keywords = check_keyword_risk(student_text)

    if keyword_triggered:
        risk_result = services.process_risk_check(session, request.user, student_text, conversation)
        if risk_result and risk_result.get("should_stop_session"):
            response = HttpResponse(status=HTTPStatus.NO_CONTENT)
            response["HX-Redirect"] = "/risk/popup/"
            return response

    # ── Generate AI response (with embedded risk assessment when safe) ──
    try:
        response_data = services.generate_teaching_response(
            session, request.user, student_text, conversation,
            include_risk_assessment=not keyword_triggered,
        )
    except (ConfigurationError, APIError) as exc:
        logger.error("Teaching response failed for session %s: %s", session.session_id, exc)
        return _htmx_error("AI 教学响应暂时不可用，请稍后再试。")

    # ── Handle risk from merged response ──
    if not keyword_triggered and response_data.get("should_stop_session"):
        from risk.services import create_risk_event, stop_session_for_risk

        create_risk_event(
            user=request.user,
            session=session,
            trigger_text=student_text,
            detection_source="ai",
            action_taken=response_data.get("risk_reasoning", ""),
            session_stopped=True,
            follow_up_mode="onsite_manual_followup",
        )
        stop_session_for_risk(session, request.user)
        response = HttpResponse(status=HTTPStatus.NO_CONTENT)
        response["HX-Redirect"] = "/risk/popup/"
        return response

    # Auto-generate image if the AI provided an image_prompt.
    # Never let image dispatch failures break the text teaching response.
    image_prompt = response_data.get("image_prompt", "").strip()
    if image_prompt:
        ai_msg = (
            ChatMessage.objects.filter(session=session, role=ChatMessage.Role.ASSISTANT)
            .order_by("-created_at").first()
        )
        if ai_msg:
            try:
                _start_image_generation(session, image_prompt, ai_msg.message_id)
            except Exception:
                logger.exception(
                    "Teaching image dispatch failed for session %s message %s",
                    session.session_id,
                    ai_msg.message_id,
                )

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


@profile_required
def stream_message_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Stream AI teaching response via Server-Sent Events.

    Returns a StreamingHttpResponse with text/event-stream content type.
    Frontend consumes SSE events and renders tokens as they arrive.
    """
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)

    session = services.get_session_or_404(session_id, request.user)

    if session.status != TeachingSession.Status.ONGOING:
        return HttpResponse(
            "data: {\"type\": \"error\", \"message\": \"会话已结束\"}\n\n",
            content_type="text/event-stream",
        )

    student_text = request.POST.get("message", "").strip()
    if not student_text:
        return HttpResponse(
            "data: {\"type\": \"error\", \"message\": \"消息不能为空\"}\n\n",
            content_type="text/event-stream",
        )

    # ── Risk keyword check ──
    from risk.services import check_keyword_risk
    keyword_triggered, _keywords = check_keyword_risk(student_text)
    if keyword_triggered:
        return HttpResponse(
            "data: {\"type\": \"error\", \"message\": \"消息包含敏感内容，请重新表述\"}\n\n",
            content_type="text/event-stream",
        )

    # ── Create user message ──
    ChatMessage.objects.create(
        session=session,
        user=request.user,
        role=ChatMessage.Role.USER,
        content=student_text,
    )

    # ── Gather context ──
    conversation = services.get_conversation_history(session)
    plan_steps = session.teaching_plan.get("plan_steps", []) if session.teaching_plan else []
    current_step = 1
    if plan_steps and conversation:
        total_msgs = len(conversation)
        current_step = min(total_msgs // 2 + 1, len(plan_steps))

    step_contexts = session.teaching_plan.get("step_contexts", []) if session.teaching_plan else []
    prefetched_chunks = None
    if step_contexts and 1 <= current_step <= len(step_contexts):
        prefetched_chunks = step_contexts[current_step - 1] or None

    profile = getattr(request.user, "profile", None)

    from knowledge_base.rag.chains import stream_teaching_content
    from knowledge_base.rag.retriever import get_retriever

    retriever = get_retriever(k=3, user=request.user, session=session, use_case="teaching")

    def _sse_generator():
        """Generate SSE events from the streaming chain."""
        try:
            stream = stream_teaching_content(
                profile=profile,
                selected_skill=session.selected_skill,
                teaching_plan_steps=plan_steps,
                current_step=current_step,
                conversation_history=conversation,
                student_message=student_text,
                retriever=retriever,
                prefetched_chunks=prefetched_chunks,
            )

            for event in stream:
                if event.get("type") == "done":
                    # Save assistant ChatMessage
                    tc = event.get("teaching_content", {})
                    ai_msg = ChatMessage.objects.create(
                        session=session,
                        user=request.user,
                        role=ChatMessage.Role.ASSISTANT,
                        content=tc.get("content", ""),
                        image_prompt=tc.get("image_prompt", ""),
                        teaching_step=current_step,
                    )
                    event["teaching_content"]["message_id"] = ai_msg.message_id

                    image_prompt = tc.get("image_prompt", "").strip()
                    if image_prompt:
                        try:
                            _start_image_generation(session, image_prompt, ai_msg.message_id)
                        except Exception:
                            logger.exception(
                                "Streaming image dispatch failed for session %s message %s",
                                session.session_id,
                                ai_msg.message_id,
                            )

                    # Update RAG context IDs
                    source_ids = tc.get("source_chunk_ids", [])
                    if source_ids:
                        existing_ids = list(session.rag_context_ids or [])
                        for cid in source_ids:
                            if cid not in existing_ids:
                                existing_ids.append(cid)
                        session.rag_context_ids = existing_ids
                        session.save(update_fields=["rag_context_ids"])

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        except Exception as exc:
            logger.exception("Streaming failed for session %s", session.session_id)
            yield f"data: {{\"type\": \"error\", \"message\": \"{str(exc)}\"}}\n\n"

    response = StreamingHttpResponse(
        _sse_generator(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _start_image_generation(session, image_prompt: str, message_id: str) -> None:
    from media_app.tasks import dispatch_teaching_image
    dispatch_teaching_image(session.session_id, image_prompt, message_id)


def _image_failed_html(message: str, retry_url: str) -> HttpResponse:
    return HttpResponse(
        '<div class="mt-2 p-3 bg-red-50 border border-red-200 rounded-lg text-center">'
        f'<p class="text-xs text-red-600 mb-2">{message}</p>'
        f'<button hx-post="{retry_url}" hx-swap="outerHTML" hx-target="closest div" '
        'class="text-xs text-purple-600 hover:text-purple-800 underline">'
        '重新生成配图</button></div>'
    )


@profile_required
def message_image_status_view(request: HttpRequest, message_id: str) -> HttpResponse:
    from media_app.concurrency import get_image_error, get_image_status

    message = get_object_or_404(ChatMessage, message_id=message_id, user=request.user)
    if message.image_url:
        return HttpResponse(
            '<div class="mt-2 flex justify-center">'
            f'<img src="{message.image_url}" alt="教学配图" loading="lazy" '
            'class="rounded-lg shadow border border-gray-200 max-w-full max-w-lg">'
            "</div>"
        )
    status = get_image_status(message_id)
    if status == "failed":
        retry_url = reverse("teaching:retry_message_image", kwargs={"message_id": message_id})
        return _image_failed_html(get_image_error(message_id) or "配图失败，请稍后重试", retry_url)
    return _teaching_message_polling_html(message_id)


@profile_required
def retry_message_image_view(request: HttpRequest, message_id: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)
    from media_app.concurrency import clear_image_status

    message = get_object_or_404(ChatMessage, message_id=message_id, user=request.user)
    if not message.image_prompt:
        return _htmx_error("无法重试：缺少图片描述。")
    clear_image_status(message_id)
    message.image_url = ""
    message.save(update_fields=["image_url"])
    try:
        _start_image_generation(message.session, message.image_prompt, message.message_id)
    except Exception:
        logger.exception("Retry teaching image failed for message %s", message_id)
        return _htmx_error("配图重试失败，请稍后再试。")
    return _teaching_message_polling_html(message_id)


@profile_required
def generate_scene_image_view(request: HttpRequest, session_id: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)
    session = services.get_session_or_404(session_id, request.user)
    prompt = request.POST.get("prompt", "").strip()
    if not prompt:
        prompt = f"DBT技能教学情景图：{session.selected_skill or '正念练习'}"
    from media_app.tasks import dispatch_scene_image
    job_id = dispatch_scene_image(session.session_id, prompt)
    return _teaching_scene_polling_html(session.session_id, job_id)


@profile_required
def scene_image_status_view(request: HttpRequest, session_id: str) -> HttpResponse:
    from media_app.concurrency import get_image_error, get_image_status
    from media_app.tasks import get_scene_image_url

    services.get_session_or_404(session_id, request.user)
    job_id = request.GET.get("job_id", "").strip()
    image_url = get_scene_image_url(session_id, job_id or None)
    if image_url:
        return HttpResponse(
            f'<img src="{image_url}" alt="教学配图" class="w-full rounded-lg shadow" loading="lazy">'
        )
    if not job_id:
        return HttpResponse(
            '<div class="p-3 bg-gray-50 border border-gray-200 rounded-lg text-center">'
            '<span class="text-xs text-gray-500">暂无配图任务</span></div>'
        )
    resource_id = f"scene:{session_id}:{job_id}"
    if get_image_status(resource_id) == "failed":
        retry_url = (
            reverse("teaching:retry_scene_image", kwargs={"session_id": session_id})
            + f"?job_id={job_id}"
        )
        return _image_failed_html(
            get_image_error(resource_id) or "配图失败，请稍后重试",
            retry_url,
        )
    return _teaching_scene_polling_html(session_id, job_id)


@profile_required
def retry_scene_image_view(request: HttpRequest, session_id: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)
    from media_app.concurrency import clear_image_status
    from media_app.tasks import dispatch_scene_image, get_scene_prompt

    services.get_session_or_404(session_id, request.user)
    old_job_id = request.GET.get("job_id", "").strip() or request.POST.get("job_id", "").strip()
    prompt = get_scene_prompt(session_id, old_job_id) if old_job_id else ""
    if not prompt:
        prompt = request.POST.get("prompt", "").strip()
    if not prompt:
        return _htmx_error("无法重试：缺少图片描述。")
    if old_job_id:
        clear_image_status(f"scene:{session_id}:{old_job_id}")
    job_id = dispatch_scene_image(session_id, prompt)
    return _teaching_scene_polling_html(session_id, job_id)


def _teaching_message_polling_html(message_id: str) -> HttpResponse:
    from media_app.concurrency import get_image_status, wait_label_for_status
    label = wait_label_for_status(get_image_status(message_id))
    poll_url = reverse("teaching:message_image_status", kwargs={"message_id": message_id})
    return HttpResponse(
        '<div class="mt-2 p-3 bg-purple-50 border border-purple-200 rounded-lg text-center"'
        f' hx-get="{poll_url}" hx-trigger="every 3s" hx-swap="outerHTML">'
        '<div class="inline-block w-4 h-4 border-2 border-purple-200 '
        'border-t-purple-500 rounded-full animate-spin"></div>'
        f'<span class="text-xs text-purple-600 ml-2">{label}</span></div>'
    )


def _teaching_scene_polling_html(session_id: str, job_id: str) -> HttpResponse:
    from media_app.concurrency import get_image_status, wait_label_for_status
    label = wait_label_for_status(get_image_status(f"scene:{session_id}:{job_id}"))
    poll_url = (
        reverse("teaching:scene_image_status", kwargs={"session_id": session_id})
        + f"?job_id={job_id}"
    )
    return HttpResponse(
        '<div class="p-3 bg-purple-50 border border-purple-200 rounded-lg text-center"'
        f' data-job-id="{job_id}"'
        f' hx-get="{poll_url}" hx-trigger="every 3s" hx-swap="outerHTML">'
        '<div class="inline-block w-4 h-4 border-2 border-purple-200 '
        'border-t-purple-500 rounded-full animate-spin"></div>'
        f'<span class="text-xs text-purple-600 ml-2">{label}</span></div>'
    )

