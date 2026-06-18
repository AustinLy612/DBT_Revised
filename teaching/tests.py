"""Tests for Step 7: Teaching Session Main Flow.

Coverage:
- Session creation & skill selection
- Teaching dialogue (send message, get AI response)
- Session state transitions (skill_selection → teaching → completed/stopped/terminated)
- Risk detection (keyword-based + recording)
- Data persistence (chat messages, rag context, summary, plan)
- Authorization (session ownership, profile_required)

Mock strategy:
- View tests mock teaching.services functions (the API boundary that calls RAG)
- Service tests mock knowledge_base.rag.chains functions (the DeepSeek/Qdrant boundary)
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from knowledge_base.rag.llm_client import APIError

from .models import ChatMessage, TeachingSession

User = get_user_model()

# ── Mock chain output values ──

MOCK_PERSONAL_INQUIRY = {
    "greeting": "谢谢你分享你此刻的心情。学习压力大的时候确实会让人感到疲惫。",
    "question": "最近一周学校里或者家里有什么事情让你觉得特别有压力，或者让你感到开心吗？",
    "inquiry_focus": "近期学业和情绪状态",
}

MOCK_SKILL_SELECTION = {
    "selected_module": "正念",
    "selected_skill": "观察呼吸",
    "reason": "适合初学者，有助于情绪管理",
    "skill_difficulty": "初级",
    "alternative_skills": ["情绪命名"],
    "source_chunk_ids": ["chunk_001"],
}

MOCK_TEACHING_PLAN = {
    "module": "正念",
    "skill": "观察呼吸",
    "plan_steps": [
        {"step_number": 1, "title": "导入", "content": "介绍正念概念", "estimated_minutes": 5},
        {"step_number": 2, "title": "演示", "content": "示范呼吸方法", "estimated_minutes": 10},
        {"step_number": 3, "title": "练习", "content": "带领呼吸练习", "estimated_minutes": 10},
    ],
    "estimated_total_minutes": 25,
    "prerequisites": [],
    "source_chunk_ids": ["chunk_002"],
}

MOCK_TEACHING_CONTENT = {
    "message_type": "讲解",
    "content": "正念是一种将注意力集中在当下的练习。让我们从观察呼吸开始。",
    "question": "",
    "source_chunk_ids": ["chunk_003"],
    "confidence": "high",
}

MOCK_TEACHING_SUMMARY = {
    "skill_covered": "观察呼吸",
    "key_points": ["正念的定义", "呼吸观察方法", "日常应用"],
    "student_understanding": "良好",
    "recommendations": ["每天练习5分钟"],
    "summary_text": "学生掌握了观察呼吸的基本技巧。",
}

MOCK_RISK_LOW = {
    "risk_level": "无",
    "risk_type": "",
    "reasoning": "正常对话",
    "should_stop_session": False,
    "follow_up_action": "",
    "triggered_keywords": [],
}

MOCK_RISK_HIGH = {
    "risk_level": "高",
    "risk_type": "自伤",
    "reasoning": "检测到明确自伤意图",
    "should_stop_session": True,
    "follow_up_action": "立即中止会话，联系专业人员",
    "triggered_keywords": ["自杀"],
}


# ── Helpers ──

def create_student(username="student"):
    """Create a student user with completed profile."""
    from questionnaire.forms import ProfileForm

    user = User.objects.create_user(username=username, password="testpass123", role="student")
    user.profile_completed = True
    user.save()
    form = ProfileForm({
        "gender": "male",
        "age": 15,
        "grade": "grade_9",
        "hobby_tags": ["音乐", "阅读"],
        "concern_tags": ["学业压力"],
    })
    if form.is_valid():
        profile = form.save(commit=False)
        profile.user = user
        profile.save()
    return user


def create_session(user):
    """Create a teaching session in skill_selection phase."""
    return TeachingSession.objects.create(
        user=user,
        selected_skill="观察呼吸",
        selected_module="正念",
        selection_reason="适合初学者",
        rag_context_ids=["chunk_001"],
        phase=TeachingSession.Phase.SKILL_SELECTION,
        status=TeachingSession.Status.ONGOING,
    )


# ═══════════════════════════════════════════════════════════
# View Test Mixin — patches chain functions and retriever
# ═══════════════════════════════════════════════════════════

class ViewTestMixin:
    """Mixin that patches RAG chain functions and retriever for view tests.

    Patches at the knowledge_base.rag level so the REAL teaching.services
    functions run (and update session fields), but without calling DeepSeek
    or Qdrant.

    The retriever is replaced with a mock so search_with_context returns
    empty results (no Qdrant/embedding model needed).
    """

    _patches: list = []

    @classmethod
    def start_service_patches(cls):
        if cls._patches:
            return  # already started
        from knowledge_base.rag.schemas import (
            PersonalInquiryResult, SkillSelectionResult, TeachingPlan,
            TeachingContent, TeachingSummary, RiskAssessment,
        )

        # Mock retriever — returns empty chunks (no Qdrant needed)
        mock_ret = MagicMock()
        mock_ret.search_with_context.return_value = []
        mock_ret._get_relevant_documents.return_value = []

        cls._patches = [
            patch("knowledge_base.rag.retriever.get_retriever",
                  return_value=mock_ret),
            patch("knowledge_base.rag.chains.generate_personal_inquiry",
                  return_value=PersonalInquiryResult(**MOCK_PERSONAL_INQUIRY)),
            patch("knowledge_base.rag.chains.generate_skill_selection",
                  return_value=SkillSelectionResult(**MOCK_SKILL_SELECTION)),
            patch("knowledge_base.rag.chains.generate_teaching_plan",
                  return_value=TeachingPlan(**MOCK_TEACHING_PLAN)),
            patch("knowledge_base.rag.chains.generate_teaching_content",
                  return_value=TeachingContent(**MOCK_TEACHING_CONTENT)),
            patch("knowledge_base.rag.chains.generate_teaching_summary",
                  return_value=TeachingSummary(**MOCK_TEACHING_SUMMARY)),
            patch("knowledge_base.rag.chains.run_risk_assessment",
                  return_value=RiskAssessment(**MOCK_RISK_LOW)),
        ]
        for p in cls._patches:
            p.start()

    @classmethod
    def stop_service_patches(cls):
        for p in cls._patches:
            p.stop()
        cls._patches.clear()


# ═══════════════════════════════════════════════════════════
# Session Creation & Skill Selection Tests
# ═══════════════════════════════════════════════════════════

class SessionCreationTests(TestCase):
    """Test creating a session and the skill selection flow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s1")
        self.client.login(username="s1", password="testpass123")

    def test_start_session_creates_record(self):
        self.assertEqual(TeachingSession.objects.count(), 0)
        self.client.post(reverse("teaching:start"))
        self.assertEqual(TeachingSession.objects.count(), 1)

    def test_start_session_requires_post(self):
        response = self.client.get(reverse("teaching:start"))
        self.assertRedirects(response, reverse("teaching:home"))

    def test_start_session_redirects_to_session_page(self):
        response = self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.assertRedirects(response, reverse("teaching:session", args=[session.session_id]))

    def test_start_session_sets_initial_state(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.assertEqual(session.phase, TeachingSession.Phase.PRE_MOOD_RECORDING)
        self.assertEqual(session.status, TeachingSession.Status.ONGOING)

    def test_pre_mood_advances_to_personal_inquiry(self):
        """After pre-mood recording, phase advances to personal_inquiry (not skill_selection)."""
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.client.post(
            reverse("teaching:record_pre_mood", args=[session.session_id]),
            {"mood_value": 3},
        )
        session.refresh_from_db()
        self.assertEqual(session.phase, TeachingSession.Phase.PERSONAL_INQUIRY)
        self.assertTrue(session.pre_mood_id)

    def test_personal_inquiry_triggers_skill_selection(self):
        """After submitting personal context, skill selection runs."""
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        # Record pre-mood → personal_inquiry
        self.client.post(
            reverse("teaching:record_pre_mood", args=[session.session_id]),
            {"mood_value": 3},
        )
        session.refresh_from_db()
        self.assertEqual(session.phase, TeachingSession.Phase.PERSONAL_INQUIRY)

        # Submit personal context → skill_selection
        self.client.post(
            reverse("teaching:personal_inquiry", args=[session.session_id]),
            {"personal_context": "最近考试压力很大，经常感到焦虑。"},
        )
        session.refresh_from_db()
        self.assertEqual(session.phase, TeachingSession.Phase.SKILL_SELECTION)
        self.assertEqual(session.selected_module, "正念")
        self.assertEqual(session.selected_skill, "观察呼吸")
        self.assertEqual(session.selection_reason, "适合初学者，有助于情绪管理")
        self.assertIn("chunk_001", session.rag_context_ids)
        self.assertEqual(session.personal_context, "最近考试压力很大，经常感到焦虑。")

    def test_start_requires_profile_completed(self):
        user2 = User.objects.create_user(username="noprofile", password="testpass123", role="student")
        self.client.login(username="noprofile", password="testpass123")
        response = self.client.post(reverse("teaching:start"))
        self.assertEqual(response.status_code, 302)

    def test_start_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.post(reverse("teaching:start"))
        self.assertEqual(response.status_code, 302)

    def test_personal_inquiry_fails_gracefully_on_api_error(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        # Record pre-mood → personal_inquiry
        self.client.post(
            reverse("teaching:record_pre_mood", args=[session.session_id]),
            {"mood_value": 3},
        )
        session.refresh_from_db()
        self.assertEqual(session.phase, TeachingSession.Phase.PERSONAL_INQUIRY)

        # Submit personal context → API error during skill selection
        with patch("knowledge_base.rag.chains.generate_skill_selection",
                   side_effect=APIError("API error")):
            response = self.client.post(
                reverse("teaching:personal_inquiry", args=[session.session_id]),
                {"personal_context": "最近压力很大。"},
            )
        self.assertRedirects(response, reverse("teaching:session", args=[session.session_id]))
        session.refresh_from_db()
        # On error, phase reverts to info_collection for retry
        self.assertEqual(session.phase, TeachingSession.Phase.INFO_COLLECTION)

    def test_personal_inquiry_requires_post(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.client.post(
            reverse("teaching:record_pre_mood", args=[session.session_id]),
            {"mood_value": 3},
        )
        session.refresh_from_db()
        response = self.client.get(
            reverse("teaching:personal_inquiry", args=[session.session_id]),
        )
        self.assertRedirects(response, reverse("teaching:session", args=[session.session_id]))

    def test_personal_inquiry_empty_context_rejected(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.client.post(
            reverse("teaching:record_pre_mood", args=[session.session_id]),
            {"mood_value": 3},
        )
        session.refresh_from_db()
        response = self.client.post(
            reverse("teaching:personal_inquiry", args=[session.session_id]),
            {"personal_context": "   "},
        )
        self.assertRedirects(response, reverse("teaching:session", args=[session.session_id]))
        # Phase should still be personal_inquiry
        session.refresh_from_db()
        self.assertEqual(session.phase, TeachingSession.Phase.PERSONAL_INQUIRY)

    def test_personal_inquiry_wrong_phase_rejected(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        response = self.client.post(
            reverse("teaching:personal_inquiry", args=[session.session_id]),
            {"personal_context": "hello"},
        )
        self.assertRedirects(response, reverse("teaching:session", args=[session.session_id]))


class SkillConfirmationTests(TestCase):
    """Test confirming the skill selection and generating the teaching plan."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s2")
        self.client.login(username="s2", password="testpass123")
        self.session = create_session(self.user)

    def test_confirm_skill_generates_plan(self):
        self.assertEqual(self.session.phase, TeachingSession.Phase.SKILL_SELECTION)
        response = self.client.post(
            reverse("teaching:confirm_skill", args=[self.session.session_id])
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.phase, TeachingSession.Phase.TEACHING)
        self.assertIn("plan_steps", self.session.teaching_plan)
        self.assertEqual(len(self.session.teaching_plan["plan_steps"]), 3)
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))

    def test_confirm_skill_with_custom_override(self):
        response = self.client.post(
            reverse("teaching:confirm_skill", args=[self.session.session_id]),
            {"custom_skill": "情绪调节"},
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.selected_skill, "情绪调节")
        self.assertEqual(self.session.phase, TeachingSession.Phase.TEACHING)

    def test_confirm_skill_requires_post(self):
        response = self.client.get(
            reverse("teaching:confirm_skill", args=[self.session.session_id])
        )
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))

    def test_confirm_skill_wrong_phase_rejected(self):
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.save()
        response = self.client.post(
            reverse("teaching:confirm_skill", args=[self.session.session_id])
        )
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))

    def test_confirm_skill_wrong_owner_rejected(self):
        other = create_student("other_skill_confirm")
        self.client.login(username="other_skill_confirm", password="testpass123")
        response = self.client.post(
            reverse("teaching:confirm_skill", args=[self.session.session_id])
        )
        self.assertEqual(response.status_code, 404)

    def test_confirm_skill_fails_gracefully_on_api_error(self):
        with patch("knowledge_base.rag.chains.generate_teaching_plan",
                   side_effect=APIError("LLM error")):
            response = self.client.post(
                reverse("teaching:confirm_skill", args=[self.session.session_id])
            )
            self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))
            self.session.refresh_from_db()
            self.assertEqual(self.session.phase, TeachingSession.Phase.SKILL_SELECTION)


# ═══════════════════════════════════════════════════════════
# Teaching Dialogue Tests
# ═══════════════════════════════════════════════════════════

class TeachingDialogueTests(TestCase):
    """Test sending messages and getting AI responses."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s3")
        self.client.login(username="s3", password="testpass123")
        self.session = create_session(self.user)
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.teaching_plan = MOCK_TEACHING_PLAN
        self.session.save()
        self.url = reverse("teaching:send_message", args=[self.session.session_id])

    def test_send_message_saves_user_and_ai_messages(self):
        pre_count = ChatMessage.objects.count()
        self.client.post(self.url, {"message": "什么是正念？"})
        self.assertEqual(ChatMessage.objects.count(), pre_count + 2)  # user + assistant

    def test_send_message_ai_response_contains_content(self):
        self.client.post(self.url, {"message": "什么是正念？"})
        ai_msg = ChatMessage.objects.filter(role=ChatMessage.Role.ASSISTANT).first()
        self.assertIsNotNone(ai_msg)
        self.assertIn("正念", ai_msg.content)

    def test_send_message_updates_rag_context(self):
        self.client.post(self.url, {"message": "什么是正念？"})
        self.session.refresh_from_db()
        self.assertIn("chunk_003", self.session.rag_context_ids)

    def test_send_message_empty_rejected(self):
        pre_count = ChatMessage.objects.count()
        response = self.client.post(self.url, {"message": "   "})
        self.assertEqual(ChatMessage.objects.count(), pre_count)

    def test_send_message_requires_post(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_send_message_session_ended_rejected(self):
        self.session.status = TeachingSession.Status.COMPLETED
        self.session.save()
        response = self.client.post(self.url, {"message": "hi"})

    def test_send_message_wrong_owner_rejected(self):
        other = create_student("other_send_msg")
        self.client.login(username="other_send_msg", password="testpass123")
        response = self.client.post(self.url, {"message": "hello"})
        self.assertEqual(response.status_code, 404)

    def test_multiple_messages_in_sequence(self):
        for _ in range(3):
            self.client.post(self.url, {"message": "一条消息"})
        user_msgs = ChatMessage.objects.filter(role=ChatMessage.Role.USER)
        ai_msgs = ChatMessage.objects.filter(role=ChatMessage.Role.ASSISTANT)
        self.assertEqual(user_msgs.count(), 3)
        self.assertEqual(ai_msgs.count(), 3)

    def test_send_message_returns_htmx_partial(self):
        response = self.client.post(self.url, {"message": "你好"})
        self.assertContains(response, "chat-messages")
        self.assertContains(response, "正念")

    def test_htmx_partial_includes_autoplay_script(self):
        """The HTMX partial includes the autoPlayLatest() call for TTS auto-play."""
        response = self.client.post(self.url, {"message": "你好"})
        self.assertContains(response, "DBT_TTS.autoPlayLatest()")

    def test_htmx_partial_has_data_role_attributes(self):
        """HTMX partial messages include data-role for auto-play JS detection."""
        response = self.client.post(self.url, {"message": "你好"})
        self.assertContains(response, 'data-role="assistant"')

    def test_send_message_fails_gracefully_on_api_error(self):
        with patch("knowledge_base.rag.chains.generate_teaching_content",
                   side_effect=APIError("LLM error")):
            response = self.client.post(self.url, {"message": "你好"})
            self.assertContains(response, "不可用")


# ═══════════════════════════════════════════════════════════
# Session Completion & Termination Tests
# ═══════════════════════════════════════════════════════════

class SessionCompletionTests(TestCase):
    """Test ending and terminating the teaching session."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s4")
        self.client.login(username="s4", password="testpass123")
        self.session = create_session(self.user)
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.teaching_plan = MOCK_TEACHING_PLAN
        self.session.save()
        ChatMessage.objects.create(session=self.session, user=self.user,
                                   role=ChatMessage.Role.USER, content="hello")
        ChatMessage.objects.create(session=self.session, user=self.user,
                                   role=ChatMessage.Role.ASSISTANT, content="你好！")

    def test_end_session_sets_completed(self):
        self.client.post(reverse("teaching:end_session", args=[self.session.session_id]))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.COMPLETED)
        self.assertIsNotNone(self.session.completed_at)

    def test_end_session_generates_summary(self):
        self.client.post(reverse("teaching:end_session", args=[self.session.session_id]))
        self.session.refresh_from_db()
        self.assertIn("观察呼吸", self.session.teaching_summary)

    def test_end_session_creates_system_message(self):
        self.client.post(reverse("teaching:end_session", args=[self.session.session_id]))
        sys_msg = ChatMessage.objects.filter(role=ChatMessage.Role.SYSTEM).first()
        self.assertIsNotNone(sys_msg)
        self.assertIn("教学已完成", sys_msg.content)

    def test_end_session_already_ended_noop(self):
        self.session.status = TeachingSession.Status.COMPLETED
        self.session.save()
        response = self.client.post(reverse("teaching:end_session", args=[self.session.session_id]))
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))

    def test_end_session_requires_post(self):
        response = self.client.get(reverse("teaching:end_session", args=[self.session.session_id]))
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))

    def test_end_session_fails_gracefully_on_api_error(self):
        with patch("knowledge_base.rag.chains.generate_teaching_summary",
                   side_effect=APIError("LLM error")):
            response = self.client.post(reverse("teaching:end_session", args=[self.session.session_id]))
            self.session.refresh_from_db()
            self.assertEqual(self.session.status, TeachingSession.Status.USER_TERMINATED)

    def test_terminate_session_sets_terminated(self):
        self.client.post(reverse("teaching:terminate", args=[self.session.session_id]))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.USER_TERMINATED)
        self.assertIsNotNone(self.session.completed_at)

    def test_terminate_session_requires_post(self):
        response = self.client.get(reverse("teaching:terminate", args=[self.session.session_id]))
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))


# ═══════════════════════════════════════════════════════════
# Session Page Display Tests
# ═══════════════════════════════════════════════════════════

class SessionPageTests(TestCase):
    """Test the session page renders correctly in each phase."""

    def setUp(self):
        self.user = create_student("s5")
        self.client.login(username="s5", password="testpass123")
        self.session = create_session(self.user)

    def test_pre_mood_recording_phase_shows_mood_selector(self):
        self.session.phase = TeachingSession.Phase.PRE_MOOD_RECORDING
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "教学前心情记录")

    def test_skill_selection_phase_shows_recommendation(self):
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "观察呼吸")
        self.assertContains(response, "技能推荐")

    def test_teaching_phase_shows_plan(self):
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.teaching_plan = MOCK_TEACHING_PLAN
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, "教学计划")
        self.assertContains(response, "导入")

    def test_completed_phase_shows_summary(self):
        self.session.status = TeachingSession.Status.COMPLETED
        self.session.teaching_summary = "学生掌握了正念基础。"
        self.session.completed_at = self.session.started_at
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, "教学已完成")
        self.assertContains(response, "学生掌握了正念基础")

    def test_stopped_by_risk_shows_message(self):
        self.session.status = TeachingSession.Status.STOPPED_BY_RISK
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, "会话已中止")

    def test_user_terminated_shows_message(self):
        self.session.status = TeachingSession.Status.USER_TERMINATED
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, "已终止")

    def test_conversation_displayed(self):
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.save()
        ChatMessage.objects.create(session=self.session, user=self.user,
                                   role=ChatMessage.Role.USER, content="测试消息")
        ChatMessage.objects.create(session=self.session, user=self.user,
                                   role=ChatMessage.Role.ASSISTANT, content="测试回复")
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, "测试消息")
        self.assertContains(response, "测试回复")

    def test_wrong_owner_gets_404(self):
        other = create_student("other_session_page")
        self.client.login(username="other_session_page", password="testpass123")
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 404)

    def test_personal_inquiry_phase_shows_inquiry(self):
        self.session.phase = TeachingSession.Phase.PERSONAL_INQUIRY
        self.session.pre_mood_id = "test-mood-id"
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "在开始之前，先聊聊你最近的情况")
        self.assertContains(response, "分享你最近的经历")

    def test_unauthenticated_redirected(self):
        self.client.logout()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 302)


# ═══════════════════════════════════════════════════════════
# Risk Detection Tests
# ═══════════════════════════════════════════════════════════

class TTSAutoPlayTests(TestCase):
    """Test TTS auto-play toggle rendering and chat message data attributes."""

    def setUp(self):
        self.user = create_student("tts_autoplay")
        self.client.login(username="tts_autoplay", password="testpass123")
        self.session = create_session(self.user)

    def test_autoplay_toggle_rendered_in_teaching_phase(self):
        """TTS auto-play toggle exists in teaching phase UI."""
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.teaching_plan = MOCK_TEACHING_PLAN
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, "tts-autoplay-toggle")
        self.assertContains(response, "自动播报")

    def test_autoplay_toggle_not_in_pre_mood_phase(self):
        """Toggle only appears during active teaching, not pre-mood."""
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertNotContains(response, "tts-autoplay-toggle")

    def test_chat_messages_have_data_role_attributes(self):
        """Chat messages include data-role for auto-play JS detection."""
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.save()
        ChatMessage.objects.create(session=self.session, user=self.user,
                                   role=ChatMessage.Role.ASSISTANT, content="AI回复")
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, 'data-role="assistant"')

    def test_media_js_loaded_on_session_page(self):
        """The media.js file with DBT_TTS client is present on session page."""
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.teaching_plan = MOCK_TEACHING_PLAN
        self.session.save()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertContains(response, "media.js")


class RiskDetectionTests(TestCase):
    """Test keyword-based risk detection during teaching.

    Overrides the run_risk_assessment mock to return HIGH risk so
    the real check_keyword_risk + process_risk_check logic exercises
    the stop-session path.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s6")
        self.client.login(username="s6", password="testpass123")
        self.session = create_session(self.user)
        self.session.phase = TeachingSession.Phase.TEACHING
        self.session.teaching_plan = MOCK_TEACHING_PLAN
        self.session.save()
        self.url = reverse("teaching:send_message", args=[self.session.session_id])

    def test_high_risk_keyword_stops_session(self):
        from knowledge_base.rag.schemas import RiskAssessment
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=RiskAssessment(**MOCK_RISK_HIGH)):
            # Also need to patch generate_teaching_content to avoid error
            from knowledge_base.rag.schemas import TeachingContent
            with patch("knowledge_base.rag.chains.generate_teaching_content",
                       return_value=TeachingContent(**MOCK_TEACHING_CONTENT)):
                self.client.post(self.url, {"message": "我想自杀"})
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.STOPPED_BY_RISK)

    def test_risk_event_created_on_keyword(self):
        from knowledge_base.rag.schemas import RiskAssessment
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=RiskAssessment(**MOCK_RISK_HIGH)):
            from knowledge_base.rag.schemas import TeachingContent
            with patch("knowledge_base.rag.chains.generate_teaching_content",
                       return_value=TeachingContent(**MOCK_TEACHING_CONTENT)):
                self.client.post(self.url, {"message": "我想自杀"})
        self.session.refresh_from_db()
        from risk.models import RiskEvent
        event = RiskEvent.objects.first()
        self.assertIsNotNone(event)
        self.assertEqual(event.user, self.user)

    def test_normal_message_no_risk(self):
        # Default MOCK_RISK_LOW (risk_level="无") — session stays ongoing
        self.client.post(self.url, {"message": "我感觉有点难过"})
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.ONGOING)

    def test_system_message_after_risk_stop(self):
        from knowledge_base.rag.schemas import RiskAssessment
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=RiskAssessment(**MOCK_RISK_HIGH)):
            from knowledge_base.rag.schemas import TeachingContent
            with patch("knowledge_base.rag.chains.generate_teaching_content",
                       return_value=TeachingContent(**MOCK_TEACHING_CONTENT)):
                self.client.post(self.url, {"message": "我想自杀"})
        sys_msgs = ChatMessage.objects.filter(role=ChatMessage.Role.SYSTEM)
        self.assertTrue(sys_msgs.exists())


