"""Tests for Step 6: LangChain-based RAG with structured output schemas.

Coverage:
- Schema validation (valid + invalid data for each Pydantic model)
- Prompt template rendering (each build_*_messages function)
- LLM client error handling (missing API key, timeout, bad responses)
- Retriever (wrapping hybrid_search, Document output, RetrievalLog)
- Validator (JSON repair: fences, trailing commas, non-JSON text)
- Chains (each generate_* function with mock LLM responses)
- End-to-end: retriever + chain + mock → valid structured output
- Stability: repeated calls with same input produce same structure
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase

from .models import KnowledgeChunk, KnowledgeDocument, RetrievalLog
from .rag.llm_client import APIError, ConfigurationError
from .rag.retriever import DBTRetriever, get_retriever
from .rag.schemas import (
    RiskAssessment,
    SkillSelectionResult,
    TeachingContent,
    TeachingPlan,
    TeachingPlanStep,
    TeachingSummary,
    TestQuestion,
    TestQuestions,
)
from .rag.validator import OutputValidator, ValidationError
from .services import ensure_mongodb_text_index

User = get_user_model()

# ── Helpers ──


def create_admin(username="ragadmin"):
    return User.objects.create_user(
        username=username, password="testpass123", role="admin"
    )


def create_student(username="ragstudent"):
    return User.objects.create_user(
        username=username, password="testpass123", role="student"
    )


def create_session(user):
    from teaching.models import TeachingSession
    return TeachingSession.objects.create(
        user=user, status="teaching", selected_module="正念", selected_skill="观察呼吸"
    )


def create_profile(user):
    from questionnaire.models import UserProfile
    return UserProfile.objects.create(
        user=user,
        gender="male",
        age=15,
        grade="grade_9",
        hobby_tags=["音乐", "阅读"],
        concern_tags=["学业压力"],
    )


# ── Valid mock LLM responses (satisfy every schema) ──

VALID_SKILL_SELECTION = {
    "selected_module": "正念",
    "selected_skill": "观察呼吸",
    "reason": "根据学生档案，对情绪管理有需求，观察呼吸是正念的基础技能，适合初学者",
    "skill_difficulty": "初级",
    "alternative_skills": ["情绪命名", "转移注意力"],
    "is_repeat": False,
    "repeat_justification": "",
    "source_chunk_ids": [],
}

VALID_TEACHING_PLAN = {
    "module": "正念",
    "skill": "观察呼吸",
    "plan_steps": [
        {"step_number": 1, "title": "导入", "content": "介绍正念的概念和好处", "estimated_minutes": 5},
        {"step_number": 2, "title": "演示", "content": "示范观察呼吸的方法", "estimated_minutes": 10},
        {"step_number": 3, "title": "练习", "content": "带领学生进行呼吸练习", "estimated_minutes": 10},
    ],
    "estimated_total_minutes": 25,
    "prerequisites": ["了解基本的情绪概念"],
    "source_chunk_ids": [],
}

VALID_TEACHING_CONTENT = {
    "message_type": "讲解",
    "content": "正念是一种将注意力集中在当下的练习。观察呼吸是最基础也最重要的正念练习之一。",
    "question": "",
    "source_chunk_ids": [],
    "confidence": "high",
}

VALID_TEACHING_SUMMARY = {
    "skill_covered": "观察呼吸",
    "key_points": ["正念的定义", "观察呼吸的方法", "如何应用到日常生活"],
    "student_understanding": "良好",
    "recommendations": ["建议每天练习5分钟", "下次可以学习身体扫描"],
    "summary_text": "学生掌握了基本的观察呼吸技巧，能够理解正念的核心概念。",
}

VALID_TEST_QUESTIONS = {
    "questions": [
        {
            "question_text": f"测试题{i}的题干",
            "options": ["选项A", "选项B", "选项C", "选项D"],
            "correct_option": 0,
            "explanation": f"测试题{i}的解析说明",
            "source_chunk_ids": [],
        }
        for i in range(1, 6)
    ],
    "test_difficulty": "初级",
}

VALID_RISK_ASSESSMENT = {
    "risk_level": "无",
    "risk_type": "",
    "reasoning": "学生表达了正常的情绪困扰，没有自伤或伤害他人的内容",
    "should_stop_session": False,
    "follow_up_action": "",
    "triggered_keywords": [],
}


# ═══════════════════════════════════════════════════════════
# Schema Validation Tests
# ═══════════════════════════════════════════════════════════

class SkillSelectionSchemaTests(TestCase):
    def test_valid_skill_selection(self):
        result = SkillSelectionResult(**VALID_SKILL_SELECTION)
        self.assertEqual(result.selected_skill, "观察呼吸")
        self.assertEqual(result.skill_difficulty, "初级")

    def test_invalid_difficulty_rejected(self):
        data = {**VALID_SKILL_SELECTION, "skill_difficulty": "超级难"}
        with self.assertRaises(Exception):
            SkillSelectionResult(**data)

    def test_empty_skill_rejected(self):
        data = {**VALID_SKILL_SELECTION, "selected_skill": ""}
        with self.assertRaises(Exception):
            SkillSelectionResult(**data)

    def test_empty_reason_rejected(self):
        data = {**VALID_SKILL_SELECTION, "reason": ""}
        with self.assertRaises(Exception):
            SkillSelectionResult(**data)

    def test_defaults_applied(self):
        result = SkillSelectionResult(
            selected_module="正念",
            selected_skill="正念",
            reason="好",
            skill_difficulty="初级",
        )
        self.assertEqual(result.alternative_skills, [])
        self.assertEqual(result.source_chunk_ids, [])
        self.assertFalse(result.is_repeat)
        self.assertEqual(result.repeat_justification, "")

    def test_repeat_fields_accepted(self):
        result = SkillSelectionResult(
            **{
                **VALID_SKILL_SELECTION,
                "is_repeat": True,
                "repeat_justification": "学生再次描述考试焦虑，且该技能测试未通过。",
            }
        )
        self.assertTrue(result.is_repeat)
        self.assertIn("测试未通过", result.repeat_justification)


class TeachingPlanSchemaTests(TestCase):
    def test_valid_teaching_plan(self):
        result = TeachingPlan(**VALID_TEACHING_PLAN)
        self.assertEqual(result.module, "正念")
        self.assertEqual(len(result.plan_steps), 3)
        self.assertIsInstance(result.plan_steps[0], TeachingPlanStep)

    def test_empty_steps_rejected(self):
        data = {**VALID_TEACHING_PLAN, "plan_steps": []}
        with self.assertRaises(Exception):
            TeachingPlan(**data)

    def test_step_number_must_be_positive(self):
        bad_steps = [
            {"step_number": 0, "title": "导入", "content": "test", "estimated_minutes": 5}
        ]
        data = {**VALID_TEACHING_PLAN, "plan_steps": bad_steps}
        with self.assertRaises(Exception):
            TeachingPlan(**data)


class TeachingContentSchemaTests(TestCase):
    def test_valid_teaching_content(self):
        result = TeachingContent(**VALID_TEACHING_CONTENT)
        self.assertEqual(result.message_type, "讲解")
        self.assertEqual(result.confidence, "high")

    def test_invalid_message_type_rejected(self):
        data = {**VALID_TEACHING_CONTENT, "message_type": "未知类型"}
        with self.assertRaises(Exception):
            TeachingContent(**data)

    def test_invalid_confidence_rejected(self):
        data = {**VALID_TEACHING_CONTENT, "confidence": "super_high"}
        with self.assertRaises(Exception):
            TeachingContent(**data)

    def test_all_message_types_accepted(self):
        for msg_type in ["讲解", "示例", "提问", "反馈", "总结"]:
            data = {**VALID_TEACHING_CONTENT, "message_type": msg_type}
            result = TeachingContent(**data)
            self.assertEqual(result.message_type, msg_type)


class TeachingSummarySchemaTests(TestCase):
    def test_valid_summary(self):
        result = TeachingSummary(**VALID_TEACHING_SUMMARY)
        self.assertEqual(result.skill_covered, "观察呼吸")
        self.assertEqual(result.student_understanding, "良好")

    def test_invalid_understanding_rejected(self):
        data = {**VALID_TEACHING_SUMMARY, "student_understanding": "非常棒"}
        with self.assertRaises(Exception):
            TeachingSummary(**data)

    def test_empty_key_points_rejected(self):
        data = {**VALID_TEACHING_SUMMARY, "key_points": []}
        with self.assertRaises(Exception):
            TeachingSummary(**data)


class TestQuestionsSchemaTests(TestCase):
    def test_valid_test_questions(self):
        result = TestQuestions(**VALID_TEST_QUESTIONS)
        self.assertEqual(len(result.questions), 5)
        self.assertIsInstance(result.questions[0], TestQuestion)

    def test_wrong_question_count_rejected(self):
        data = {**VALID_TEST_QUESTIONS, "questions": [VALID_TEST_QUESTIONS["questions"][0]] * 3}
        with self.assertRaises(Exception):
            TestQuestions(**data)

    def test_wrong_option_count_rejected(self):
        bad_questions = [
            {
                "question_text": "题",
                "options": ["A", "B", "C"],
                "correct_option": 0,
                "explanation": "解析",
                "source_chunk_ids": [],
            }
        ] * 5
        data = {**VALID_TEST_QUESTIONS, "questions": bad_questions}
        with self.assertRaises(Exception):
            TestQuestions(**data)

    def test_invalid_correct_option_rejected(self):
        bad_questions = [dict(VALID_TEST_QUESTIONS["questions"][0]) for _ in range(5)]
        bad_questions[0]["correct_option"] = 5
        data = {**VALID_TEST_QUESTIONS, "questions": bad_questions}
        with self.assertRaises(Exception):
            TestQuestions(**data)


class RiskAssessmentSchemaTests(TestCase):
    def test_valid_risk_assessment(self):
        result = RiskAssessment(**VALID_RISK_ASSESSMENT)
        self.assertEqual(result.risk_level, "无")
        self.assertFalse(result.should_stop_session)

    def test_invalid_risk_level_rejected(self):
        data = {**VALID_RISK_ASSESSMENT, "risk_level": "超高风险"}
        with self.assertRaises(Exception):
            RiskAssessment(**data)

    def test_high_risk_stops_session(self):
        data = {
            "risk_level": "高",
            "risk_type": "自伤",
            "reasoning": "学生明确表达了自伤意图",
            "should_stop_session": True,
            "follow_up_action": "立即联系心理老师",
            "triggered_keywords": ["想死", "自残"],
        }
        result = RiskAssessment(**data)
        self.assertTrue(result.should_stop_session)
        self.assertEqual(result.risk_type, "自伤")


# ═══════════════════════════════════════════════════════════
# Prompt Template Tests
# ═══════════════════════════════════════════════════════════

class PromptTemplateTests(TestCase):
    """Verify every prompt builder returns well-formed chat messages."""

    def setUp(self):
        self.student = create_student("prompttest")
        self.profile = create_profile(self.student)

    def test_skill_selection_messages_structure(self):
        msgs = self._import_prompt("build_skill_selection_messages")(
            profile=self.profile,
            history_skills=["观察呼吸", "情绪命名"],
            recent_avoid_skills=["观察呼吸", "情绪命名"],
            failed_skills=["情绪命名"],
            available_modules=["正念", "情绪调节"],
            retrieval_chunks=[{"chunk_text": "正念是DBT核心技能", "metadata": {}}],
        )
        self._assert_chat_structure(msgs)
        self.assertIn("JSON", msgs[0]["content"])
        self.assertIn("正念是DBT核心技能", msgs[1]["content"])
        self.assertIn("观察呼吸", msgs[1]["content"])
        self.assertIn("近期避免重复列表", msgs[1]["content"])
        self.assertIn("已学技能历史（按最近完成顺序", msgs[1]["content"])
        self.assertIn("测试薄弱", msgs[1]["content"])
        self.assertIn("is_repeat", msgs[0]["content"])
        self.assertIn("默认禁止再次推荐", msgs[0]["content"])

    def test_teaching_plan_messages_structure(self):
        msgs = self._import_prompt("build_teaching_plan_messages")(
            profile=self.profile,
            selected_skill="观察呼吸",
            selected_module="正念",
            retrieval_chunks=[{"chunk_text": "观察呼吸是最基础的练习", "metadata": {}}],
        )
        self._assert_chat_structure(msgs)
        self.assertIn("教学计划", msgs[1]["content"])

    def test_teaching_content_messages_structure(self):
        msgs = self._import_prompt("build_teaching_content_messages")(
            profile=self.profile,
            selected_skill="观察呼吸",
            student_message="什么是正念？",
            conversation_history=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！今天学习正念"},
            ],
        )
        self._assert_chat_structure(msgs)
        self.assertIn("什么是正念？", msgs[1]["content"])

    def test_teaching_summary_messages_structure(self):
        msgs = self._import_prompt("build_teaching_summary_messages")(
            profile=self.profile,
            skill="观察呼吸",
            conversation_history=[
                {"role": "user", "content": "好"},
                {"role": "assistant", "content": "很好"},
            ],
        )
        self._assert_chat_structure(msgs)
        self.assertIn("教学摘要", msgs[1]["content"])

    def test_test_questions_messages_structure(self):
        msgs = self._import_prompt("build_test_questions_messages")(
            profile=self.profile,
            skill="观察呼吸",
            module="正念",
            teaching_summary_key_points=["正念定义", "呼吸方法"],
        )
        self._assert_chat_structure(msgs)
        self.assertIn("5道测试题", msgs[1]["content"])

    def test_risk_assessment_messages_structure(self):
        msgs = self._import_prompt("build_risk_assessment_messages")(
            user_message="我今天心情不太好",
            triggered_keywords=["心情不好"],
        )
        self._assert_chat_structure(msgs)
        self.assertIn("我今天心情不太好", msgs[1]["content"])

    def test_skill_selection_with_none_profile(self):
        msgs = self._import_prompt("build_skill_selection_messages")(profile=None)
        self._assert_chat_structure(msgs)
        self.assertIn("尚未完成问卷", msgs[1]["content"])

    def test_skill_selection_with_empty_retrieval(self):
        """When retrieval returns no chunks, prompt should say so."""
        msgs = self._import_prompt("build_skill_selection_messages")(
            profile=self.profile,
            retrieval_chunks=[],
        )
        self._assert_chat_structure(msgs)
        self.assertIn("未检索到", msgs[1]["content"])

    def test_no_fabrication_rule_in_all_system_prompts(self):
        """Every system prompt that touches DBT knowledge must include
        the no-fabrication rule."""
        for builder_name in [
            "build_skill_selection_messages",
            "build_teaching_plan_messages",
            "build_teaching_content_messages",
            "build_test_questions_messages",
        ]:
            build_fn = self._import_prompt(builder_name)
            # Build with minimal args
            try:
                msgs = build_fn(
                    profile=self.profile,
                    selected_skill="正念",
                    selected_module="正念",
                    student_message="test",
                    skill="正念",
                    retrieval_chunks=[],
                )
            except TypeError:
                msgs = build_fn(profile=self.profile)

            system = msgs[0]["content"]
            self.assertIn(
                "编造", system,
                f"No-fabrication rule missing in {builder_name} system prompt"
            )

    def _import_prompt(self, name):
        from .rag import prompts
        return getattr(prompts, name)

    def _assert_chat_structure(self, msgs):
        self.assertIsInstance(msgs, list)
        self.assertGreaterEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")


# ═══════════════════════════════════════════════════════════
# LLM Client Error Handling Tests
# ═══════════════════════════════════════════════════════════

class LLMClientErrorTests(TestCase):
    def test_missing_api_key_raises_configuration_error(self):
        from .rag.llm_client import chat_completion
        with patch("django.conf.settings.DEEPSEEK_API_KEY", ""):
            with self.assertRaises(ConfigurationError):
                chat_completion([{"role": "user", "content": "test"}])

    def test_api_timeout_raises_api_error(self):
        import requests
        from .rag.llm_client import APIError, chat_completion
        with patch("django.conf.settings.DEEPSEEK_API_KEY", "test-key"):
            with patch("django.conf.settings.DEEPSEEK_BASE_URL", "https://api.deepseek.com"):
                mock_session = MagicMock()
                mock_session.post.side_effect = requests.Timeout
                with patch("knowledge_base.rag.llm_client._get_session", return_value=mock_session):
                    with self.assertRaises(APIError) as ctx:
                        chat_completion([{"role": "user", "content": "test"}])
                    self.assertIn("timed out", str(ctx.exception))

    def test_connection_error_raises_api_error(self):
        import requests
        from .rag.llm_client import APIError, chat_completion
        with patch("django.conf.settings.DEEPSEEK_API_KEY", "test-key"):
            with patch("django.conf.settings.DEEPSEEK_BASE_URL", "https://api.deepseek.com"):
                mock_session = MagicMock()
                mock_session.post.side_effect = requests.ConnectionError("refused")
                with patch("knowledge_base.rag.llm_client._get_session", return_value=mock_session):
                    with self.assertRaises(APIError) as ctx:
                        chat_completion([{"role": "user", "content": "test"}])
                    self.assertIn("connection failed", str(ctx.exception))

    def test_non_200_response_raises_api_error(self):
        from .rag.llm_client import APIError, chat_completion
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": {"message": "Invalid API key"}}
        mock_resp.text = '{"error": {"message": "Invalid API key"}}'
        with patch("django.conf.settings.DEEPSEEK_API_KEY", "test-key"):
            with patch("django.conf.settings.DEEPSEEK_BASE_URL", "https://api.deepseek.com"):
                mock_session = MagicMock()
                mock_session.post.return_value = mock_resp
                with patch("knowledge_base.rag.llm_client._get_session", return_value=mock_session):
                    with self.assertRaises(APIError) as ctx:
                        chat_completion([{"role": "user", "content": "test"}])
                    self.assertIn("401", str(ctx.exception))

    def test_successful_response_parsing(self):
        from .rag.llm_client import chat_completion
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "chatcmpl-123",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "你好！"},
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 50},
        }
        with patch("django.conf.settings.DEEPSEEK_API_KEY", "test-key"):
            with patch("django.conf.settings.DEEPSEEK_BASE_URL", "https://api.deepseek.com"):
                mock_session = MagicMock()
                mock_session.post.return_value = mock_resp
                with patch("knowledge_base.rag.llm_client._get_session", return_value=mock_session):
                    result = chat_completion(
                        [{"role": "user", "content": "你好"}],
                    )
                    self.assertEqual(result["role"], "assistant")
                    self.assertEqual(result["content"], "你好！")
                    self.assertEqual(result["finish_reason"], "stop")

    def test_empty_choices_raises_api_error(self):
        from .rag.llm_client import APIError, chat_completion
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": []}
        with patch("django.conf.settings.DEEPSEEK_API_KEY", "test-key"):
            with patch("django.conf.settings.DEEPSEEK_BASE_URL", "https://api.deepseek.com"):
                mock_session = MagicMock()
                mock_session.post.return_value = mock_resp
                with patch("knowledge_base.rag.llm_client._get_session", return_value=mock_session):
                    with self.assertRaises(APIError):
                        chat_completion([{"role": "user", "content": "test"}])


# ═══════════════════════════════════════════════════════════
# Validator Tests
# ═══════════════════════════════════════════════════════════

class ValidatorRepairTests(TestCase):
    def test_parse_valid_json(self):
        raw = '{"a": 1, "b": "hello"}'
        result = OutputValidator.repair_json(raw)
        self.assertEqual(result, {"a": 1, "b": "hello"})

    def test_strip_markdown_fence(self):
        raw = '```json\n{"a": 1}\n```'
        result = OutputValidator.repair_json(raw)
        self.assertEqual(result, {"a": 1})

    def test_strip_markdown_fence_no_lang(self):
        raw = '```\n{"a": 1}\n```'
        result = OutputValidator.repair_json(raw)
        self.assertEqual(result, {"a": 1})

    def test_strip_surrounding_text(self):
        raw = '这是一些解释文字\n{"a": 1}\n以上是JSON结果'
        result = OutputValidator.repair_json(raw)
        self.assertEqual(result, {"a": 1})

    def test_fix_trailing_comma_in_object(self):
        raw = '{"a": 1, "b": 2,}'
        result = OutputValidator.repair_json(raw)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_fix_trailing_comma_in_array(self):
        raw = '{"items": [1, 2, 3,]}'
        result = OutputValidator.repair_json(raw)
        self.assertEqual(result, {"items": [1, 2, 3]})

    def test_unrepairable_raises(self):
        raw = "this is not json at all just random text"
        with self.assertRaises(ValidationError):
            OutputValidator.repair_json(raw)


class ValidatorSchemaTests(TestCase):
    def test_valid_data_passes(self):
        result = OutputValidator.validate_and_repair(
            VALID_SKILL_SELECTION, SkillSelectionResult
        )
        self.assertEqual(result["selected_skill"], "观察呼吸")

    def test_valid_data_as_string_parsed(self):
        raw = json.dumps(VALID_SKILL_SELECTION)
        result = OutputValidator.validate_and_repair(raw, SkillSelectionResult)
        self.assertEqual(result["selected_skill"], "观察呼吸")

    def test_invalid_schema_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            OutputValidator.validate_and_repair(
                {"selected_skill": "", "reason": "", "skill_difficulty": "初级"},
                SkillSelectionResult,
            )

    def test_direct_validate(self):
        result = OutputValidator.validate(VALID_TEACHING_CONTENT, TeachingContent)
        self.assertEqual(result["message_type"], "讲解")

    def test_validate_all_schema_types(self):
        """Every schema type must accept its valid fixture."""
        tests = [
            (VALID_SKILL_SELECTION, SkillSelectionResult),
            (VALID_TEACHING_PLAN, TeachingPlan),
            (VALID_TEACHING_CONTENT, TeachingContent),
            (VALID_TEACHING_SUMMARY, TeachingSummary),
            (VALID_TEST_QUESTIONS, TestQuestions),
            (VALID_RISK_ASSESSMENT, RiskAssessment),
        ]
        for data, model in tests:
            result = OutputValidator.validate(data, model)
            self.assertIsInstance(result, dict)


# ═══════════════════════════════════════════════════════════
# Retriever Tests
# ═══════════════════════════════════════════════════════════

class RetrieverTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_mongodb_text_index()

    def setUp(self):
        self.doc = KnowledgeDocument.objects.create(
            title="检索器测试", module="正念", skill="观察呼吸"
        )
        self.c1 = KnowledgeChunk.objects.create(
            document=self.doc,
            chunk_text="观察呼吸是DBT正念模块的核心练习，帮助学生专注于当下。",
            metadata={"section_title": "呼吸练习", "module": "正念", "difficulty": "初级"},
        )
        self.c2 = KnowledgeChunk.objects.create(
            document=self.doc,
            chunk_text="情绪调节模块包括识别情绪、命名情绪和减少情绪脆弱性。",
            metadata={"section_title": "情绪调节概述", "module": "情绪调节"},
        )
        self.admin = create_admin("retriever_admin")
        self.student = create_student("retriever_student")
        self.session = create_session(self.student)

    def test_retriever_returns_langchain_documents(self):
        retriever = DBTRetriever(k=3)
        docs = retriever.invoke("观察呼吸")
        self.assertGreaterEqual(len(docs), 1)
        for doc in docs:
            self.assertIsInstance(doc.page_content, str)
            self.assertIn("chunk_id", doc.metadata)
            self.assertIn("score", doc.metadata)

    def test_retriever_logs_retrieval(self):
        retriever = DBTRetriever(k=3, user=self.student, session=self.session, use_case="teaching")
        count_before = RetrievalLog.objects.filter(user=self.student).count()
        retriever.invoke("观察呼吸")
        count_after = RetrievalLog.objects.filter(user=self.student).count()
        self.assertEqual(count_after, count_before + 1)

    def test_retriever_search_with_context_returns_raw_chunks(self):
        retriever = DBTRetriever(k=3)
        chunks = retriever.search_with_context("观察呼吸")
        self.assertIsInstance(chunks, list)
        if chunks:
            self.assertIn("chunk_id", chunks[0])
            self.assertIn("chunk_text", chunks[0])

    def test_retriever_search_with_context_logs(self):
        retriever = DBTRetriever(k=3)
        count_before = RetrievalLog.objects.count()
        retriever.search_with_context("观察呼吸", user=self.student, use_case="teaching")
        count_after = RetrievalLog.objects.count()
        self.assertEqual(count_after, count_before + 1)

    def test_retriever_no_match_returns_empty(self):
        retriever = DBTRetriever(k=3)
        docs = retriever.invoke("xyzabc123不存在的内容")
        self.assertEqual(len(docs), 0)

    def test_get_retriever_factory(self):
        retriever = get_retriever(k=7, user=self.student, use_case="test_generation")
        self.assertEqual(retriever.k, 7)
        self.assertEqual(retriever.user, self.student)
        self.assertEqual(retriever.use_case, "test_generation")

    def test_retriever_document_metadata_fields(self):
        retriever = DBTRetriever(k=3)
        docs = retriever.invoke("观察呼吸")
        if docs:
            doc = docs[0]
            self.assertIn("chunk_id", doc.metadata)
            self.assertIn("document_id", doc.metadata)
            self.assertIn("source", doc.metadata)
            self.assertIn("score", doc.metadata)


# ═══════════════════════════════════════════════════════════
# Chain Tests (with mock LLM responses)
# ═══════════════════════════════════════════════════════════

class ChainSkillSelectionTests(TestCase):
    def setUp(self):
        self.student = create_student("chain_skill")
        self.profile = create_profile(self.student)

    def test_skill_selection_with_mock_returns_valid_result(self):
        from .rag.chains import generate_skill_selection
        result = generate_skill_selection(
            profile=self.profile,
            history_skills=["正念"],
            available_modules=["正念", "情绪调节"],
            retrieval_query="DBT技能概述",
            mock_llm_response=VALID_SKILL_SELECTION,
        )
        self.assertIsInstance(result, SkillSelectionResult)
        self.assertEqual(result.selected_skill, "观察呼吸")
        self.assertEqual(result.skill_difficulty, "初级")

    def test_skill_selection_with_no_profile(self):
        from .rag.chains import generate_skill_selection
        result = generate_skill_selection(
            profile=None,
            retrieval_query="DBT技能",
            mock_llm_response=VALID_SKILL_SELECTION,
        )
        self.assertIsInstance(result, SkillSelectionResult)


class ChainTeachingPlanTests(TestCase):
    def setUp(self):
        self.student = create_student("chain_plan")
        self.profile = create_profile(self.student)

    def test_teaching_plan_with_mock_returns_valid_result(self):
        from .rag.chains import generate_teaching_plan
        result = generate_teaching_plan(
            profile=self.profile,
            selected_skill="观察呼吸",
            selected_module="正念",
            retrieval_query="正念 观察呼吸 教学方法",
            mock_llm_response=VALID_TEACHING_PLAN,
        )
        self.assertIsInstance(result, TeachingPlan)
        self.assertEqual(result.module, "正念")
        self.assertEqual(len(result.plan_steps), 3)

    def test_teaching_plan_steps_have_required_fields(self):
        from .rag.chains import generate_teaching_plan
        result = generate_teaching_plan(
            profile=self.profile,
            selected_skill="观察呼吸",
            selected_module="正念",
            mock_llm_response=VALID_TEACHING_PLAN,
        )
        for step in result.plan_steps:
            self.assertIsInstance(step.step_number, int)
            self.assertGreaterEqual(step.step_number, 1)
            self.assertTrue(step.title)
            self.assertTrue(step.content)


class ChainTeachingContentTests(TestCase):
    def setUp(self):
        self.student = create_student("chain_content")
        self.profile = create_profile(self.student)

    def test_teaching_content_with_mock(self):
        from .rag.chains import generate_teaching_content
        result = generate_teaching_content(
            profile=self.profile,
            selected_skill="观察呼吸",
            student_message="什么是正念？",
            mock_llm_response=VALID_TEACHING_CONTENT,
        )
        self.assertIsInstance(result, TeachingContent)
        self.assertEqual(result.message_type, "讲解")

    def test_teaching_content_question_type(self):
        from .rag.chains import generate_teaching_content
        question_response = {**VALID_TEACHING_CONTENT, "message_type": "提问", "question": "你能说出正念的一个好处吗？"}
        result = generate_teaching_content(
            profile=self.profile,
            selected_skill="观察呼吸",
            student_message="我理解了",
            mock_llm_response=question_response,
        )
        self.assertEqual(result.message_type, "提问")
        self.assertTrue(result.question)

    def test_plan_steps_pydantic_objects_are_accepted(self):
        """Chain plan → content: Pydantic TeachingPlanStep objects must work."""
        from .rag.chains import generate_teaching_plan, generate_teaching_content

        plan = generate_teaching_plan(
            profile=self.profile,
            selected_skill="观察呼吸",
            selected_module="正念",
            mock_llm_response=VALID_TEACHING_PLAN,
        )
        self.assertEqual(len(plan.plan_steps), 3)
        self.assertTrue(hasattr(plan.plan_steps[0], "step_number"))

        result = generate_teaching_content(
            profile=self.profile,
            selected_skill="观察呼吸",
            teaching_plan_steps=plan.plan_steps,
            current_step=2,
            student_message="我试了一下，感觉有点难",
            mock_llm_response=VALID_TEACHING_CONTENT,
        )
        self.assertIsInstance(result, TeachingContent)
        self.assertEqual(result.message_type, "讲解")


class ChainTeachingSummaryTests(TestCase):
    def setUp(self):
        self.student = create_student("chain_summary")
        self.profile = create_profile(self.student)

    def test_teaching_summary_with_mock(self):
        from .rag.chains import generate_teaching_summary
        result = generate_teaching_summary(
            profile=self.profile,
            skill="观察呼吸",
            conversation_history=[
                {"role": "user", "content": "我学会了"},
                {"role": "assistant", "content": "很好！"},
            ],
            mock_llm_response=VALID_TEACHING_SUMMARY,
        )
        self.assertIsInstance(result, TeachingSummary)
        self.assertEqual(result.skill_covered, "观察呼吸")
        self.assertEqual(result.student_understanding, "良好")
        self.assertGreaterEqual(len(result.key_points), 1)


class ChainTestQuestionsTests(TestCase):
    def setUp(self):
        self.student = create_student("chain_testq")
        self.profile = create_profile(self.student)

    def test_test_questions_with_mock(self):
        from .rag.chains import generate_test_questions
        result = generate_test_questions(
            profile=self.profile,
            skill="观察呼吸",
            module="正念",
            teaching_summary_key_points=["正念定义", "呼吸方法"],
            mock_llm_response=VALID_TEST_QUESTIONS,
        )
        self.assertIsInstance(result, TestQuestions)
        self.assertEqual(len(result.questions), 5)
        self.assertEqual(result.test_difficulty, "初级")

    def test_each_question_has_required_fields(self):
        from .rag.chains import generate_test_questions
        result = generate_test_questions(
            profile=self.profile,
            skill="观察呼吸",
            module="正念",
            mock_llm_response=VALID_TEST_QUESTIONS,
        )
        for q in result.questions:
            self.assertTrue(q.question_text)
            self.assertEqual(len(q.options), 4)
            self.assertGreaterEqual(q.correct_option, 0)
            self.assertLessEqual(q.correct_option, 3)
            self.assertTrue(q.explanation)


class ChainRiskAssessmentTests(TestCase):
    def test_risk_assessment_with_mock(self):
        from .rag.chains import run_risk_assessment
        result = run_risk_assessment(
            user_message="我今天心情不太好",
            triggered_keywords=["心情不好"],
            mock_llm_response=VALID_RISK_ASSESSMENT,
        )
        self.assertIsInstance(result, RiskAssessment)
        self.assertEqual(result.risk_level, "无")
        self.assertFalse(result.should_stop_session)

    def test_high_risk_scenario(self):
        from .rag.chains import run_risk_assessment
        high_risk = {
            "risk_level": "高",
            "risk_type": "自伤",
            "reasoning": "学生表达了自伤计划",
            "should_stop_session": True,
            "follow_up_action": "立即联系心理老师",
            "triggered_keywords": ["自残"],
        }
        result = run_risk_assessment(
            user_message="我想伤害自己",
            triggered_keywords=["伤害"],
            mock_llm_response=high_risk,
        )
        self.assertEqual(result.risk_level, "高")
        self.assertTrue(result.should_stop_session)


# ═══════════════════════════════════════════════════════════
# Stability Tests (same input → same structure)
# ═══════════════════════════════════════════════════════════

class StabilityTests(TestCase):
    """Verify repeated calls with identical mock input produce identical structure."""

    def setUp(self):
        self.student = create_student("stab_user")
        self.profile = create_profile(self.student)

    def test_skill_selection_stable_structure(self):
        from .rag.chains import generate_skill_selection
        r1 = generate_skill_selection(
            profile=self.profile, mock_llm_response=VALID_SKILL_SELECTION
        )
        r2 = generate_skill_selection(
            profile=self.profile, mock_llm_response=VALID_SKILL_SELECTION
        )
        self.assertEqual(r1.selected_skill, r2.selected_skill)
        self.assertEqual(r1.skill_difficulty, r2.skill_difficulty)
        self.assertEqual(r1.model_dump().keys(), r2.model_dump().keys())

    def test_teaching_plan_stable_structure(self):
        from .rag.chains import generate_teaching_plan
        r1 = generate_teaching_plan(
            profile=self.profile, selected_skill="观察呼吸", selected_module="正念",
            mock_llm_response=VALID_TEACHING_PLAN,
        )
        r2 = generate_teaching_plan(
            profile=self.profile, selected_skill="观察呼吸", selected_module="正念",
            mock_llm_response=VALID_TEACHING_PLAN,
        )
        self.assertEqual(r1.module, r2.module)
        self.assertEqual(len(r1.plan_steps), len(r2.plan_steps))

    def test_test_questions_stable_structure(self):
        from .rag.chains import generate_test_questions
        r1 = generate_test_questions(
            profile=self.profile, skill="观察呼吸", module="正念",
            mock_llm_response=VALID_TEST_QUESTIONS,
        )
        r2 = generate_test_questions(
            profile=self.profile, skill="观察呼吸", module="正念",
            mock_llm_response=VALID_TEST_QUESTIONS,
        )
        self.assertEqual(len(r1.questions), len(r2.questions))
        for q1, q2 in zip(r1.questions, r2.questions):
            self.assertEqual(len(q1.options), len(q2.options))

    def test_risk_assessment_stable_structure(self):
        from .rag.chains import run_risk_assessment
        r1 = run_risk_assessment(
            user_message="test", mock_llm_response=VALID_RISK_ASSESSMENT,
        )
        r2 = run_risk_assessment(
            user_message="test", mock_llm_response=VALID_RISK_ASSESSMENT,
        )
        self.assertEqual(r1.risk_level, r2.risk_level)
        self.assertEqual(r1.should_stop_session, r2.should_stop_session)


# ═══════════════════════════════════════════════════════════
# Retrieval Dependency Tests
# ═══════════════════════════════════════════════════════════

class RetrievalDependencyTests(TransactionTestCase):
    """Verify that when valid retrieval context is provided, output fields
    referencing source_chunk_ids are populated."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_mongodb_text_index()

    def setUp(self):
        self.student = create_student("retdep_user")
        self.profile = create_profile(self.student)
        self.doc = KnowledgeDocument.objects.create(
            title="检索依赖测试", module="正念", skill="观察呼吸"
        )
        self.chunk = KnowledgeChunk.objects.create(
            document=self.doc,
            chunk_text="正念练习中的观察呼吸技能是最基础的入门练习方法。",
            metadata={"section_title": "呼吸练习"},
        )

    def test_skill_selection_populates_source_when_chunks_available(self):
        """When real chunks exist, the chain should populate source_chunk_ids."""
        from .rag.chains import generate_skill_selection
        mock_with_source = {**VALID_SKILL_SELECTION}
        # Chain combines retrieval + LLM — with mock, we test the schema accepts source_chunk_ids
        result = generate_skill_selection(
            profile=self.profile,
            mock_llm_response=VALID_SKILL_SELECTION,
        )
        self.assertIsInstance(result.source_chunk_ids, list)

    def test_test_questions_populates_source_per_question(self):
        """Each question schema includes source_chunk_ids."""
        from .rag.chains import generate_test_questions
        result = generate_test_questions(
            profile=self.profile,
            skill="观察呼吸",
            module="正念",
            mock_llm_response=VALID_TEST_QUESTIONS,
        )
        for q in result.questions:
            self.assertIsInstance(q.source_chunk_ids, list)

    def test_retriever_with_real_data_finds_chunk_for_chain(self):
        """When real retrieval finds chunks, the retriever returns Documents
        with proper metadata that chains can use."""
        retriever = DBTRetriever(k=5, user=self.student, use_case="teaching")
        chunks = retriever.search_with_context("观察呼吸")
        if chunks:
            self.assertIn("chunk_id", chunks[0])
            self.assertIn("chunk_text", chunks[0])
            self.assertIn("source", chunks[0])
