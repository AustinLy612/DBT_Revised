"""Tests for Step 10: Risk Identification & High-Risk Session Termination.

Coverage:
- Keyword risk detection (high-risk, moderate-risk, combined)
- Semantic risk detection (AI assessment triggered for non-keyword text)
- False positive control (normal emotional expressions not misclassified)
- RiskEvent data completeness (all required fields present)
- Session recovery after risk stop (new session allowed)
- Risk popup view (authenticated access, hotline content)
- Centralized risk services (keyword check, moderate concern, should_assess)
- Detection source classification (keyword / ai / both)
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import RiskEvent
from teaching.models import ChatMessage, TeachingSession

User = get_user_model()


# ── Helpers ──────────────────────────────────────────────────

def create_student(username: str, password: str = "testpass123") -> User:
    """Create a student user with profile completed."""
    from questionnaire.forms import ProfileForm
    user = User.objects.create_user(
        username=username,
        password=password,
        role=User.Role.STUDENT,
        profile_completed=True,
    )
    form = ProfileForm({
        "gender": "male", "age": 15, "grade": "grade_9",
        "hobby_tags": ["阅读", "游戏"], "concern_tags": ["学业压力"],
    })
    if form.is_valid():
        profile = form.save(commit=False)
        profile.user = user
        profile.save()
    return user


def create_session(user: User, phase: str = "teaching") -> TeachingSession:
    """Create a teaching session in the given phase."""
    return TeachingSession.objects.create(
        user=user,
        phase=phase,
        status=TeachingSession.Status.ONGOING,
        selected_skill="观察呼吸",
        selected_module="正念",
        teaching_plan={"plan_steps": []},
    )


# ── Mock data ────────────────────────────────────────────────

MOCK_RISK_HIGH = {
    "risk_level": "高",
    "risk_type": "自伤意图",
    "reasoning": "用户表达了明确的自伤意图，需要立即关注",
    "should_stop_session": True,
    "follow_up_action": "立即停止教学，引导用户寻求线下帮助",
    "source_chunk_ids": [],
}

MOCK_RISK_LOW = {
    "risk_level": "无",
    "risk_type": "",
    "reasoning": "正常情绪表达，不构成高风险",
    "should_stop_session": False,
    "follow_up_action": "",
    "source_chunk_ids": [],
}

MOCK_RISK_MODERATE = {
    "risk_level": "中",
    "risk_type": "情绪困扰",
    "reasoning": "用户表达了明显的情绪困扰但无自伤意图",
    "should_stop_session": False,
    "follow_up_action": "建议关注用户情绪状态",
    "source_chunk_ids": [],
}


# ═══════════════════════════════════════════════════════════
# 1. Keyword Detection Tests
# ═══════════════════════════════════════════════════════════

class KeywordDetectionTests(TestCase):
    """Test centralized keyword detection in risk.services."""

    def test_high_risk_keyword_suicide_detected(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我想自杀")
        self.assertTrue(triggered)
        self.assertIn("自杀", keywords)

    def test_high_risk_keyword_self_harm_detected(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我想伤害自己")
        self.assertTrue(triggered)
        self.assertIn("伤害自己", keywords)

    def test_high_risk_keyword_ending_life(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("活不下去了，想结束生命")
        self.assertTrue(triggered)
        self.assertIn("活不下去", keywords)
        self.assertIn("结束生命", keywords)

    def test_high_risk_keyword_cut_wrist(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我想割腕")
        self.assertTrue(triggered)
        self.assertIn("割腕", keywords)

    def test_moderate_keyword_despair(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我感到绝望")
        self.assertTrue(triggered)
        self.assertIn("绝望", keywords)

    def test_moderate_keyword_no_meaning(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("活着没意义")
        self.assertTrue(triggered)
        self.assertIn("活着没意义", keywords)

    def test_normal_text_no_risk(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("今天天气很好")
        self.assertFalse(triggered)
        self.assertEqual(keywords, [])

    def test_teaching_content_no_risk(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我觉得正念呼吸很有帮助")
        self.assertFalse(triggered)
        self.assertEqual(keywords, [])

    def test_multiple_keywords_in_one_sentence(self):
        from risk.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我想自杀，不想活了，我要跳楼")
        self.assertTrue(triggered)
        self.assertIn("自杀", keywords)
        self.assertIn("不想活了", keywords)
        self.assertIn("跳楼", keywords)

    def test_high_risk_keyword_list_is_complete(self):
        from risk.services import HIGH_RISK_KEYWORDS
        self.assertIn("自杀", HIGH_RISK_KEYWORDS)
        self.assertIn("自伤", HIGH_RISK_KEYWORDS)
        self.assertIn("自残", HIGH_RISK_KEYWORDS)
        self.assertIn("想死", HIGH_RISK_KEYWORDS)
        self.assertGreaterEqual(len(HIGH_RISK_KEYWORDS), 15)

    def test_moderate_keyword_list_is_complete(self):
        from risk.services import MODERATE_RISK_KEYWORDS
        self.assertIn("绝望", MODERATE_RISK_KEYWORDS)
        self.assertIn("我想杀人", MODERATE_RISK_KEYWORDS)
        self.assertGreaterEqual(len(MODERATE_RISK_KEYWORDS), 5)

    def test_keyword_can_be_imported_from_teaching(self):
        from teaching.services import check_keyword_risk
        triggered, _ = check_keyword_risk("我想自杀")
        self.assertTrue(triggered)

    def test_keyword_can_be_imported_from_testing(self):
        from testing.services import check_keyword_risk
        triggered, _ = check_keyword_risk("我想自杀")
        self.assertTrue(triggered)


# ═══════════════════════════════════════════════════════════
# 2. Moderate Concern Detection Tests
# ═══════════════════════════════════════════════════════════

class ModerateConcernTests(TestCase):
    """Test moderate concern indicators that warrant AI assessment."""

    def test_has_moderate_concern_life_no_meaning(self):
        from risk.services import has_moderate_concern
        self.assertTrue(has_moderate_concern("活得没意义"))

    def test_has_moderate_concern_existence_no_meaning(self):
        from risk.services import has_moderate_concern
        self.assertTrue(has_moderate_concern("存在没意义"))

    def test_has_moderate_concern_hate_self(self):
        from risk.services import has_moderate_concern
        self.assertTrue(has_moderate_concern("我恨我自己"))

    def test_has_moderate_concern_hurt_me(self):
        from risk.services import has_moderate_concern
        self.assertTrue(has_moderate_concern("伤害我"))

    def test_has_moderate_concern_normal_text(self):
        from risk.services import has_moderate_concern
        self.assertFalse(has_moderate_concern("今天心情不好，感觉很累"))

    def test_has_moderate_concern_empty_text(self):
        from risk.services import has_moderate_concern
        self.assertFalse(has_moderate_concern(""))

    def test_should_assess_risk_keyword_triggered(self):
        from risk.services import should_assess_risk
        self.assertTrue(should_assess_risk("我想自杀"))

    def test_should_assess_risk_moderate_concern(self):
        from risk.services import should_assess_risk
        self.assertTrue(should_assess_risk("活得没意义，不想活了"))

    def test_should_assess_risk_normal_text(self):
        from risk.services import should_assess_risk
        self.assertFalse(should_assess_risk("我喜欢学习DBT技能"))

    def test_should_assess_risk_emotional_but_safe(self):
        from risk.services import should_assess_risk
        self.assertFalse(should_assess_risk("我有点难过"))


# ═══════════════════════════════════════════════════════════
# 3. Process Risk Check Tests (Teaching)
# ═══════════════════════════════════════════════════════════

class ProcessRiskCheckTeachingTests(TestCase):
    """Test process_risk_check in the teaching context."""

    def setUp(self):
        self.user = create_student("risk_t1")
        self.session = create_session(self.user, phase="teaching")

    def test_normal_text_returns_none(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="无", should_stop=False)):
            result = process_risk_check(self.session, self.user, "我觉得正念很有用")
        self.assertIsNone(result)

    def test_keyword_triggers_ai_assessment_and_stops(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            result = process_risk_check(self.session, self.user, "我想自杀")
        self.assertIsNotNone(result)
        self.assertTrue(result["should_stop_session"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.STOPPED_BY_RISK)

    def test_keyword_triggers_risk_event_creation(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            process_risk_check(self.session, self.user, "我想自杀")
        event = RiskEvent.objects.first()
        self.assertIsNotNone(event)
        self.assertEqual(event.user, self.user)
        self.assertEqual(event.session, self.session)
        self.assertEqual(event.trigger_text, "我想自杀")
        self.assertTrue(event.session_stopped)

    def test_moderate_keyword_with_low_ai_result_does_not_stop(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="无", should_stop=False)):
            result = process_risk_check(self.session, self.user, "我感到绝望")
        self.assertIsNotNone(result)
        self.assertFalse(result["should_stop_session"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.ONGOING)

    def test_moderate_concern_triggers_ai_assessment(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="中", should_stop=False)):
            result = process_risk_check(self.session, self.user, "活得没意义")
        self.assertIsNotNone(result)

    def test_stop_session_creates_system_message(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            process_risk_check(self.session, self.user, "我想自杀")
        sys_msg = ChatMessage.objects.filter(role=ChatMessage.Role.SYSTEM).first()
        self.assertIsNotNone(sys_msg)
        self.assertIn("高风险", sys_msg.content)
        self.assertIn("会话已自动中止", sys_msg.content)

    def test_risk_event_detection_source_both(self):
        """When keyword triggers AND AI confirms high risk, source is 'both'."""
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            process_risk_check(self.session, self.user, "我想自杀")
        event = RiskEvent.objects.first()
        self.assertEqual(event.detection_source, "both")

    def test_risk_event_detection_source_keyword(self):
        """When keyword triggers but AI does not escalate, source is 'keyword'."""
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="无", should_stop=False)):
            process_risk_check(self.session, self.user, "我想杀人")
        event = RiskEvent.objects.first()
        self.assertEqual(event.detection_source, "keyword")


# ═══════════════════════════════════════════════════════════
# 4. Process Risk Check Tests (Testing)
# ═══════════════════════════════════════════════════════════

class ProcessRiskCheckTestingTests(TestCase):
    """Test process_test_risk_check in the testing context."""

    def setUp(self):
        from testing.models import Test
        self.user = create_student("risk_t2")
        self.session = create_session(self.user, phase="teaching")
        self.session.status = TeachingSession.Status.COMPLETED
        self.session.save()
        self.test = Test.objects.create(
            session=self.session,
            user=self.user,
            attempt_no=1,
            status=Test.Status.ONGOING,
        )

    def test_normal_answer_returns_none(self):
        from risk.services import process_test_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="无", should_stop=False)):
            result = process_test_risk_check(
                self.test, self.user, "使用TIPP技巧冷静下来"
            )
        self.assertIsNone(result)

    def test_keyword_in_answer_triggers_and_stops_test(self):
        from risk.services import process_test_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            result = process_test_risk_check(
                self.test, self.user, "我想自伤"
            )
        self.assertIsNotNone(result)
        self.assertTrue(result["should_stop_session"])
        self.test.refresh_from_db()
        self.assertEqual(self.test.status, self.test.Status.USER_TERMINATED)

    def test_keyword_in_answer_creates_risk_event(self):
        from risk.services import process_test_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            process_test_risk_check(self.test, self.user, "我想自残")
        event = RiskEvent.objects.first()
        self.assertIsNotNone(event)
        self.assertEqual(event.session, self.session)

    def test_moderate_concern_in_answer_triggers_ai(self):
        from risk.services import process_test_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="中", should_stop=False)):
            result = process_test_risk_check(
                self.test, self.user, "我恨我自己"
            )
        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════
# 5. RiskEvent Model & Data Completeness Tests
# ═══════════════════════════════════════════════════════════

class RiskEventModelTests(TestCase):
    """Test RiskEvent model creation and field constraints."""

    def setUp(self):
        self.user = create_student("risk_m1")
        self.session = create_session(self.user)

    def test_create_risk_event_with_all_fields(self):
        event = RiskEvent.objects.create(
            user=self.user,
            session=self.session,
            trigger_text="我想自杀",
            detection_source="both",
            action_taken="立即停止教学，记录风险事件",
            session_stopped=True,
            follow_up_mode="onsite_manual_followup",
        )
        self.assertIsNotNone(event.risk_event_id)
        self.assertEqual(len(event.risk_event_id), 36)

    def test_risk_event_defaults(self):
        event = RiskEvent.objects.create(
            user=self.user,
            session=self.session,
            trigger_text="测试文本",
        )
        self.assertEqual(event.detection_source, "keyword")
        self.assertTrue(event.session_stopped)
        self.assertEqual(event.follow_up_mode, "onsite_manual_followup")
        self.assertFalse(event.exported_flag)
        self.assertEqual(event.action_taken, "")

    def test_risk_event_trigger_time_auto_set(self):
        event = RiskEvent.objects.create(
            user=self.user,
            session=self.session,
            trigger_text="测试",
        )
        self.assertIsNotNone(event.trigger_time)

    def test_risk_event_related_to_user(self):
        event = RiskEvent.objects.create(
            user=self.user, session=self.session, trigger_text="测试",
        )
        self.assertEqual(self.user.risk_events.count(), 1)
        self.assertEqual(self.user.risk_events.first(), event)

    def test_risk_event_related_to_session(self):
        event = RiskEvent.objects.create(
            user=self.user, session=self.session, trigger_text="测试",
        )
        self.assertEqual(self.session.risk_events.count(), 1)

    def test_risk_event_detection_source_choices(self):
        event = RiskEvent.objects.create(
            user=self.user, session=self.session, trigger_text="测试",
            detection_source="ai",
        )
        self.assertEqual(event.detection_source, "ai")

    def test_create_risk_event_via_factory(self):
        from risk.services import create_risk_event
        event = create_risk_event(
            user=self.user,
            session=self.session,
            trigger_text="测试触发文本",
            detection_source="both",
            action_taken="已采取措施",
            session_stopped=True,
            follow_up_mode="onsite_manual_followup",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_text, "测试触发文本")
        self.assertEqual(event.detection_source, "both")
        self.assertEqual(event.action_taken, "已采取措施")
        self.assertTrue(event.session_stopped)


# ═══════════════════════════════════════════════════════════
# 6. False Positive Control Tests
# ═══════════════════════════════════════════════════════════

class FalsePositiveControlTests(TestCase):
    """Test that normal emotional expressions are NOT misclassified as risk.

    All tests mock AI to return risk_level="无" — with the dual-channel model
    AI always runs, and None is only returned when BOTH channels find nothing.
    """

    def setUp(self):
        self.user = create_student("risk_fp1")
        self.session = create_session(self.user, phase="teaching")
        self._ai_patcher = patch(
            "knowledge_base.rag.chains.run_risk_assessment",
            return_value=_make_risk_assessment(risk_level="无", should_stop=False),
        )
        self._ai_patcher.start()

    def tearDown(self):
        self._ai_patcher.stop()

    def test_sadness_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "我感到有点难过")
        self.assertIsNone(result)

    def test_academic_stress_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "我考试压力很大")
        self.assertIsNone(result)

    def test_loneliness_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "我感觉很孤独，没人理解我")
        self.assertIsNone(result)

    def test_anxiety_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "我经常担心考试考不好")
        self.assertIsNone(result)

    def test_family_conflict_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "我和父母最近总是吵架")
        self.assertIsNone(result)

    def test_sleep_issues_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "最近总是睡不好觉")
        self.assertIsNone(result)

    def test_low_self_esteem_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "我总觉得自己不够好")
        self.assertIsNone(result)

    def test_anger_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "我今天特别生气")
        self.assertIsNone(result)

    def test_emoji_only_not_misclassified(self):
        from risk.services import process_risk_check
        result = process_risk_check(self.session, self.user, "😟😢")
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════
# 7. AI Semantic Risk Detection Tests
# ═══════════════════════════════════════════════════════════

class SemanticRiskDetectionTests(TestCase):
    """Test that semantically risky text (without keywords) triggers AI assessment.

    With the dual-channel model, AI semantic assessment always runs on every
    message — keyword matching and LLM assessment are independent channels.
    """

    def setUp(self):
        self.user = create_student("risk_s1")
        self.session = create_session(self.user, phase="teaching")

    def test_hate_self_triggers_ai_assessment(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="中", should_stop=False)):
            result = process_risk_check(self.session, self.user, "我恨我")
        self.assertIsNotNone(result)

    def test_moderate_indicator_plus_context_stops_if_ai_confirms(self):
        """Moderate concern + AI confirms high risk → session stopped."""
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            result = process_risk_check(self.session, self.user, "伤害我自己")
        self.assertIsNotNone(result)
        self.assertTrue(result["should_stop_session"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.STOPPED_BY_RISK)

    def test_keyword_free_but_semantically_risky_with_ai_confirmation(self):
        """Text without explicit keywords but flagged by AI as high risk."""
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            # This text hits moderate concern indicator "伤害我"
            result = process_risk_check(self.session, self.user, "不想伤害自己了")
        self.assertIsNotNone(result)

    def test_detection_source_ai_only_when_keyword_not_triggered(self):
        """AI-only detection when only moderate concern triggers assessment."""
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            # "我恨我" is a moderate concern indicator, not a keyword
            process_risk_check(self.session, self.user, "我恨我")
        event = RiskEvent.objects.first()
        # "我恨我" is in MODERATE_CONCERN_INDICATORS but NOT in any keyword list
        from risk.services import check_keyword_risk
        kw_triggered, _ = check_keyword_risk("我恨我")
        if not kw_triggered:
            self.assertIn(event.detection_source, ["ai", "both"])

    def test_pure_semantic_detection_no_keywords_no_indicators(self):
        """AI channel detects high risk from text with no keywords or indicators."""
        from risk.services import check_keyword_risk, has_moderate_concern, process_risk_check
        text = "我觉得很累，不知道该怎么办"
        # Verify no keyword or moderate concern indicator matches
        kw_hit, _ = check_keyword_risk(text)
        self.assertFalse(kw_hit)
        self.assertFalse(has_moderate_concern(text))
        # AI channel flags it as high risk independently
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            result = process_risk_check(self.session, self.user, text)
        self.assertIsNotNone(result)
        self.assertTrue(result["should_stop_session"])
        self.assertEqual(result["risk_level"], "高")
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.STOPPED_BY_RISK)
        event = RiskEvent.objects.first()
        self.assertEqual(event.detection_source, "ai")
        self.assertEqual(event.trigger_text, text)


# ═══════════════════════════════════════════════════════════
# 8. Session Recovery Tests
# ═══════════════════════════════════════════════════════════

class SessionRecoveryTests(TestCase):
    """Test that user can start new session after risk stop."""

    def setUp(self):
        self.user = create_student("risk_rec1")
        self.client.login(username="risk_rec1", password="testpass123")

    def test_new_session_after_risk_stop(self):
        """After one session is stopped by risk, user can create a new one."""
        from risk.services import create_risk_event, stop_session_for_risk

        session1 = create_session(self.user, phase="teaching")
        event = create_risk_event(
            user=self.user, session=session1,
            trigger_text="我想自杀", detection_source="both",
        )
        stop_session_for_risk(session1, self.user)
        session1.refresh_from_db()
        self.assertEqual(session1.status, TeachingSession.Status.STOPPED_BY_RISK)

        # Create a new session — should succeed
        session2 = create_session(self.user, phase="pre_mood_recording")
        self.assertEqual(session2.status, TeachingSession.Status.ONGOING)
        self.assertNotEqual(session1.session_id, session2.session_id)

    def test_user_not_blocked_after_risk_stop(self):
        """User can still log in and access teaching after risk stop."""
        from risk.services import create_risk_event, stop_session_for_risk

        session1 = create_session(self.user, phase="teaching")
        event = create_risk_event(
            user=self.user, session=session1,
            trigger_text="我想自杀", detection_source="both",
        )
        stop_session_for_risk(session1, self.user)

        # User should still be able to access teaching home
        response = self.client.get(reverse("teaching:home"))
        self.assertEqual(response.status_code, 200)

    def test_risk_stopped_session_isolated(self):
        """Risk stop on session 1 does NOT affect session 2."""
        from risk.services import create_risk_event, stop_session_for_risk

        session1 = create_session(self.user, phase="teaching")
        stop_session_for_risk(session1, self.user)

        # Create a new teaching session
        from teaching.services import create_session as create_teaching_session
        session2 = create_teaching_session(self.user)
        self.assertEqual(session2.status, TeachingSession.Status.ONGOING)
        self.assertEqual(session2.phase, TeachingSession.Phase.PRE_MOOD_RECORDING)

    def test_multiple_risk_events_for_same_user_different_sessions(self):
        """User can have multiple risk events across different sessions."""
        from risk.services import create_risk_event, stop_session_for_risk

        session1 = create_session(self.user, phase="teaching")
        create_risk_event(
            user=self.user, session=session1,
            trigger_text="risk1", detection_source="keyword",
        )
        stop_session_for_risk(session1, self.user)

        session2 = create_session(self.user, phase="teaching")
        create_risk_event(
            user=self.user, session=session2,
            trigger_text="risk2", detection_source="both",
        )
        stop_session_for_risk(session2, self.user)

        self.assertEqual(RiskEvent.objects.filter(user=self.user).count(), 2)
        self.assertTrue(
            TeachingSession.objects.filter(
                user=self.user, status=TeachingSession.Status.STOPPED_BY_RISK
            ).count() >= 2
        )


# ═══════════════════════════════════════════════════════════
# 9. Risk Popup View Tests
# ═══════════════════════════════════════════════════════════

class RiskPopupViewTests(TestCase):
    """Test the risk popup page."""

    def setUp(self):
        self.user = create_student("risk_pop1")
        self.client.login(username="risk_pop1", password="testpass123")

    def test_popup_accessible_when_authenticated(self):
        response = self.client.get(reverse("risk:popup"))
        self.assertEqual(response.status_code, 200)

    def test_popup_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("risk:popup"))
        self.assertEqual(response.status_code, 302)

    def test_popup_contains_hotline_12356(self):
        response = self.client.get(reverse("risk:popup"))
        self.assertContains(response, "12356")

    def test_popup_contains_beijing_hotline(self):
        response = self.client.get(reverse("risk:popup"))
        self.assertContains(response, "010-82951332")

    def test_popup_contains_contact_teacher_prompt(self):
        response = self.client.get(reverse("risk:popup"))
        self.assertContains(response, "老师")

    def test_popup_contains_return_button(self):
        response = self.client.get(reverse("risk:popup"))
        self.assertContains(response, "返回教学首页")

    def test_popup_contains_immediate_danger_warning(self):
        response = self.client.get(reverse("risk:popup"))
        self.assertContains(response, "紧急帮助")


# ═══════════════════════════════════════════════════════════
# 10. Detection Source Classification Tests
# ═══════════════════════════════════════════════════════════

class DetectionSourceTests(TestCase):
    """Test that detection_source is correctly classified."""

    def setUp(self):
        self.user = create_student("risk_ds1")
        self.session = create_session(self.user, phase="teaching")

    def test_keyword_only_detection_marks_keyword(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="无", should_stop=False)):
            process_risk_check(self.session, self.user, "我想自杀")
        event = RiskEvent.objects.first()
        self.assertEqual(event.detection_source, "keyword")

    def test_both_detection_marks_both(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            process_risk_check(self.session, self.user, "我想自杀")
        event = RiskEvent.objects.first()
        self.assertEqual(event.detection_source, "both")

    def test_ai_only_detection_marks_ai(self):
        """When moderate concern triggers AI, and AI alone finds high risk."""
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            # "我恨我" only hits moderate concern, NOT keyword list
            process_risk_check(self.session, self.user, "我恨我")
        event = RiskEvent.objects.first()
        from risk.services import check_keyword_risk
        kw_hit, _ = check_keyword_risk("我恨我")
        if not kw_hit:
            self.assertEqual(event.detection_source, "ai")

    def test_follow_up_mode_on_risk_stop(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="高", should_stop=True)):
            process_risk_check(self.session, self.user, "我想自杀")
        event = RiskEvent.objects.first()
        self.assertEqual(event.follow_up_mode, "onsite_manual_followup")

    def test_follow_up_mode_no_action_when_not_stopped(self):
        from risk.services import process_risk_check
        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   return_value=_make_risk_assessment(risk_level="无", should_stop=False)):
            process_risk_check(self.session, self.user, "我想自杀")
        event = RiskEvent.objects.first()
        self.assertEqual(event.follow_up_mode, "no_action")


# ═══════════════════════════════════════════════════════════
# 11. Risk Admin Tests
# ═══════════════════════════════════════════════════════════

class RiskAdminTests(TestCase):
    """Test RiskEvent admin pages."""

    def setUp(self):
        from accounts.models import User
        self.admin = User.objects.filter(is_staff=True).first()
        if not self.admin:
            self.admin = User.objects.create_user(
                username="riskadmin", password="adminpass",
                role=User.Role.ADMIN, is_staff=True, is_superuser=True,
            )
        self.user = create_student("risk_adm1")
        self.session = create_session(self.user)
        RiskEvent.objects.create(
            user=self.user, session=self.session,
            trigger_text="测试风险文本",
            detection_source="keyword",
            session_stopped=True,
        )

    def test_admin_can_access_risk_event_list(self):
        self.client.login(username="riskadmin", password="adminpass")
        response = self.client.get(reverse("admin:risk_riskevent_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_view_risk_event_detail(self):
        self.client.login(username="riskadmin", password="adminpass")
        event = RiskEvent.objects.first()
        response = self.client.get(
            reverse("admin:risk_riskevent_change", args=[event.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_access_risk_admin(self):
        self.client.login(username="risk_adm1", password="testpass123")
        response = self.client.get(reverse("admin:risk_riskevent_changelist"))
        self.assertNotEqual(response.status_code, 200)


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _make_risk_assessment(risk_level: str, should_stop: bool):
    """Create a RiskAssessment Pydantic model that matches the schema."""
    from knowledge_base.rag.schemas import RiskAssessment
    return RiskAssessment(
        risk_level=risk_level,
        risk_type="自伤意图" if risk_level == "高" else "情绪困扰" if risk_level == "中" else "",
        reasoning="测试推理文本",
        should_stop_session=should_stop,
        follow_up_action=(
            "停止教学，引导寻求线下帮助" if should_stop
            else "建议关注" if risk_level == "中"
            else ""
        ),
        source_chunk_ids=[],
    )


# ═══════════════════════════════════════════════════════════
# 8. AI Risk Assessment Failure Scenarios (Step 13)
# ═══════════════════════════════════════════════════════════

class AIRiskAssessmentFailureTests(TestCase):
    """Test that the dual-channel risk system degrades gracefully when AI is unavailable.

    The keyword channel must still work and stop high-risk sessions even
    when the AI semantic assessment raises APIError.
    """

    def setUp(self):
        self.user = create_student("ai_fail_student")
        self.session = TeachingSession.objects.create(
            user=self.user,
            selected_skill="正念呼吸",
            status=TeachingSession.Status.ONGOING,
            phase="teaching",
        )

    def test_ai_failure_on_high_risk_keyword_still_stops_session(self):
        """When AI fails but keyword triggers high risk, session must still stop."""
        from risk.services import process_risk_check
        from knowledge_base.rag.llm_client import APIError

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=APIError("LLM unavailable")):
            result = process_risk_check(self.session, self.user, "我想自杀")

        self.assertIsNotNone(result)
        self.assertTrue(result["should_stop_session"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, TeachingSession.Status.STOPPED_BY_RISK)

    def test_ai_failure_on_high_risk_keyword_creates_risk_event(self):
        """When AI fails, keyword-triggered risk events must still be created."""
        from risk.services import process_risk_check
        from knowledge_base.rag.llm_client import APIError

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=APIError("LLM unavailable")):
            process_risk_check(self.session, self.user, "我想自杀")

        event = RiskEvent.objects.filter(user=self.user).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_text, "我想自杀")
        self.assertEqual(event.detection_source, "keyword")
        self.assertTrue(event.session_stopped)

    def test_ai_failure_on_normal_text_returns_none(self):
        """When AI fails and no keyword triggers, should return None (no false positive)."""
        from risk.services import process_risk_check
        from knowledge_base.rag.llm_client import APIError

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=APIError("LLM unavailable")):
            result = process_risk_check(self.session, self.user, "今天天气真好")

        self.assertIsNone(result)

    def test_ai_failure_on_moderate_concern_still_creates_event(self):
        """When AI fails but moderate concern keyword triggers, event is created."""
        from risk.services import process_risk_check
        from knowledge_base.rag.llm_client import APIError

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=APIError("LLM unavailable")):
            result = process_risk_check(self.session, self.user, "活着没意义")

        self.assertIsNotNone(result)
        event = RiskEvent.objects.filter(user=self.user, trigger_text="活着没意义").first()
        self.assertIsNotNone(event)
        # AI failed so detection is keyword-only
        self.assertEqual(event.detection_source, "keyword")

    def test_ai_failure_logs_error(self):
        """When AI fails, an error should be logged."""
        from risk.services import process_risk_check
        from knowledge_base.rag.llm_client import APIError
        import logging

        risk_logger = logging.getLogger("dbt_platform.risk")
        with self.assertLogs(risk_logger, level="ERROR") as log_cm:
            with patch("knowledge_base.rag.chains.run_risk_assessment",
                       side_effect=APIError("LLM unavailable")):
                process_risk_check(self.session, self.user, "我想自杀")

        self.assertTrue(
            any("AI risk assessment failed" in msg for msg in log_cm.output),
            f"Expected 'AI risk assessment failed' in log output, got: {log_cm.output}"
        )

    def test_ai_failure_on_unexpected_error_still_graceful(self):
        """Unexpected exceptions (not APIError) should also be caught gracefully."""
        from risk.services import process_risk_check

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=RuntimeError("Unexpected failure")):
            result = process_risk_check(self.session, self.user, "今天天气真好")

        self.assertIsNone(result)


class TestRiskCheckFailureTests(TestCase):
    """Test process_test_risk_check graceful degradation when AI fails."""

    def setUp(self):
        self.user = create_student("test_risk_fail")
        self.session = TeachingSession.objects.create(
            user=self.user,
            selected_skill="正念呼吸",
            status=TeachingSession.Status.ONGOING,
            phase="teaching",
        )
        from testing.models import Test
        self.test = Test.objects.create(
            user=self.user,
            session=self.session,
            attempt_no=1,
            total_questions=5,
            status="ongoing",
        )

    def test_ai_failure_on_high_risk_answer_still_terminates_test(self):
        """When AI fails but keyword triggers high risk, test must still terminate."""
        from risk.services import process_test_risk_check
        from knowledge_base.rag.llm_client import APIError

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=APIError("LLM unavailable")):
            result = process_test_risk_check(self.test, self.user, "我想自杀")

        self.assertIsNotNone(result)
        self.test.refresh_from_db()
        self.assertEqual(self.test.status, self.test.Status.USER_TERMINATED)

    def test_ai_failure_on_normal_answer_returns_none(self):
        """When AI fails and no keyword triggers in test, should return None."""
        from risk.services import process_test_risk_check
        from knowledge_base.rag.llm_client import APIError

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=APIError("LLM unavailable")):
            result = process_test_risk_check(self.test, self.user, "A")

        self.assertIsNone(result)

    def test_ai_failure_creates_risk_event_in_test_context(self):
        """When AI fails but keyword triggers, risk event must be created for test session."""
        from risk.services import process_test_risk_check
        from knowledge_base.rag.llm_client import APIError

        with patch("knowledge_base.rag.chains.run_risk_assessment",
                   side_effect=APIError("LLM unavailable")):
            process_test_risk_check(self.test, self.user, "我想自杀")

        event = RiskEvent.objects.filter(user=self.user, session=self.session).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_text, "我想自杀")

    def test_ai_failure_logs_error_in_test_context(self):
        """When AI fails in testing context, an error should be logged."""
        from risk.services import process_test_risk_check
        from knowledge_base.rag.llm_client import APIError
        import logging

        risk_logger = logging.getLogger("dbt_platform.risk")
        with self.assertLogs(risk_logger, level="ERROR") as log_cm:
            with patch("knowledge_base.rag.chains.run_risk_assessment",
                       side_effect=APIError("LLM unavailable")):
                process_test_risk_check(self.test, self.user, "我想自杀")

        self.assertTrue(
            any("AI risk assessment failed in testing" in msg for msg in log_cm.output),
            f"Expected 'AI risk assessment failed in testing' in log output, got: {log_cm.output}"
        )