# ═══════════════════════════════════════════════════════════
# Data Persistence Tests
# ═══════════════════════════════════════════════════════════

class DataPersistenceTests(TestCase):
    """Test that all session data is correctly persisted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s7")
        self.client.login(username="s7", password="testpass123")

    def test_full_session_data_traceable(self):
        # 1. Start session
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()

        # 2. Record pre-mood → personal_inquiry
        self.client.post(reverse("teaching:record_pre_mood", args=[session.session_id]),
                         {"mood_value": 3})
        session.refresh_from_db()

        # 3. Submit personal context → skill_selection
        self.client.post(reverse("teaching:personal_inquiry", args=[session.session_id]),
                         {"personal_context": "最近考试压力很大。"})
        session.refresh_from_db()

        # 4. Confirm skill → generates teaching plan
        self.client.post(reverse("teaching:confirm_skill", args=[session.session_id]))
        session.refresh_from_db()

        # 5. Send messages
        for msg in ["什么是正念？", "怎么练习？", "我试了一下，感觉不错"]:
            self.client.post(
                reverse("teaching:send_message", args=[session.session_id]),
                {"message": msg},
            )

        # 6. End session
        self.client.post(reverse("teaching:end_session", args=[session.session_id]))
        session.refresh_from_db()

        # Verify
        self.assertEqual(session.status, TeachingSession.Status.COMPLETED)
        self.assertIsNotNone(session.completed_at)
        self.assertTrue(session.teaching_summary)
        self.assertTrue(session.selection_reason)
        self.assertTrue(session.teaching_plan)
        self.assertTrue(session.rag_context_ids)
        self.assertTrue(session.personal_context)

        # Messages
        user_msgs = ChatMessage.objects.filter(session=session, role=ChatMessage.Role.USER).count()
        ai_msgs = ChatMessage.objects.filter(session=session, role=ChatMessage.Role.ASSISTANT).count()
        self.assertEqual(user_msgs, 3)
        self.assertEqual(ai_msgs, 3)

    def test_selected_skill_persisted(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.client.post(reverse("teaching:record_pre_mood", args=[session.session_id]),
                         {"mood_value": 3})
        self.client.post(reverse("teaching:personal_inquiry", args=[session.session_id]),
                         {"personal_context": "最近压力大。"})
        session.refresh_from_db()
        self.assertEqual(session.selected_module, "正念")
        self.assertEqual(session.selected_skill, "观察呼吸")

    def test_teaching_plan_persisted(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.client.post(reverse("teaching:record_pre_mood", args=[session.session_id]),
                         {"mood_value": 3})
        self.client.post(reverse("teaching:personal_inquiry", args=[session.session_id]),
                         {"personal_context": "最近压力大。"})
        self.client.post(reverse("teaching:confirm_skill", args=[session.session_id]))
        session.refresh_from_db()
        self.assertTrue(session.teaching_plan)
        self.assertIn("plan_steps", session.teaching_plan)

    def test_conversation_history_retrievable(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.client.post(reverse("teaching:record_pre_mood", args=[session.session_id]),
                         {"mood_value": 3})
        self.client.post(reverse("teaching:personal_inquiry", args=[session.session_id]),
                         {"personal_context": "最近压力大。"})
        self.client.post(reverse("teaching:confirm_skill", args=[session.session_id]))
        self.client.post(
            reverse("teaching:send_message", args=[session.session_id]),
            {"message": "你好"},
        )

        from .services import get_conversation_history
        history = get_conversation_history(session)
        self.assertGreaterEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    def test_rag_context_accumulates(self):
        self.client.post(reverse("teaching:start"))
        session = TeachingSession.objects.first()
        self.client.post(reverse("teaching:record_pre_mood", args=[session.session_id]),
                         {"mood_value": 3})
        self.client.post(reverse("teaching:personal_inquiry", args=[session.session_id]),
                         {"personal_context": "最近压力大。"})
        session.refresh_from_db()
        initial_ids = list(session.rag_context_ids)

        self.client.post(reverse("teaching:confirm_skill", args=[session.session_id]))
        session.refresh_from_db()
        after_plan_ids = list(session.rag_context_ids)

        self.assertGreaterEqual(len(after_plan_ids), len(initial_ids))


# ═══════════════════════════════════════════════════════════
# State Transition Tests (service-level, mock chains directly)
# ═══════════════════════════════════════════════════════════

class StateTransitionTests(TestCase):
    """Test correctness of session state transitions at the service level.

    Patches the chain functions in knowledge_base.rag.chains directly
    so we test the REAL service layer orchestration.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from knowledge_base.rag.schemas import (
            SkillSelectionResult,
            TeachingPlan,
            TeachingSummary,
            RiskAssessment,
        )
        cls._patches = [
            patch("knowledge_base.rag.chains.generate_teaching_plan",
                  return_value=TeachingPlan(**MOCK_TEACHING_PLAN)),
            patch("knowledge_base.rag.chains.generate_teaching_summary",
                  return_value=TeachingSummary(**MOCK_TEACHING_SUMMARY)),
            patch("knowledge_base.rag.chains.run_risk_assessment",
                  return_value=RiskAssessment(**MOCK_RISK_HIGH)),
        ]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s8")

    def test_initial_state_is_pre_mood_recording_and_ongoing(self):
        session = TeachingSession.objects.create(user=self.user)
        self.assertEqual(session.phase, TeachingSession.Phase.PRE_MOOD_RECORDING)
        self.assertEqual(session.status, TeachingSession.Status.ONGOING)

    def test_pre_mood_to_personal_inquiry_transition(self):
        from .services import run_pre_mood
        session = TeachingSession.objects.create(
            user=self.user,
            phase=TeachingSession.Phase.PRE_MOOD_RECORDING,
        )
        run_pre_mood(session, self.user, mood_value=4, emoji="🙂")
        session.refresh_from_db()
        self.assertEqual(session.phase, TeachingSession.Phase.PERSONAL_INQUIRY)
        self.assertTrue(session.pre_mood_id)

    def test_personal_inquiry_to_skill_selection_transition(self):
        from .services import run_personal_inquiry
        from knowledge_base.rag.schemas import SkillSelectionResult as Ssr
        session = TeachingSession.objects.create(
            user=self.user,
            phase=TeachingSession.Phase.PERSONAL_INQUIRY,
            pre_mood_id="test-mood-id",
        )
        with patch("knowledge_base.rag.chains.generate_skill_selection",
                   return_value=Ssr(**MOCK_SKILL_SELECTION)):
            mock_ret = MagicMock()
            mock_ret.search_with_context.return_value = []
            with patch("knowledge_base.rag.retriever.get_retriever", return_value=mock_ret):
                run_personal_inquiry(session, self.user, "最近考试压力很大。")
        session.refresh_from_db()
        self.assertEqual(session.phase, TeachingSession.Phase.SKILL_SELECTION)
        self.assertEqual(session.personal_context, "最近考试压力很大。")
        self.assertEqual(session.selected_skill, "观察呼吸")

    def test_skill_to_teaching_transition(self):
        from .services import run_teaching_plan
        session = TeachingSession.objects.create(
            user=self.user,
            selected_skill="观察呼吸",
            phase=TeachingSession.Phase.SKILL_SELECTION,
        )
        run_teaching_plan(session, self.user)
        self.assertEqual(session.phase, TeachingSession.Phase.TEACHING)
        self.assertEqual(session.status, TeachingSession.Status.ONGOING)

    def test_teaching_to_completed_transition(self):
        from .services import generate_session_summary
        session = TeachingSession.objects.create(
            user=self.user,
            selected_skill="观察呼吸",
            phase=TeachingSession.Phase.TEACHING,
            teaching_plan=MOCK_TEACHING_PLAN,
        )
        history = [{"role": "user", "content": "test"}]
        generate_session_summary(session, self.user, history)
        session.refresh_from_db()
        self.assertEqual(session.status, TeachingSession.Status.COMPLETED)

    def test_terminate_transition(self):
        from .services import terminate_session
        session = TeachingSession.objects.create(user=self.user)
        terminate_session(session)
        session.refresh_from_db()
        self.assertEqual(session.status, TeachingSession.Status.USER_TERMINATED)

    def test_risk_stop_transition(self):
        from .services import process_risk_check
        session = TeachingSession.objects.create(
            user=self.user,
            selected_skill="观察呼吸",
            phase=TeachingSession.Phase.TEACHING,
        )
        result = process_risk_check(session, self.user, "我想自杀", [])
        session.refresh_from_db()
        self.assertEqual(session.status, TeachingSession.Status.STOPPED_BY_RISK)
        self.assertIsNotNone(result)

    def test_skill_selection_reads_test_records(self):
        """run_skill_selection queries testing.Test for performance history."""
        from .services import run_skill_selection
        from testing.models import Test as TestModel

        session = TeachingSession.objects.create(
            user=self.user,
            phase=TeachingSession.Phase.SKILL_SELECTION,
        )
        prev_session = TeachingSession.objects.create(
            user=self.user,
            selected_skill="情绪调节",
            phase=TeachingSession.Phase.TEACHING,
        )
        TestModel.objects.create(
            session=prev_session,
            user=self.user,
            total_questions=5,
            correct_count=2,
            passed=False,
            status=TestModel.Status.COMPLETED,
        )

        from knowledge_base.rag.schemas import SkillSelectionResult
        with patch("knowledge_base.rag.chains.generate_skill_selection",
                   return_value=SkillSelectionResult(**MOCK_SKILL_SELECTION)):
            mock_ret = MagicMock()
            mock_ret.search_with_context.return_value = []
            with patch("knowledge_base.rag.retriever.get_retriever", return_value=mock_ret):
                run_skill_selection(session, self.user)

        session.refresh_from_db()
        self.assertEqual(session.selected_module, "正念")
        self.assertEqual(session.selected_skill, "观察呼吸")
        self.assertIn("chunk_001", session.rag_context_ids)


