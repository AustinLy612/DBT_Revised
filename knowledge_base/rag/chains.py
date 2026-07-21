"""RAG chain functions for DBT teaching and testing sub-flows.

Each function in this module is a self-contained RAG pipeline:
  1. Retrieve relevant chunks from the knowledge base
  2. Format the prompt with student context and retrieved content
  3. Call the LLM (DeepSeek) with the formatted prompt
  4. Parse the JSON response through the Pydantic schema
  5. Return the validated structured output

All functions accept either:
- A real DeepSeek API call (when DEEPSEEK_API_KEY is set), or
- A mock_llm_response parameter for testing without API access

Error handling:
- If the LLM returns invalid JSON, the validator attempts repair
- If the LLM call fails, the error is logged and re-raised
- If the schema validation fails, a ValidationError is raised with details
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .llm_client import (
    APIError,
    ConfigurationError,
    chat_completion,
)
from .prompts import (
    build_personal_inquiry_messages,
    build_risk_assessment_messages,
    build_skill_selection_messages,
    build_teaching_content_messages,
    build_teaching_plan_messages,
    build_teaching_summary_messages,
    build_test_questions_messages,
)
from .retriever import DBTRetriever, get_retriever
from .schemas import (
    PersonalInquiryResult,
    RiskAssessment,
    SkillSelectionResult,
    TeachingContent,
    TeachingPlan,
    TeachingSummary,
    TestQuestions,
)
from .validator import OutputValidator

logger = logging.getLogger("dbt_platform.knowledge_base.rag")


def _call_llm_or_mock(
    messages: list[dict[str, str]],
    schema_model: type,
    mock_llm_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the LLM or return a mock response for testing.

    Args:
        messages: Chat messages to send.
        schema_model: Pydantic model class for validation.
        mock_llm_response: If provided, use this instead of calling the API.

    Returns:
        Validated dict matching the schema.
    """
    if mock_llm_response is not None:
        try:
            return OutputValidator.validate_and_repair(mock_llm_response, schema_model)
        except Exception as exc:
            raise APIError(
                f"Mock validation failed for {schema_model.__name__}: {exc}"
            ) from exc

    raw_result = chat_completion(
        messages,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    content = raw_result["content"]

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("LLM returned non-JSON. Attempting repair. Error: %s", exc)
        try:
            parsed = OutputValidator.repair_json(content)
        except Exception as repair_exc:
            raise APIError(
                f"LLM returned unrepairable JSON for {schema_model.__name__}: {repair_exc}"
            ) from repair_exc

    try:
        return OutputValidator.validate_and_repair(parsed, schema_model)
    except Exception as exc:
        raise APIError(
            f"LLM output validation failed for {schema_model.__name__}: {exc}"
        ) from exc


# ── Personal Inquiry ──

def generate_personal_inquiry(
    *,
    profile: Any = None,
    mood_value: int = 3,
    mood_note: str = "",
    mock_llm_response: dict[str, Any] | None = None,
) -> PersonalInquiryResult:
    """Generate a warm, empathetic question to understand the student's recent situation.

    Called after pre-mood recording and before skill selection so the student's
    personal context can inform which skill is most appropriate.

    Returns a validated PersonalInquiryResult with greeting, question, and focus.
    """
    messages = build_personal_inquiry_messages(
        profile=profile,
        mood_value=mood_value,
        mood_note=mood_note,
    )

    result = _call_llm_or_mock(messages, PersonalInquiryResult, mock_llm_response)
    return PersonalInquiryResult(**result)


# ── Skill Selection ──

def generate_skill_selection(
    *,
    profile: Any = None,
    history_skills: list[str] | None = None,
    recent_avoid_skills: list[str] | None = None,
    failed_skills: list[str] | None = None,
    available_modules: list[str] | None = None,
    retriever: DBTRetriever | None = None,
    retrieval_query: str = "",
    personal_context: str = "",
    mood_value: int | None = None,
    mock_llm_response: dict[str, Any] | None = None,
) -> SkillSelectionResult:
    """Generate a skill recommendation for a student.

    Returns a validated SkillSelectionResult with the recommended skill,
    rationale, difficulty level, and supporting chunks.
    """

    is_mock = mock_llm_response is not None
    if is_mock:
        chunks = []
    else:
        ret = retriever or get_retriever(k=5, use_case="teaching")
        chunks = ret.search_with_context(retrieval_query or "DBT技能概述")

    messages = build_skill_selection_messages(
        profile=profile,
        history_skills=history_skills,
        recent_avoid_skills=recent_avoid_skills,
        failed_skills=failed_skills,
        available_modules=available_modules,
        retrieval_chunks=chunks,
        personal_context=personal_context,
        mood_value=mood_value,
    )

    result = _call_llm_or_mock(messages, SkillSelectionResult, mock_llm_response)
    return SkillSelectionResult(**result)


# ── Teaching Plan ──

def generate_teaching_plan(
    *,
    profile: Any = None,
    selected_skill: str = "",
    selected_module: str = "",
    retriever: DBTRetriever | None = None,
    retrieval_query: str = "",
    mock_llm_response: dict[str, Any] | None = None,
) -> TeachingPlan:
    """Generate a structured teaching plan for a session.

    Returns a validated TeachingPlan with steps, estimated durations,
    prerequisites, and supporting chunks.
    """

    is_mock = mock_llm_response is not None
    if is_mock:
        chunks = []
    else:
        ret = retriever or get_retriever(k=5, use_case="teaching")
        query = retrieval_query or f"{selected_module} {selected_skill} 教学方法"
        chunks = ret.search_with_context(query)

    messages = build_teaching_plan_messages(
        profile=profile,
        selected_skill=selected_skill,
        selected_module=selected_module,
        retrieval_chunks=chunks,
    )

    result = _call_llm_or_mock(messages, TeachingPlan, mock_llm_response)
    return TeachingPlan(**result)


# ── Teaching Opening (AI initiates the conversation) ──


def generate_teaching_opening(
    *,
    profile: Any = None,
    selected_skill: str = "",
    selected_module: str = "",
    selection_reason: str = "",
    personal_context: str = "",
    teaching_plan_steps: list[Any] | None = None,
    retriever: DBTRetriever | None = None,
    mock_llm_response: dict[str, Any] | None = None,
) -> TeachingContent:
    """Generate the AI's opening message when the teaching phase begins.

    This allows the AI to initiate the conversation — the student doesn't
    need to send the first message.  The opening greets the student,
    introduces the skill, and leads into the first teaching point.
    """
    from .prompts import build_teaching_opening_messages
    from .retriever import get_retriever

    is_mock = mock_llm_response is not None
    if is_mock:
        chunks = []
    else:
        ret = retriever or get_retriever(k=3, use_case="teaching")
        query = f"{selected_skill} 入门介绍 基础概念"
        chunks = ret.search_with_context(query)

    messages = build_teaching_opening_messages(
        profile=profile,
        selected_skill=selected_skill,
        selected_module=selected_module,
        selection_reason=selection_reason,
        personal_context=personal_context,
        teaching_plan_steps=teaching_plan_steps,
        retrieval_chunks=chunks,
    )

    result = _call_llm_or_mock(messages, TeachingContent, mock_llm_response)
    return TeachingContent(**result)


# ── Teaching Content ──

def generate_teaching_content(
    *,
    profile: Any = None,
    selected_skill: str = "",
    teaching_plan_steps: list[Any] | None = None,
    current_step: int = 1,
    conversation_history: list[dict[str, str]] | None = None,
    student_message: str = "",
    retriever: DBTRetriever | None = None,
    retrieval_query: str = "",
    include_risk_assessment: bool = False,
    prefetched_chunks: list[dict[str, Any]] | None = None,
    mock_llm_response: dict[str, Any] | None = None,
) -> TeachingContent:
    """Generate a single teaching message during a session.

    Returns a validated TeachingContent with message type, content,
    optional question, confidence, source chunks, and (when
    include_risk_assessment=True) risk assessment fields.

    When prefetched_chunks is provided, they are merged into the
    retrieval results, giving the LLM broader context without
    requiring an additional search round-trip.
    """

    is_mock = mock_llm_response is not None
    if is_mock:
        chunks = []
    else:
        ret = retriever or get_retriever(k=3, use_case="teaching")
        query = retrieval_query or f"{selected_skill} {student_message}"
        chunks = ret.search_with_context(query)
        # Merge pre-fetched chunks (deduplicated by chunk_id)
        if prefetched_chunks:
            seen_ids = {c.get("chunk_id") for c in chunks if c.get("chunk_id")}
            for pc in prefetched_chunks:
                if pc.get("chunk_id") not in seen_ids:
                    seen_ids.add(pc.get("chunk_id"))
                    chunks.append(pc)

    messages = build_teaching_content_messages(
        profile=profile,
        selected_skill=selected_skill,
        teaching_plan_steps=teaching_plan_steps,
        current_step=current_step,
        conversation_history=conversation_history,
        student_message=student_message,
        retrieval_chunks=chunks,
        include_risk_assessment=include_risk_assessment,
    )

    result = _call_llm_or_mock(messages, TeachingContent, mock_llm_response)
    return TeachingContent(**result)


# ── Streaming Teaching Content ──

import re as _re  # noqa: E402

_META_RE = _re.compile(r"<!--META:(.*?)-->", _re.DOTALL)


def stream_teaching_content(
    *,
    profile: Any = None,
    selected_skill: str = "",
    teaching_plan_steps: list[Any] | None = None,
    current_step: int = 1,
    conversation_history: list[dict[str, str]] | None = None,
    student_message: str = "",
    retriever: DBTRetriever | None = None,
    retrieval_query: str = "",
    prefetched_chunks: list[dict[str, Any]] | None = None,
):
    """Stream teaching content generation, yielding SSE-style events.

    Yields dicts with keys:
      - {"type": "content", "text": "..."}  — incremental text delta
      - {"type": "done", "teaching_content": {...}}  — final structured result
      - {"type": "error", "message": "..."}  — on failure

    The LLM outputs natural language with metadata in an HTML comment
    (<!--META:{...}-->) at the end.  The comment is filtered from the
    stream so the user only sees the teaching content.
    """
    from .llm_client import APIError, ConfigurationError, chat_completion_stream
    from .prompts import build_streaming_teaching_messages
    from .retriever import get_retriever

    # ── RAG retrieval ──
    ret = retriever or get_retriever(k=3, use_case="teaching")
    query = retrieval_query or f"{selected_skill} {student_message}"
    try:
        chunks = ret.search_with_context(query)
    except Exception:
        chunks = []
    if prefetched_chunks:
        seen_ids = {c.get("chunk_id") for c in chunks if c.get("chunk_id")}
        for pc in prefetched_chunks:
            if pc.get("chunk_id") not in seen_ids:
                seen_ids.add(pc.get("chunk_id"))
                chunks.append(pc)

    # ── Build messages & stream ──
    messages = build_streaming_teaching_messages(
        profile=profile,
        selected_skill=selected_skill,
        teaching_plan_steps=teaching_plan_steps,
        current_step=current_step,
        conversation_history=conversation_history,
        student_message=student_message,
        retrieval_chunks=chunks,
    )

    try:
        stream = chat_completion_stream(messages)
    except (ConfigurationError, APIError) as exc:
        yield {"type": "error", "message": str(exc)}
        return

    full_text = ""
    streaming_content = True
    for item in stream:
        if streaming_content and item == "[STREAM_DONE]":
            streaming_content = False
            continue
        if streaming_content:
            # Content delta — yield directly, frontend filters META comment
            yield {"type": "content", "text": item}
        else:
            # Final accumulated text (after [STREAM_DONE] sentinel)
            full_text = item

    # Parse final result from accumulated full text
    teaching_content = _parse_streaming_content(full_text, chunks)
    yield {"type": "done", "teaching_content": teaching_content}
    logger.debug("Streaming teaching complete: %d chars", len(teaching_content.get("content", "")))


def _parse_streaming_content(full_text: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse the streaming LLM output into a TeachingContent-compatible dict.

    Extracts metadata from <!--META:{...}--> and cleans the content.
    Falls back to sensible defaults if metadata parsing fails.
    """
    meta_match = _META_RE.search(full_text)
    meta: dict[str, Any] = {}
    if meta_match:
        try:
            meta = json.loads(meta_match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Remove the meta comment from displayed content
    clean_content = _META_RE.sub("", full_text).strip()

    source_ids = [c.get("chunk_id", "") for c in chunks if c.get("chunk_id")]

    return {
        "content": clean_content,
        "message_type": meta.get("message_type", "讲解"),
        "image_prompt": meta.get("image_prompt", ""),
        "question": meta.get("question", ""),
        "confidence": 0.8,
        "source_chunk_ids": source_ids,
        "risk_level": meta.get("risk_level", "无"),
        "should_stop_session": meta.get("should_stop_session", False),
        "risk_reasoning": meta.get("risk_reasoning", ""),
    }

def generate_teaching_summary(
    *,
    profile: Any = None,
    skill: str = "",
    conversation_history: list[dict[str, str]] | None = None,
    retriever: DBTRetriever | None = None,
    retrieval_query: str = "",
    mock_llm_response: dict[str, Any] | None = None,
) -> TeachingSummary:
    """Generate a teaching summary after a session ends.

    Returns a validated TeachingSummary with key points, understanding
    assessment, and recommendations.
    """

    is_mock = mock_llm_response is not None
    if is_mock:
        chunks = []
    else:
        ret = retriever or get_retriever(k=3, use_case="teaching")
        query = retrieval_query or f"{skill} 教学总结"
        chunks = ret.search_with_context(query)

    messages = build_teaching_summary_messages(
        profile=profile,
        skill=skill,
        conversation_history=conversation_history,
        retrieval_chunks=chunks,
    )

    result = _call_llm_or_mock(messages, TeachingSummary, mock_llm_response)
    return TeachingSummary(**result)


# ── Test Questions ──

def generate_test_questions(
    *,
    profile: Any = None,
    skill: str = "",
    module: str = "",
    teaching_summary_key_points: list[str] | None = None,
    previous_tests_correct_rates: list[float] | None = None,
    retriever: DBTRetriever | None = None,
    retrieval_query: str = "",
    mock_llm_response: dict[str, Any] | None = None,
) -> TestQuestions:
    """Generate 5 test questions for a completed teaching session.

    Returns a validated TestQuestions with exactly 5 multiple-choice
    questions, each with options, correct answer, and explanation.
    """

    is_mock = mock_llm_response is not None
    if is_mock:
        chunks = []
    else:
        ret = retriever or get_retriever(k=8, use_case="test_generation")
        query = retrieval_query or f"{module} {skill} 测试题 情景选择题"
        chunks = ret.search_with_context(query)

    messages = build_test_questions_messages(
        profile=profile,
        skill=skill,
        module=module,
        teaching_summary_key_points=teaching_summary_key_points,
        previous_tests_correct_rates=previous_tests_correct_rates,
        retrieval_chunks=chunks,
    )

    result = _call_llm_or_mock(messages, TestQuestions, mock_llm_response)
    return TestQuestions(**result)


# ── Risk Assessment ──

def run_risk_assessment(
    *,
    user_message: str = "",
    recent_context: list[dict[str, str]] | None = None,
    triggered_keywords: list[str] | None = None,
    mock_llm_response: dict[str, Any] | None = None,
) -> RiskAssessment:
    """Evaluate a user message for potential risk.

    Returns a validated RiskAssessment with risk level, type, reasoning,
    and whether the session should be stopped.
    """

    messages = build_risk_assessment_messages(
        user_message=user_message,
        recent_context=recent_context,
        triggered_keywords=triggered_keywords,
    )

    result = _call_llm_or_mock(messages, RiskAssessment, mock_llm_response)
    return RiskAssessment(**result)
