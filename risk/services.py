"""Centralized risk detection and handling services.

Provides unified keyword lists, detection functions, and risk event
creation shared by teaching and testing modules.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import models
from django.utils import timezone

logger = logging.getLogger("dbt_platform.risk")

HIGH_RISK_KEYWORDS: list[str] = [
    "自杀", "自伤", "自残", "想死", "不想活", "不想活了",
    "割腕", "跳楼", "上吊", "安眠药", "结束生命",
    "杀死自己", "伤害自己", "活不下去", "死了算了",
    "去死", "死掉", "不想存在", "消失算了",
]

MODERATE_RISK_KEYWORDS: list[str] = [
    "绝望", "毫无希望", "没有意义", "活着没意义",
    "我想伤害", "我想杀人", "我要杀",
]

_MODERATE_CONCERN_INDICATORS: list[str] = [
    "活得没意义", "存在没意义", "我恨我", "讨厌自己", "伤害我",
]


def check_keyword_risk(text: str) -> tuple[bool, list[str]]:
    """Check if text contains risk keywords (both high and moderate).

    Returns (is_triggered, keywords_found).
    """
    triggered: list[str] = []
    for kw in HIGH_RISK_KEYWORDS:
        if kw in text:
            triggered.append(kw)
    for kw in MODERATE_RISK_KEYWORDS:
        if kw in text:
            triggered.append(kw)
    return bool(triggered), triggered


def has_moderate_concern(text: str) -> bool:
    """Check for moderate concern indicators that warrant AI assessment.

    These are expressions that don't match explicit keyword lists but
    still suggest emotional distress that should be evaluated.
    """
    return any(ind in text for ind in _MODERATE_CONCERN_INDICATORS)


def should_assess_risk(text: str) -> bool:
    """Determine if AI risk assessment should run for this text.

    Returns True if keywords triggered OR moderate concern indicators found.
    Normal, non-concerning text returns False (no AI call needed).
    """
    triggered, _ = check_keyword_risk(text)
    return triggered or has_moderate_concern(text)


def _classify_detection_source(keyword_triggered: bool, ai_risk_level: str) -> str:
    """Determine detection source based on which channels flagged concern."""
    if keyword_triggered and ai_risk_level == "高":
        return "both"
    if ai_risk_level == "高":
        return "ai"
    return "keyword"


def create_risk_event(
    user: models.Model,
    session: models.Model,
    trigger_text: str,
    detection_source: str = "keyword",
    action_taken: str = "",
    session_stopped: bool = True,
    follow_up_mode: str = "onsite_manual_followup",
) -> models.Model:
    """Create a RiskEvent record and return it."""
    from .models import RiskEvent

    event = RiskEvent.objects.create(
        user=user,
        session=session,
        trigger_text=trigger_text,
        detection_source=detection_source,
        action_taken=action_taken,
        session_stopped=session_stopped,
        follow_up_mode=follow_up_mode,
    )
    logger.info("RiskEvent %s created for user %s (source=%s, stopped=%s)",
                event.risk_event_id, user.id, detection_source, session_stopped)
    return event


def stop_session_for_risk(
    session: models.Model,
    user: models.Model,
) -> None:
    """Stop a teaching session due to risk detection.

    Sets status → STOPPED_BY_RISK, sets completed_at, creates system message.
    """
    from teaching.models import ChatMessage, TeachingSession

    session.status = TeachingSession.Status.STOPPED_BY_RISK
    session.completed_at = timezone.now()
    session.save(update_fields=["status", "completed_at"])

    ChatMessage.objects.create(
        session=session,
        user=user,
        role=ChatMessage.Role.SYSTEM,
        content=(
            "[系统] 检测到高风险内容，会话已自动中止。"
            "如需帮助，请联系专业人士。"
        ),
    )
    logger.info("Session %s stopped by risk", session.session_id)


def process_risk_check(
    session: models.Model,
    user: models.Model,
    text: str,
    recent_context: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Run dual-channel (keyword + AI semantic) risk assessment on every message.

    Both channels always run independently:
    - Keyword channel: fast string matching against HIGH/MODERATE keyword lists.
    - AI semantic channel: LLM-based assessment for expressions that keywords miss.

    Returns None only when BOTH channels find no concern.  Otherwise returns
    the risk dict and creates a RiskEvent.  If either channel flags
    should_stop_session the teaching session is stopped immediately.
    """
    triggered, keywords = check_keyword_risk(text)

    from knowledge_base.rag.chains import run_risk_assessment
    from knowledge_base.rag.llm_client import APIError

    ai_available = True
    try:
        result = run_risk_assessment(
            user_message=text,
            recent_context=recent_context,
            triggered_keywords=keywords,
        )
    except APIError as exc:
        logger.error("AI risk assessment failed, falling back to keyword-only: %s", exc)
        ai_available = False
    except Exception as exc:
        logger.exception("Unexpected error in AI risk assessment: %s", exc)
        ai_available = False

    if ai_available:
        risk_dict = result.model_dump()
        ai_risk_level = result.risk_level
        should_stop = result.should_stop_session
    else:
        # AI unavailable — fall back to keyword-only assessment.
        # Conservative: treat any high-risk keyword match as a stop condition.
        has_high_risk = any(kw in text for kw in HIGH_RISK_KEYWORDS)
        risk_dict = {"risk_level": "高" if has_high_risk else "中", "should_stop_session": has_high_risk, "follow_up_action": ("停止教学，引导寻求线下帮助" if has_high_risk else "建议关注"), "reasoning": "AI 风险评估不可用，基于关键词评估"}
        ai_risk_level = "无"  # AI did not contribute; detection source stays keyword
        should_stop = has_high_risk

    # Only skip when both channels report no concern
    if not triggered and ai_risk_level == "无":
        return None

    detection_source = _classify_detection_source(triggered, ai_risk_level)

    create_risk_event(
        user=user,
        session=session,
        trigger_text=text,
        detection_source=detection_source,
        action_taken=risk_dict.get("follow_up_action", ""),
        session_stopped=should_stop,
        follow_up_mode="onsite_manual_followup" if should_stop else "no_action",
    )

    if should_stop:
        stop_session_for_risk(session, user)

    return risk_dict