# ═══════════════════════════════════════════════════════════
# Authorization Tests
# ═══════════════════════════════════════════════════════════

class AuthorizationTests(TestCase):
    """Test authorization rules for teaching session access."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("s9")
        self.other = create_student("other9")
        self.session = create_session(self.user)

    def test_owner_can_access_session(self):
        self.client.login(username="s9", password="testpass123")
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 200)

    def test_non_owner_cannot_access_session(self):
        self.client.login(username="other9", password="testpass123")
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_confirm_skill(self):
        self.client.login(username="other9", password="testpass123")
        response = self.client.post(reverse("teaching:confirm_skill", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_record_pre_mood(self):
        self.client.login(username="other9", password="testpass123")
        self.session.phase = TeachingSession.Phase.PRE_MOOD_RECORDING
        self.session.save()
        response = self.client.post(
            reverse("teaching:record_pre_mood", args=[self.session.session_id]),
            {"mood_value": 3},
        )
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_send_message(self):
        self.client.login(username="other9", password="testpass123")
        response = self.client.post(
            reverse("teaching:send_message", args=[self.session.session_id]),
            {"message": "hello"},
        )
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_end_session(self):
        self.client.login(username="other9", password="testpass123")
        response = self.client.post(reverse("teaching:end_session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_terminate(self):
        self.client.login(username="other9", password="testpass123")
        response = self.client.post(reverse("teaching:terminate", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_redirected(self):
        self.client.logout()
        response = self.client.get(reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(response.status_code, 302)

    def test_profile_required_on_teaching_home(self):
        user2 = User.objects.create_user(username="np2", password="testpass123", role="student")
        self.client.login(username="np2", password="testpass123")
        response = self.client.get(reverse("teaching:home"))
        self.assertEqual(response.status_code, 302)


# ═══════════════════════════════════════════════════════════
# Teaching Home View Tests
# ═══════════════════════════════════════════════════════════

class TeachingHomeTests(TestCase):
    """Test the teaching home page."""

    def setUp(self):
        self.user = create_student("s10")
        self.client.login(username="s10", password="testpass123")

    def test_home_shows_profile_info(self):
        response = self.client.get(reverse("teaching:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "s10")

    def test_home_shows_start_button(self):
        response = self.client.get(reverse("teaching:home"))
        self.assertContains(response, "开始新教学")

    def test_home_shows_recent_sessions(self):
        TeachingSession.objects.create(
            user=self.user, selected_skill="正念", status=TeachingSession.Status.COMPLETED
        )
        response = self.client.get(reverse("teaching:home"))
        self.assertContains(response, "正念")

    def test_home_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.get(reverse("teaching:home"))
        self.assertEqual(response.status_code, 302)


# ═══════════════════════════════════════════════════════════
# Keyword Risk Detection Unit Tests
# ═══════════════════════════════════════════════════════════

class KeywordRiskUnitTests(TestCase):
    """Test the keyword risk detection in isolation."""

    def test_high_risk_keywords_detected(self):
        from .services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我想自杀")
        self.assertTrue(triggered)
        self.assertIn("自杀", keywords)

    def test_moderate_keywords_detected(self):
        from .services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我感到绝望")
        self.assertTrue(triggered)
        self.assertIn("绝望", keywords)

    def test_normal_text_no_risk(self):
        from .services import check_keyword_risk
        triggered, keywords = check_keyword_risk("今天天气很好")
        self.assertFalse(triggered)
        self.assertEqual(keywords, [])

    def test_multiple_keywords_detected(self):
        from .services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我想自杀，不想活了")
        self.assertTrue(triggered)
        self.assertIn("自杀", keywords)
        self.assertIn("不想活了", keywords)

    def test_moderate_concern_detected(self):
        from .services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我感到绝望")
        self.assertTrue(triggered)
        self.assertIn("绝望", keywords)
