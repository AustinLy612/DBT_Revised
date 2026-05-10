"""RAG chain functions for DBT teaching and testing sub-flows.

Each function in this module is a self-contained RAG pipeline:
  1. Retrieve relevant chunks from the knowledge base
  2. Format the prompt with student context and retrieved content
  3. Call the LLM (MiniMax) with the formatted prompt
  4. Parse the JSON response through the Pydantic schema
  5. Return the validated structured output

All functions accept either:
- A real MiniMax API call (when MINIMAX_API_KEY is set), or
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
    minimax_chat_completion,
)
from .prompts import (
    build_risk_assessment_messages,
    build_skill_selection_messages,
    build_teaching_content_messages,
    build_teaching_plan_messages,
    build_teaching_summary_messages,
    build_test_questions_messages,
)
from .retriever import DBTRetriever, get_retriever
from .schemas import (
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
        return OutputValidator.validate_and_repair(mock_llm_response, schema_model)

    raw_result = minimax_chat_completion(
        messages,
        temperature=0.3,
        reply_format="json",
    )

    content = raw_result["content"]

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("LLM returned non-JSON. Attempting repair. Error: %s", exc)
        parsed = OutputValidator.repair_json(content)

    return OutputValidator.validate_and_repair(parsed, schema_model)


# ── Skill Selection ──

def generate_skill_selection(
    *,
    profile: Any = None,
    history_skills: list[str] | None = None,
    available_modules: list[str] | None = None,
    retriever: DBTRetriever | None = None,
    retrieval_query: str = "",
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
        available_modules=available_modules,
        retrieval_chunks=chunks,
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
    mock_llm_response: dict[str, Any] | None = None,
) -> TeachingContent:
    """Generate a single teaching message during a session.

    Returns a validated TeachingContent with message type, content,
    optional question, confidence, and source chunks.
    """

    is_mock = mock_llm_response is not None
    if is_mock:
        chunks = []
    else:
        ret = retriever or get_retriever(k=3, use_case="teaching")
        query = retrieval_query or f"{selected_skill} {student_message}"
        chunks = ret.search_with_context(query)

    messages = build_teaching_content_messages(
        profile=profile,
        selected_skill=selected_skill,
        teaching_plan_steps=teaching_plan_steps,
        current_step=current_step,
        conversation_history=conversation_history,
        student_message=student_message,
        retrieval_chunks=chunks,
    )

    result = _call_llm_or_mock(messages, TeachingContent, mock_llm_response)
    return TeachingContent(**result)


# ── Teaching Summary ──

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
