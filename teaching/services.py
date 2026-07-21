"""Teaching session orchestration services.

Implements the full teaching state machine:
  pre_mood_recording → personal_inquiry → info_collection → skill_selection →
  rag_retrieval_for_teaching → teaching → completed/stopped_by_risk/user_terminated
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from django.db import models
from django.shortcuts import get_object_or_404
from django.utils import timezone

from risk.services import check_keyword_risk  # noqa: F401 — re-exported for callers

logger = logging.getLogger("dbt_platform.teaching")

# Recent completed teachings whose skills should not be re-recommended
# unless the model provides an explicit, valid repeat justification.
RECENT_SKILL_REPEAT_WINDOW = 3

# Fallback module lookup when switching to an alternative skill.
_SKILL_MODULE_HINTS: dict[str, str] = {
    "观察呼吸": "正念",
    "身体扫描": "正念",
    "正念行走": "正念",
    "正念饮食": "正念",
    "正念聆听": "正念",
    "观察-描述-参与": "正念",
    "不评判练习": "正念",
    "情绪命名": "情绪调节",
    "情绪追踪": "情绪调节",
    "相反行动": "情绪调节",
    "ABC情绪分析": "情绪调节",
    "积累积极情绪": "情绪调节",
    "事实核查": "情绪调节",
    "STOP技能": "痛苦耐受",
    "TIP技能": "痛苦耐受",
    "TIP技能（冷水刺激）": "痛苦耐受",
    "转移注意力": "痛苦耐受",
    "自我安抚": "痛苦耐受",
    "自我安抚（五感）": "痛苦耐受",
    "接受现实": "痛苦耐受",
    "危机生存": "痛苦耐受",
    "DEAR MAN沟通法": "人际效能",
    "DEAR MAN": "人际效能",
    "GIVE技巧": "人际效能",
    "GIVE技巧（维护关系）": "人际效能",
    "FAST技巧": "人际效能",
    "FAST技巧（保持自尊）": "人际效能",
    "设置边界": "人际效能",
    "请求练习": "人际效能",
}


def create_session(user: models.Model) -> models.Model:
    """Create a new teaching session starting at pre_mood_recording phase."""
    from .models import TeachingSession

    session = TeachingSession.objects.create(
        user=user,
        phase=TeachingSession.Phase.PRE_MOOD_RECORDING,
        status=TeachingSession.Status.ONGOING,
    )
    logger.info("Created teaching session %s for user %s", session.session_id, user.id)
    return session


def get_session_or_404(session_id: str, user: models.Model) -> models.Model:
    """Get a session by ID, ensuring the requesting user owns it."""
    from .models import TeachingSession

    return get_object_or_404(TeachingSession, session_id=session_id, user=user)


# ═══════════════════════════════════════════════════════════════
# Phase 1: Pre-mood recording
# ═══════════════════════════════════════════════════════════════

def run_pre_mood(
    session: models.Model,
    user: models.Model,
    mood_value: int,
    emoji: str = "",
    note: str = "",
) -> str:
    """Record the pre-teaching mood and advance to personal_inquiry phase.

    Returns the mood_id.
    """
    from mood.models import MoodRecord

    mood = MoodRecord.objects.create(
        user=user,
        session=session,
        mood_value=mood_value,
        emoji=emoji or _emoji_for_value(mood_value),
        note=note,
        context=MoodRecord.Context.BEFORE_TEACHING,
    )

    session.pre_mood_id = mood.mood_id
    session.phase = session.Phase.PERSONAL_INQUIRY
    session.save(update_fields=["pre_mood_id", "phase"])

    logger.info("Pre-mood %s recorded for session %s, value=%d, advancing to personal_inquiry",
                mood.mood_id, session.session_id, mood_value)
    return mood.mood_id


def _emoji_for_value(value: int) -> str:
    emojis = {1: "😫", 2: "😟", 3: "😐", 4: "🙂", 5: "😄"}
    return emojis.get(value, "😐")


# ═══════════════════════════════════════════════════════════════
# Phase 2: Personal inquiry — ask about recent experiences
# ═══════════════════════════════════════════════════════════════

def generate_inquiry_question(
    session: models.Model,
    user: models.Model,
) -> dict[str, Any]:
    """Generate a warm, personalized question asking about the student's recent situation.

    Uses the student's profile and pre-mood to craft an empathetic inquiry.
    Returns a dict with greeting, question, and inquiry_focus.
    """
    from knowledge_base.rag.chains import generate_personal_inquiry

    profile = getattr(user, "profile", None)

    # Read mood value from the session's pre-mood record
    mood_value = 3
    mood_note = ""
    if session.pre_mood_id:
        from mood.models import MoodRecord
        try:
            mood = MoodRecord.objects.get(mood_id=session.pre_mood_id)
            mood_value = mood.mood_value
            mood_note = mood.note or ""
        except MoodRecord.DoesNotExist:
            pass

    result = generate_personal_inquiry(
        profile=profile,
        mood_value=mood_value,
        mood_note=mood_note,
    )

    logger.info("Personal inquiry generated for session %s: focus=%s",
                session.session_id, result.inquiry_focus)
    return result.model_dump()


def run_personal_inquiry(
    session: models.Model,
    user: models.Model,
    personal_context: str,
) -> dict[str, Any]:
    """Store the student's personal context and run info collection + skill selection.

    The personal context becomes the most important input for skill recommendation.

    Returns a dict with the skill selection result.
    """
    session.personal_context = personal_context
    session.save(update_fields=["personal_context"])

    logger.info("Personal context recorded for session %s (%d chars)",
                session.session_id, len(personal_context))

    # Now gather all data and run skill selection
    return run_info_collection(session, user)


# ═══════════════════════════════════════════════════════════════
# Phase 3: Info collection — read questionnaire + history + tests
# ═══════════════════════════════════════════════════════════════

def _session_counts_as_taught(session: models.Model) -> bool:
    """True when a prior session actually entered teaching with a concrete skill."""
    skill = (session.selected_skill or "").strip()
    if not skill:
        return False
    if session.phase == session.Phase.TEACHING:
        return True
    if session.status in (
        session.Status.COMPLETED,
        session.Status.USER_TERMINATED,
        session.Status.STOPPED_BY_RISK,
    ) and session.teaching_plan:
        return True
    return False


def _normalize_skill_name(skill: str) -> str:
    return (skill or "").strip()


def _collect_selection_context(
    session: models.Model,
    user: models.Model,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], list[str], list[str]]:
    """Build teaching history, test stats, and repeat-avoidance lists.

    Returns:
        teaching_history, test_stats, history_skills (recent-first),
        recent_avoid_skills, failed_skills
    """
    from .models import TeachingSession
    from testing.models import Test

    previous_sessions = TeachingSession.objects.filter(
        user=user
    ).exclude(
        session_id=session.session_id
    ).order_by("-started_at")[:20]

    teaching_history: list[dict[str, Any]] = []
    history_skills: list[str] = []
    for s in previous_sessions:
        skill = _normalize_skill_name(s.selected_skill or "")
        entry = {
            "skill": skill,
            "module": s.selected_module or "",
            "status": s.status,
            "phase": s.phase,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "counts_as_taught": _session_counts_as_taught(s),
        }
        teaching_history.append(entry)
        if entry["counts_as_taught"] and skill:
            history_skills.append(skill)

    recent_avoid_skills: list[str] = []
    for skill in history_skills:
        if skill not in recent_avoid_skills:
            recent_avoid_skills.append(skill)
        if len(recent_avoid_skills) >= RECENT_SKILL_REPEAT_WINDOW:
            break

    previous_tests = Test.objects.filter(user=user).order_by("-created_at")[:20]
    test_history: list[dict[str, Any]] = []
    tested_skills: set[str] = set()
    failed_skills: list[str] = []
    for t in previous_tests:
        session_skill = _normalize_skill_name(
            t.session.selected_skill if t.session_id else ""
        )
        if session_skill:
            tested_skills.add(session_skill)
        if session_skill and not t.passed and session_skill not in failed_skills:
            failed_skills.append(session_skill)
        test_history.append({
            "session_skill": session_skill,
            "attempt_no": t.attempt_no,
            "correct_count": t.correct_count,
            "total_questions": t.total_questions,
            "passed": t.passed,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    total_pass = sum(1 for t in previous_tests if t.passed)
    test_stats = {
        "total_tests": len(previous_tests),
        "total_passed": total_pass,
        "pass_rate": round(total_pass / len(previous_tests), 2) if previous_tests else None,
        "tested_skills": sorted(tested_skills),
        "failed_skills": failed_skills,
        "recent_tests": test_history[:10],
    }
    return teaching_history, test_stats, history_skills, recent_avoid_skills, failed_skills


def _apply_repeat_guard(
    result: Any,
    recent_avoid_skills: list[str],
    failed_skills: list[str],
) -> Any:
    """Block unjustified repeats of recent skills; fall back to unlearned alternatives."""
    selected = _normalize_skill_name(getattr(result, "selected_skill", "") or "")
    avoid_set = set(recent_avoid_skills)
    if not selected or selected not in avoid_set:
        return result

    justification = (getattr(result, "repeat_justification", "") or "").strip()
    is_repeat = bool(getattr(result, "is_repeat", False))
    failed_set = set(failed_skills)
    has_valid_exception = is_repeat and bool(justification)
    if not has_valid_exception and selected in failed_set:
        reason = (getattr(result, "reason", "") or "")
        if any(token in reason for token in ("未通过", "薄弱", "巩固", "复训", "未掌握")):
            result.is_repeat = True
            if not justification:
                result.repeat_justification = (
                    f"该技能「{selected}」历史测试未掌握，需要巩固。"
                )
            has_valid_exception = True

    if has_valid_exception:
        if not result.repeat_justification:
            result.repeat_justification = justification
        result.is_repeat = True
        logger.info(
            "Allowing justified skill repeat: %s (%s)",
            selected,
            result.repeat_justification[:120],
        )
        return result

    alternatives = [
        _normalize_skill_name(s)
        for s in (getattr(result, "alternative_skills", None) or [])
        if _normalize_skill_name(s) and _normalize_skill_name(s) not in avoid_set
    ]
    if not alternatives:
        logger.warning(
            "Unjustified repeat of %s with no safe alternative; keeping original",
            selected,
        )
        result.is_repeat = True
        result.repeat_justification = (
            result.repeat_justification
            or "模型推荐了近期已学技能但未给出有效复训理由，且无可用备选。"
        )
        return result

    fallback = alternatives[0]
    original = selected
    result.selected_skill = fallback
    hinted_module = _SKILL_MODULE_HINTS.get(fallback)
    if hinted_module:
        result.selected_module = hinted_module
    result.is_repeat = False
    result.repeat_justification = ""
    note = (
        f"原推荐「{original}」属于近期已学技能且缺少有效复训理由，"
        f"已回退为未学备选「{fallback}」。"
    )
    existing_reason = (getattr(result, "reason", "") or "").strip()
    result.reason = f"{note} {existing_reason}".strip()
    result.alternative_skills = [s for s in alternatives[1:] if s != fallback][:3]
    logger.info("Blocked unjustified skill repeat %s → %s", original, fallback)
    return result


def run_info_collection(session: models.Model, user: models.Model) -> dict[str, Any]:
    """Gather all user context: questionnaire, teaching history, test records.

    After collection, automatically runs skill selection (now informed by
    personal_context from the personal_inquiry phase) and advances
    the session to the skill_selection phase.

    Returns a dict with collected context including test performance data.
    """
    profile = getattr(user, "profile", None)
    profile_dict = None
    if profile:
        profile_dict = {
            "gender": profile.gender,
            "age": profile.age,
            "grade": profile.grade,
            "hobby_tags": profile.hobby_tags,
            "concern_tags": profile.concern_tags,
        }

    teaching_history, test_stats, history_skills, recent_avoid_skills, failed_skills = (
        _collect_selection_context(session, user)
    )

    collected_context = {
        "profile": profile_dict,
        "teaching_history": teaching_history,
        "test_stats": test_stats,
        "recent_avoid_skills": recent_avoid_skills,
    }

    logger.info(
        "Info collection complete for session %s: %d previous teachings, "
        "%d previous tests, avoid=%s",
        session.session_id,
        len([h for h in teaching_history if h.get("counts_as_taught")]),
        test_stats["total_tests"],
        recent_avoid_skills,
    )

    _run_skill_selection_inner(
        session,
        user,
        profile,
        teaching_history,
        test_stats,
        history_skills=history_skills,
        recent_avoid_skills=recent_avoid_skills,
        failed_skills=failed_skills,
    )

    session.phase = session.Phase.SKILL_SELECTION
    session.save(update_fields=["phase", "selected_module", "selected_skill", "selection_reason", "rag_context_ids"])

    return collected_context


# ═══════════════════════════════════════════════════════════════
# Phase 3: Skill selection
# ═══════════════════════════════════════════════════════════════

def _run_skill_selection_inner(
    session: models.Model,
    user: models.Model,
    profile: Any,
    teaching_history: list[dict[str, Any]],
    test_stats: dict[str, Any],
    *,
    history_skills: list[str] | None = None,
    recent_avoid_skills: list[str] | None = None,
    failed_skills: list[str] | None = None,
) -> dict[str, Any]:
    """Core skill selection logic — shared by info_collection and manual calls.

    Uses personal_context (from the personal_inquiry phase) and pre-mood
    as the most important inputs for skill recommendation.
    """
    from knowledge_base.rag.chains import generate_skill_selection
    from knowledge_base.rag.retriever import get_retriever

    if history_skills is None:
        history_skills = [
            h["skill"] for h in teaching_history
            if h.get("skill") and h.get("counts_as_taught", True)
        ]
    if recent_avoid_skills is None:
        recent_avoid_skills = []
        for skill in history_skills:
            if skill not in recent_avoid_skills:
                recent_avoid_skills.append(skill)
            if len(recent_avoid_skills) >= RECENT_SKILL_REPEAT_WINDOW:
                break
    if failed_skills is None:
        failed_skills = list(test_stats.get("failed_skills") or [])
        if not failed_skills:
            failed_skills = [
                t["session_skill"] for t in test_stats.get("recent_tests", [])
                if not t.get("passed") and t.get("session_skill")
            ]
            seen: set[str] = set()
            unique_failed: list[str] = []
            for skill in failed_skills:
                if skill not in seen:
                    seen.add(skill)
                    unique_failed.append(skill)
            failed_skills = unique_failed

    retrieval_query = "DBT具体技能 青少年 正念 情绪调节 痛苦耐受 人际效能"
    if failed_skills:
        retrieval_query += " 薄弱技能:" + ",".join(failed_skills[:3])
    if recent_avoid_skills:
        retrieval_query += " 近期已学请优先其他技能:" + ",".join(recent_avoid_skills)

    mood_value = None
    if session.pre_mood_id:
        from mood.models import MoodRecord
        try:
            mood = MoodRecord.objects.get(mood_id=session.pre_mood_id)
            mood_value = mood.mood_value
        except MoodRecord.DoesNotExist:
            pass

    retriever = get_retriever(k=5, user=user, session=session, use_case="teaching")
    result = generate_skill_selection(
        profile=profile,
        history_skills=history_skills,
        recent_avoid_skills=recent_avoid_skills,
        failed_skills=failed_skills,
        retriever=retriever,
        retrieval_query=retrieval_query,
        personal_context=session.personal_context or "",
        mood_value=mood_value,
    )

    result = _apply_repeat_guard(result, recent_avoid_skills, failed_skills)

    session.selected_module = result.selected_module
    session.selected_skill = result.selected_skill
    reason = result.reason or ""
    if result.is_repeat and result.repeat_justification:
        reason = f"{reason} 【复训理由】{result.repeat_justification}".strip()
    session.selection_reason = reason
    session.rag_context_ids = result.source_chunk_ids
    session.save(update_fields=["selected_module", "selected_skill", "selection_reason", "rag_context_ids"])

    logger.info(
        "Skill selected for session %s: %s (repeat=%s, personal_context_len=%d, avoid=%s)",
        session.session_id,
        session.selected_skill,
        result.is_repeat,
        len(session.personal_context or ""),
        recent_avoid_skills,
    )
    return result.model_dump()


def run_skill_selection(session: models.Model, user: models.Model) -> dict[str, Any]:
    """Public wrapper — run AI skill selection and save results to the session.

    Also reads Test records so skill selection can account for test performance.
    """
    profile = getattr(user, "profile", None)
    teaching_history, test_stats, history_skills, recent_avoid_skills, failed_skills = (
        _collect_selection_context(session, user)
    )
    return _run_skill_selection_inner(
        session,
        user,
        profile,
        teaching_history,
        test_stats,
        history_skills=history_skills,
        recent_avoid_skills=recent_avoid_skills,
        failed_skills=failed_skills,
    )


# ═══════════════════════════════════════════════════════════════
# Phase 4: RAG retrieval for teaching
# ═══════════════════════════════════════════════════════════════

def run_rag_retrieval(session: models.Model, user: models.Model) -> dict[str, Any]:
    """Explicit RAG retrieval step before entering the teaching phase.

    Retrieves relevant knowledge chunks for the selected skill and
    enriches the teaching plan context.
    """
    from knowledge_base.rag.retriever import get_retriever

    retriever = get_retriever(k=8, user=user, session=session, use_case="teaching")
    chunks = retriever.search_with_context(
        query=f"{session.selected_module} {session.selected_skill} 教学方法 步骤",
    )

    if chunks:
        chunk_ids = [c.get("chunk_id", "") for c in chunks if c.get("chunk_id")]
        existing_ids = list(session.rag_context_ids or [])
        for cid in chunk_ids:
            if cid not in existing_ids:
                existing_ids.append(cid)
        session.rag_context_ids = existing_ids
        session.save(update_fields=["rag_context_ids"])

    logger.info(
        "RAG retrieval complete for session %s: %d chunks",
        session.session_id, len(chunks),
    )
    return {"chunks_retrieved": len(chunks), "chunk_ids": session.rag_context_ids}


# ═══════════════════════════════════════════════════════════════
# Phase 5: Teaching plan + transition to teaching
# ═══════════════════════════════════════════════════════════════

def run_teaching_plan(session: models.Model, user: models.Model) -> dict[str, Any]:
    """Generate a teaching plan and advance to the teaching phase.

    Includes the rag_retrieval_for_teaching step as a sub-step.
    After plan generation, pre-fetches RAG context for each plan step
    so per-message retrievals during teaching can use cached context.
    """
    profile = getattr(user, "profile", None)

    # First run RAG retrieval
    run_rag_retrieval(session, user)

    from knowledge_base.rag.chains import generate_teaching_plan
    from knowledge_base.rag.retriever import get_retriever

    retriever = get_retriever(k=5, user=user, session=session, use_case="teaching")
    result = generate_teaching_plan(
        profile=profile,
        selected_skill=session.selected_skill,
        selected_module=session.selected_module,
        retriever=retriever,
    )

    plan_dict = result.model_dump()

    # Pre-fetch RAG context for each plan step
    plan_steps = plan_dict.get("plan_steps", [])
    step_contexts: list[list[dict[str, Any]]] = []
    if plan_steps:
        step_retriever = get_retriever(k=3, user=user, session=session, use_case="teaching")
        skill = session.selected_skill or ""
        for step in plan_steps:
            step_text = step if isinstance(step, str) else str(step)
            query = f"{skill} {step_text}"
            try:
                chunks = step_retriever.search_with_context(query)
                step_contexts.append(chunks)
            except Exception:
                logger.exception("Pre-fetch failed for step: %s", step_text[:60])
                step_contexts.append([])
        plan_dict["step_contexts"] = step_contexts

    session.teaching_plan = plan_dict
    session.phase = session.Phase.TEACHING
    if result.source_chunk_ids:
        existing_ids = list(session.rag_context_ids or [])
        for cid in result.source_chunk_ids:
            if cid not in existing_ids:
                existing_ids.append(cid)
        session.rag_context_ids = existing_ids
    session.save(update_fields=["teaching_plan", "phase", "rag_context_ids"])

    return plan_dict


def _generate_opening_message(
    session: models.Model,
    user: models.Model,
    profile: Any,
) -> str | None:
    """Generate and save the AI's opening teaching message.

    Called automatically when the teaching phase begins, so the student
    sees a warm greeting and skill introduction without having to type
    first.
    """
    from .models import ChatMessage
    from knowledge_base.rag.chains import generate_teaching_opening
    from knowledge_base.rag.retriever import get_retriever

    plan_steps = session.teaching_plan.get("plan_steps", []) if session.teaching_plan else []

    retriever = get_retriever(k=3, user=user, session=session, use_case="teaching")
    result = generate_teaching_opening(
        profile=profile,
        selected_skill=session.selected_skill or "",
        selected_module=session.selected_module or "",
        selection_reason=session.selection_reason or "",
        personal_context=session.personal_context or "",
        teaching_plan_steps=plan_steps,
        retriever=retriever,
    )

    content_dict = result.model_dump()

    ai_msg = ChatMessage.objects.create(
        session=session,
        user=user,
        role=ChatMessage.Role.ASSISTANT,
        content=content_dict["content"],
        image_prompt=content_dict.get("image_prompt", ""),
        teaching_step=1,
    )

    if content_dict.get("source_chunk_ids"):
        existing_ids = list(session.rag_context_ids or [])
        for cid in content_dict["source_chunk_ids"]:
            if cid not in existing_ids:
                existing_ids.append(cid)
        session.rag_context_ids = existing_ids
        session.save(update_fields=["rag_context_ids"])

    logger.info("AI teaching opening generated for session %s: msg=%s",
                session.session_id, ai_msg.message_id)
    return ai_msg.message_id


# ═══════════════════════════════════════════════════════════════
# Teaching dialogue
# ═══════════════════════════════════════════════════════════════

def generate_teaching_response(
    session: models.Model,
    user: models.Model,
    student_message: str,
    conversation_history: list[dict[str, str]],
    *,
    include_risk_assessment: bool = False,
) -> dict[str, Any]:
    """Generate an AI teaching response to the student's message.

    When include_risk_assessment=True, risk fields (risk_level,
    should_stop_session, risk_reasoning) are populated from the same
    LLM call — no separate risk API round-trip is needed.
    """
    from .models import ChatMessage

    ChatMessage.objects.create(
        session=session,
        user=user,
        role=ChatMessage.Role.USER,
        content=student_message,
    )

    plan_steps = session.teaching_plan.get("plan_steps", []) if session.teaching_plan else []
    current_step = 1
    if plan_steps and conversation_history:
        total_msgs = len(conversation_history)
        current_step = min(total_msgs // 2 + 1, len(plan_steps))

    # Use pre-fetched step context when available
    step_contexts = session.teaching_plan.get("step_contexts", []) if session.teaching_plan else []
    prefetched_chunks: list[dict[str, Any]] | None = None
    if step_contexts and 1 <= current_step <= len(step_contexts):
        prefetched_chunks = step_contexts[current_step - 1] or None

    profile = getattr(user, "profile", None)

    from knowledge_base.rag.chains import generate_teaching_content
    from knowledge_base.rag.retriever import get_retriever

    retriever = get_retriever(k=3, user=user, session=session, use_case="teaching")
    result = generate_teaching_content(
        profile=profile,
        selected_skill=session.selected_skill,
        teaching_plan_steps=plan_steps,
        current_step=current_step,
        conversation_history=conversation_history,
        student_message=student_message,
        retriever=retriever,
        include_risk_assessment=include_risk_assessment,
        prefetched_chunks=prefetched_chunks,
    )

    content_dict = result.model_dump()

    ChatMessage.objects.create(
        session=session,
        user=user,
        role=ChatMessage.Role.ASSISTANT,
        content=content_dict["content"],
        image_prompt=content_dict.get("image_prompt", ""),
        teaching_step=current_step,
    )

    if content_dict.get("source_chunk_ids"):
        existing_ids = list(session.rag_context_ids or [])
        for cid in content_dict["source_chunk_ids"]:
            if cid not in existing_ids:
                existing_ids.append(cid)
        session.rag_context_ids = existing_ids
        session.save(update_fields=["rag_context_ids"])

    return content_dict


# ═══════════════════════════════════════════════════════════════
# Risk detection
# ═══════════════════════════════════════════════════════════════

def process_risk_check(
    session: models.Model,
    user: models.Model,
    text: str,
    recent_context: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Run keyword + AI risk assessment on user message.

    Delegates to the centralized risk.services.process_risk_check.
    """
    from risk.services import process_risk_check as _do_check
    return _do_check(session, user, text, recent_context)