def process_test_risk_check(
    test: models.Model,
    user: models.Model,
    text: str,
    recent_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Run keyword + AI risk assessment on a test answer.

    Args:
        test: The active test.
        user: The current user.
        text: The selected answer text.
        recent_answers: Recent answer context for AI.

    Returns:
        Risk dict if risk was assessed, None if no concern detected.
    """
    triggered, keywords = check_keyword_risk(text)

    from knowledge_base.rag.chains import run_risk_assessment
    from knowledge_base.rag.llm_client import APIError

    ai_available = True
    try:
        result = run_risk_assessment(
            user_message=text,
            recent_context=recent_answers,
            triggered_keywords=keywords,
        )
    except APIError as exc:
        logger.error("AI risk assessment failed in testing, falling back to keyword-only: %s", exc)
        ai_available = False
    except Exception as exc:
        logger.exception("Unexpected error in AI risk assessment (testing): %s", exc)
        ai_available = False

    if ai_available:
        risk_dict = result.model_dump()
        ai_risk_level = result.risk_level
        should_stop = result.should_stop_session
    else:
        # AI unavailable — fall back to keyword-only assessment.
        # Conservative: treat any high-risk keyword match as a stop condition.
        has_high_risk = any(kw in text for kw in HIGH_RISK_KEYWORDS)
        risk_dict = {"risk_level": "高" if has_high_risk else "中", "should_stop_session": has_high_risk, "follow_up_action": ("停止教学，引导寻求线下帮助" if has_high_risk else "建议关注"), "reasoning": "AI 风险评估不可用，基于关键词评估"}
        ai_risk_level = "无"  # AI did not contribute; detection source stays keyword
        should_stop = has_high_risk

    # Only skip when both channels report no concern
    if not triggered and ai_risk_level == "无":
        return None

    detection_source = _classify_detection_source(triggered, ai_risk_level)

    create_risk_event(
        user=user,
        session=test.session,
        trigger_text=text,
        detection_source=detection_source,
        action_taken=risk_dict.get("follow_up_action", ""),
        session_stopped=should_stop,
        follow_up_mode="onsite_manual_followup" if should_stop else "no_action",
    )

    if should_stop:
        test.status = test.Status.USER_TERMINATED
        test.save(update_fields=["status"])
        logger.info("Test %s terminated by risk detection", test.test_id)

    return risk_dict