# ═══════════════════════════════════════════════════════════════
# Session summary & termination
# ═══════════════════════════════════════════════════════════════

def generate_session_summary(
    session: models.Model,
    user: models.Model,
    conversation_history: list[dict[str, str]],
) -> dict[str, Any]:
    """Generate the teaching summary and mark the session as completed."""
    from .models import ChatMessage

    profile = getattr(user, "profile", None)

    from knowledge_base.rag.chains import generate_teaching_summary
    from knowledge_base.rag.retriever import get_retriever

    retriever = get_retriever(k=3, user=user, session=session, use_case="teaching")
    result = generate_teaching_summary(
        profile=profile,
        skill=session.selected_skill,
        conversation_history=conversation_history,
        retriever=retriever,
    )

    summary_dict = result.model_dump()
    session.teaching_summary = summary_dict.get("summary_text", "")
    if not session.teaching_summary:
        key_points = summary_dict.get("key_points", [])
        session.teaching_summary = "; ".join(key_points)
    session.status = session.Status.COMPLETED
    session.completed_at = timezone.now()
    session.save(update_fields=["teaching_summary", "status", "completed_at"])

    ChatMessage.objects.create(
        session=session,
        user=user,
        role=ChatMessage.Role.SYSTEM,
        content=f"[系统] 教学已完成。摘要：{session.teaching_summary}",
    )

    return summary_dict


def terminate_session(session: models.Model) -> None:
    """User-initiated session termination."""
    from .models import ChatMessage

    session.status = session.Status.USER_TERMINATED
    session.completed_at = timezone.now()
    session.save(update_fields=["status", "completed_at"])


def get_conversation_history(session: models.Model) -> list[dict[str, str]]:
    """Return the full conversation history for a session."""
    from .models import ChatMessage

    messages = ChatMessage.objects.filter(session=session).order_by("created_at")
    return [
        {
            "role": m.role,
            "content": m.content,
            "message_id": m.message_id,
            "image_url": getattr(m, "image_url", ""),
        }
        for m in messages
    ]
