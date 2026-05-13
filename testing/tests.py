"""Tests for Step 8: Testing, Per-Question Explanations & Unlimited Retesting.

Coverage:
- Test creation from completed teaching session
- Question generation via RAG (mock)
- Per-question answering with immediate explanation (HTMX)
- Test completion with pass/fail determination
- Retest (unlimited, new questions each attempt)
- Test termination
- Authorization (ownership, profile_required)
- Risk detection during test
- Flow integration (start → answer all → finish → retest if needed)
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from knowledge_base.rag.llm_client import APIError

from .models import Test, TestQuestion

User = get_user_model()

# Letter-to-index mapping for answer comparisons (same as views._LETTER_TO_INDEX)
_LETTER_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}


def _ans_is_correct(letter: str, correct_option: str) -> bool:
    """Check if a letter answer matches the stored correct_option index."""
    idx = _LETTER_TO_INDEX.get(letter, -1)
    return str(idx) == str(correct_option).strip()


# ── Mock chain output values ──

MOCK_TEST_QUESTIONS = {
    "questions": [
        {
            "question_text": "当你在学校被同学欺负后感到非常愤怒，以下哪种 DBT 技巧最适合首先使用？",
            "options": [
                "立刻找老师投诉",
                "使用 TIPP 温度技巧冷静下来",
                "回家后报复对方",
                "写日记记录今天发生的事",
            ],
            "correct_option": 1,
            "explanation": "TIPP 温度技巧是 DBT 痛苦耐受模块的核心技能，通过改变生理状态快速降低情绪强度。在愤怒情绪高涨时先冷静再处理问题更为有效。",
            "source_chunk_ids": ["chunk_t001"],
        },
        {
            "question_text": "正念呼吸练习时，以下哪种做法是正确的？",
            "options": [
                "强迫自己不能有任何杂念",
                "观察呼吸，发现走神后温和地回到呼吸上",
                "必须闭眼才能进行正念呼吸",
                "每次练习至少需要30分钟才有效",
            ],
            "correct_option": 1,
            "explanation": "正念的核心是觉察当下而不评判。走神是自然现象，关键是在发现后温和地将注意力带回。",
            "source_chunk_ids": ["chunk_t002"],
        },
        {
            "question_text": "小明因为考试成绩不理想感到极度沮丧，他应该使用哪种 DBT 技能？",
            "options": [
                "逃避情绪，打游戏转移注意力",
                "使用情绪调节技能中的'相反行为'",
                "责备自己没有努力",
                "放弃学习",
            ],
            "correct_option": 1,
            "explanation": "相反行为是 DBT 情绪调节模块中的重要技能。当情绪驱使我们做无效行为时，采取相反的有效行为来改变情绪状态。",
            "source_chunk_ids": ["chunk_t003"],
        },
        {
            "question_text": "关于 DBT 中的'智慧心念'概念，以下哪种描述最准确？",
            "options": [
                "完全依靠逻辑推理做决定",
                "只听从内心的情绪冲动",
                "综合理性思维和情绪感受的最优决策状态",
                "听从他人都建议就是智慧心念",
            ],
            "correct_option": 2,
            "explanation": "智慧心念（Wise Mind）是 DBT 的核心理念之一，指的是理性思维和情绪感受的平衡状态，在这种状态下做出的决策既考虑了事实也尊重了感受。",
            "source_chunk_ids": ["chunk_t004"],
        },
        {
            "question_text": "以下哪项不是 DBT 技能的四大核心模块之一？",
            "options": [
                "正念",
                "痛苦耐受",
                "认知重塑",
                "情绪调节",
            ],
            "correct_option": 2,
            "explanation": "DBT 的四大核心模块是正念（Mindfulness）、痛苦耐受（Distress Tolerance）、情绪调节（Emotion Regulation）和人际效能（Interpersonal Effectiveness）。认知重塑是 CBT 的技术，不属于 DBT 四大模块。",
            "source_chunk_ids": ["chunk_t005"],
        },
    ],
    "test_difficulty": "初级",
}

MOCK_TEST_QUESTIONS_RETEST = {
    "questions": [
        {
            "question_text": "在进行 DEAR MAN 技巧练习时，第一步应该做什么？",
            "options": [
                "直接提出自己的需求",
                "描述当前的情境和事实",
                "表达自己的感受",
                "强调需求的重要性",
            ],
            "correct_option": 1,
            "explanation": "DEAR MAN 是 DBT 人际效能模块中的技能。D=Describe（描述情境），这是第一步，要先客观描述发生了什么。",
            "source_chunk_ids": ["chunk_t101"],
        },
        {
            "question_text": "当情绪强烈时，使用\"STOP\"技能的第一步是什么？",
            "options": ["思考后果", "观察环境", "停止当前行为", "采取有效行动"],
            "correct_option": 2,
            "explanation": "STOP 技能：S=Stop（停下），T=Take a step back（退一步），O=Observe（观察），P=Proceed mindfully（正念地行动）。第一步是停止。",
            "source_chunk_ids": ["chunk_t102"],
        },
        {
            "question_text": "以下哪种情况最适合使用\"接纳现实\"技能？",
            "options": [
                "明天要参加一场重要面试",
                "得知最好的朋友即将转学",
                "计划周末的生日派对",
                "决定要报考哪个大学",
            ],
            "correct_option": 1,
            "explanation": "接纳现实（Radical Acceptance）适用于那些无法改变的事实或已经发生的事件。朋友的转学是无法改变的事实，需要通过接纳来减少痛苦。",
            "source_chunk_ids": ["chunk_t103"],
        },
        {
            "question_text": "在 DBT 中，\"5-4-3-2-1\" 技巧属于哪个技能类别？",
            "options": ["情绪调节", "正念", "人际效能", "痛苦耐受"],
            "correct_option": 1,
            "explanation": "5-4-3-2-1 是正念练习中的接地技巧，通过关注感官体验（5个看到的、4个听到的等）将注意力带回当下。",
            "source_chunk_ids": ["chunk_t104"],
        },
        {
            "question_text": "关于\"情绪日记\"的使用，以下哪种做法是最推荐的？",
            "options": [
                "只在情绪不好的时候写",
                "每天固定时间记录情绪和相关事件",
                "只记录好的情绪",
                "让家长帮忙记录",
            ],
            "correct_option": 1,
            "explanation": "定期记录情绪有助于建立情绪觉察能力，识别情绪触发模式和趋势。DBT 推荐规律的情绪追踪以提高情绪调节能力。",
            "source_chunk_ids": ["chunk_t105"],
        },
    ],
    "test_difficulty": "初级",
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


def create_completed_session(user):
    """Create a completed teaching session ready for testing."""
    from teaching.models import TeachingSession

    session = TeachingSession.objects.create(
        user=user,
        selected_skill="观察呼吸",
        selected_module="正念",
        selection_reason="适合初学者",
        teaching_summary="学生掌握了正念呼吸的基本技巧；理解了正念的核心概念；可以进行基本的呼吸观察练习。",
        rag_context_ids=["chunk_001"],
        phase=TeachingSession.Phase.TEACHING,
        status=TeachingSession.Status.COMPLETED,
    )
    return session


# ═══════════════════════════════════════════════════════════════
# View Test Mixin
# ═══════════════════════════════════════════════════════════════

class ViewTestMixin:
    """Mixin that patches RAG chain functions and retriever for view tests.

    Patches at the knowledge_base.rag level. testing/services.py does lazy
    imports (import inside functions), so when the functions run they look up
    knowledge_base.rag.chains.* and knowledge_base.rag.retriever.* at call time
    — which is when the patches are active.
    """

    _patches: list = []

    @classmethod
    def start_service_patches(cls, test_questions=None):
        if cls._patches:
            return
        from knowledge_base.rag.schemas import RiskAssessment, TestQuestions

        mock_ret = MagicMock()
        mock_ret.search_with_context.return_value = []

        questions_data = test_questions if test_questions is not None else MOCK_TEST_QUESTIONS

        cls._patches = [
            patch("knowledge_base.rag.retriever.get_retriever",
                  return_value=mock_ret),
            patch("knowledge_base.rag.chains.generate_test_questions",
                  return_value=TestQuestions(**questions_data)),
            patch("knowledge_base.rag.chains.run_risk_assessment",
                  return_value=RiskAssessment(
                      risk_level="无", risk_type="", reasoning="正常对话",
                      should_stop_session=False, follow_up_action="",
                      triggered_keywords=[],
                  )),
        ]
        for p in cls._patches:
            p.start()

    @classmethod
    def stop_service_patches(cls):
        for p in cls._patches:
            p.stop()
        cls._patches.clear()


# ═══════════════════════════════════════════════════════════════
# Test Creation Tests
# ═══════════════════════════════════════════════════════════════

class TestCreationTests(TestCase):
    """Test creation of tests from completed teaching sessions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester1")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester1", password="testpass123")

    def test_start_test_creates_record(self):
        self.assertEqual(Test.objects.count(), 0)
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.assertEqual(Test.objects.count(), 1)

    def test_start_test_redirects_to_test_page(self):
        response = self.client.post(reverse("testing:start", args=[self.session.session_id]))
        test = Test.objects.first()
        self.assertRedirects(response, reverse("testing:test", args=[test.test_id]))

    def test_start_test_generates_5_questions(self):
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        test = Test.objects.first()
        self.assertEqual(TestQuestion.objects.filter(test=test).count(), 5)

    def test_start_test_sets_initial_state(self):
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        test = Test.objects.first()
        self.assertEqual(test.status, Test.Status.ONGOING)
        self.assertEqual(test.attempt_no, 1)
        self.assertFalse(test.passed)
        self.assertEqual(test.correct_count, 0)

    def test_start_test_requires_post(self):
        response = self.client.get(reverse("testing:start", args=[self.session.session_id]))
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))

    def test_start_test_requires_completed_session(self):
        self.session.status = self.session.Status.ONGOING
        self.session.save()
        response = self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))
        self.assertEqual(Test.objects.count(), 0)

    def test_start_test_graceful_api_error(self):
        with patch("knowledge_base.rag.chains.generate_test_questions",
                   side_effect=APIError("test error")):
            response = self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.assertRedirects(response, reverse("teaching:session", args=[self.session.session_id]))
        # Test record is created but marked terminated on API failure
        self.assertEqual(Test.objects.count(), 1)
        test = Test.objects.first()
        self.assertEqual(test.status, Test.Status.USER_TERMINATED)

    def test_test_page_loads(self):
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        test = Test.objects.first()
        response = self.client.get(reverse("testing:test", args=[test.test_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "第 1 题")
        self.assertContains(response, "共 5 题")


# ═══════════════════════════════════════════════════════════════
# Answer Question Tests
# ═══════════════════════════════════════════════════════════════

class AnswerQuestionTests(TestCase):
    """Test per-question answering with immediate explanation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester2")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester2", password="testpass123")
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()
        self.question = TestQuestion.objects.filter(test=self.test).first()

    def test_answer_question_saves_answer(self):
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "B"},
        )
        self.question.refresh_from_db()
        self.assertEqual(self.question.user_answer, "B")

    def test_answer_question_returns_htmx_partial(self):
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "B"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "回答")
        self.assertContains(response, "解析")

    def test_answer_correct_shows_right_text(self):
        correct_letter = "B"  # correct_option=1 → B
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": correct_letter},
        )
        self.assertContains(response, "回答正确")

    def test_answer_wrong_shows_error_text(self):
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "A"},
        )
        self.assertContains(response, "回答错误")

    def test_answer_shows_explanation(self):
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "A"},
        )
        self.assertContains(response, "TIPP")

    def test_answer_requires_post(self):
        response = self.client.get(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "B"},
        )
        self.assertEqual(response.status_code, 405)

    def test_cannot_answer_twice(self):
        self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "B"},
        )
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "A"},
        )
        self.assertContains(response, "已经作答")

    def test_answer_invalid_option_rejected(self):
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": self.question.question_id, "answer": "X"},
        )
        self.assertContains(response, "请选择一个有效选项")

    def test_answer_missing_question_id(self):
        response = self.client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"answer": "B"},
        )
        self.assertContains(response, "缺少题目ID")


# ═══════════════════════════════════════════════════════════════
# Test Completion Tests
# ═══════════════════════════════════════════════════════════════

class TestCompletionTests(TestCase):
    """Test test completion with pass/fail determination."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester3")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester3", password="testpass123")
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()

    def _answer_all(self, answers):
        """Answer all 5 questions with given answers ['A','B','C','D','B'] etc."""
        questions = list(TestQuestion.objects.filter(test=self.test).order_by("created_at"))
        for q, ans in zip(questions, answers):
            q.user_answer = ans
            q.is_correct = _ans_is_correct(ans, q.correct_option)
            q.save()

    def test_finish_test_with_all_correct_passes(self):
        self._answer_all(["B", "B", "B", "C", "C"])
        response = self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.test.refresh_from_db()
        self.assertEqual(self.test.status, Test.Status.COMPLETED)
        self.assertTrue(self.test.passed)
        self.assertEqual(self.test.correct_count, 5)

    def test_finish_test_4_correct_passes(self):
        self._answer_all(["B", "B", "B", "C", "A"])  # last one wrong
        response = self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.test.refresh_from_db()
        self.assertTrue(self.test.passed)
        self.assertEqual(self.test.correct_count, 4)

    def test_finish_test_3_correct_fails(self):
        # With answers: B, A, A, A, A → only Q1 correct
        self._answer_all(["B", "A", "A", "A", "A"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.test.refresh_from_db()
        self.assertFalse(self.test.passed)
        self.assertEqual(self.test.correct_count, 1)

    def test_finish_test_2_correct_fails(self):
        self._answer_all(["B", "A", "A", "A", "A"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.test.refresh_from_db()
        self.assertFalse(self.test.passed)
        self.assertEqual(self.test.correct_count, 1)

    def test_finish_test_requires_post(self):
        response = self.client.get(reverse("testing:finish", args=[self.test.test_id]))
        self.assertRedirects(response, reverse("testing:test", args=[self.test.test_id]))

    def test_cannot_finish_with_unanswered_questions(self):
        response = self.client.post(
            reverse("testing:finish", args=[self.test.test_id]), follow=True
        )
        self.test.refresh_from_db()
        self.assertEqual(self.test.status, Test.Status.ONGOING)
        self.assertContains(response, "未作答")

    def test_completed_test_page_shows_result(self):
        self._answer_all(["B", "B", "B", "C", "C"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "测试通过")

    def test_completed_page_shows_question_review(self):
        self._answer_all(["B", "B", "B", "C", "C"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "题目回顾")


# ═══════════════════════════════════════════════════════════════
# Retest Tests
# ═══════════════════════════════════════════════════════════════

class RetestTests(TestCase):
    """Test unlimited retesting with new questions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester4")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester4", password="testpass123")
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()

    def _answer_all(self, answers):
        questions = list(TestQuestion.objects.filter(test=self.test).order_by("created_at"))
        for q, ans in zip(questions, answers):
            q.user_answer = ans
            q.is_correct = _ans_is_correct(ans, q.correct_option)
            q.save()

    def test_retest_creates_new_test(self):
        self._answer_all(["A", "A", "A", "A", "A"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.assertEqual(Test.objects.count(), 1)

        response = self.client.post(reverse("testing:retest", args=[self.test.test_id]))
        self.assertEqual(Test.objects.count(), 2)

    def test_retest_increments_attempt_no(self):
        self._answer_all(["A", "A", "A", "A", "A"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))

        self.client.post(reverse("testing:retest", args=[self.test.test_id]))
        new_test = Test.objects.order_by("-created_at").first()
        self.assertEqual(new_test.attempt_no, 2)

    def test_retest_generates_new_questions(self):
        self._answer_all(["A", "A", "A", "A", "A"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.client.post(reverse("testing:retest", args=[self.test.test_id]))
        new_test = Test.objects.order_by("-created_at").first()
        self.assertEqual(TestQuestion.objects.filter(test=new_test).count(), 5)

    def test_retest_redirects_to_new_test_page(self):
        self._answer_all(["A", "A", "A", "A", "A"])
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        response = self.client.post(reverse("testing:retest", args=[self.test.test_id]))
        new_test = Test.objects.order_by("-created_at").first()
        self.assertRedirects(response, reverse("testing:test", args=[new_test.test_id]))

    def test_unlimited_retests(self):
        for attempt in range(1, 5):
            # Complete previous test
            self._answer_all(["A", "A", "A", "A", "A"])
            self.client.post(reverse("testing:finish", args=[self.test.test_id]))
            # Retest
            self.client.post(reverse("testing:retest", args=[self.test.test_id]))
            self.test = Test.objects.order_by("-created_at").first()
            self.assertEqual(self.test.attempt_no, attempt + 1)

    def test_retest_requires_post(self):
        response = self.client.get(reverse("testing:retest", args=[self.test.test_id]))
        self.assertRedirects(response, reverse("testing:test", args=[self.test.test_id]))


# ═══════════════════════════════════════════════════════════════
# Test Termination Tests
# ═══════════════════════════════════════════════════════════════

class TestTerminationTests(TestCase):
    """Test user-initiated test termination."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester5")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester5", password="testpass123")
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()

    def test_terminate_sets_status(self):
        response = self.client.post(reverse("testing:terminate", args=[self.test.test_id]))
        self.test.refresh_from_db()
        self.assertEqual(self.test.status, Test.Status.USER_TERMINATED)

    def test_terminate_redirects_to_test_page(self):
        response = self.client.post(reverse("testing:terminate", args=[self.test.test_id]))
        self.assertRedirects(response, reverse("testing:test", args=[self.test.test_id]))

    def test_terminate_requires_post(self):
        response = self.client.get(reverse("testing:terminate", args=[self.test.test_id]))
        self.assertRedirects(response, reverse("testing:test", args=[self.test.test_id]))

    def test_terminated_test_page_shows_info(self):
        self.client.post(reverse("testing:terminate", args=[self.test.test_id]))
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "已终止")

    def test_cannot_terminate_already_completed(self):
        # Answer all questions and finish
        questions = list(TestQuestion.objects.filter(test=self.test).order_by("created_at"))
        for q in questions:
            q.user_answer = "B"
            q.is_correct = (q.correct_option == "1")
            q.save()
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))

        response = self.client.post(reverse("testing:terminate", args=[self.test.test_id]))
        self.test.refresh_from_db()
        self.assertEqual(self.test.status, Test.Status.COMPLETED)


# ═══════════════════════════════════════════════════════════════
# Authorization Tests
# ═══════════════════════════════════════════════════════════════

class AuthorizationTests(TestCase):
    """Test that users can only access their own tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.owner = create_student("owner")
        self.intruder = create_student("intruder")
        self.session = create_completed_session(self.owner)
        self.owner_client = self.client
        self.owner_client.login(username="owner", password="testpass123")
        self.owner_client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()

    def test_owner_can_access_test(self):
        response = self.owner_client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertEqual(response.status_code, 200)

    def test_non_owner_cannot_access_test(self):
        intruder_client = self.client.__class__()
        intruder_client.login(username="intruder", password="testpass123")
        response = intruder_client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_answer(self):
        intruder_client = self.client.__class__()
        intruder_client.login(username="intruder", password="testpass123")
        question = TestQuestion.objects.filter(test=self.test).first()
        response = intruder_client.post(
            reverse("testing:answer", args=[self.test.test_id]),
            {"question_id": question.question_id, "answer": "B"},
        )
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_finish(self):
        intruder_client = self.client.__class__()
        intruder_client.login(username="intruder", password="testpass123")
        response = intruder_client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_retest(self):
        intruder_client = self.client.__class__()
        intruder_client.login(username="intruder", password="testpass123")
        response = intruder_client.post(reverse("testing:retest", args=[self.test.test_id]))
        self.assertEqual(response.status_code, 404)

    def test_non_owner_cannot_terminate(self):
        intruder_client = self.client.__class__()
        intruder_client.login(username="intruder", password="testpass123")
        response = intruder_client.post(reverse("testing:terminate", args=[self.test.test_id]))
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_redirected(self):
        self.owner_client.logout()
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertEqual(response.status_code, 302)


# ═══════════════════════════════════════════════════════════════
# Test Page UI Tests
# ═══════════════════════════════════════════════════════════════

class TestPageUITests(TestCase):
    """Test the test page UI content and rendering."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester6")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester6", password="testpass123")
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()

    def test_ongoing_test_shows_questions(self):
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "第 1 题")
        self.assertContains(response, "进度")

    def test_ongoing_test_shows_options(self):
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "A.")
        self.assertContains(response, "B.")
        self.assertContains(response, "C.")
        self.assertContains(response, "D.")

    def test_ongoing_test_shows_skill_name(self):
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "观察呼吸")

    def test_ongoing_test_shows_back_link(self):
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "返回教学会话")

    def test_ongoing_test_has_terminate_button(self):
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "终止")

    def test_failed_test_shows_retest_button(self):
        questions = list(TestQuestion.objects.filter(test=self.test).order_by("created_at"))
        answers = ["A", "A", "A", "A", "A"]  # all wrong
        for q, ans in zip(questions, answers):
            q.user_answer = ans
            q.is_correct = _ans_is_correct(ans, q.correct_option)
            q.save()
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertContains(response, "重新测试")

    def test_passed_test_does_not_show_retest_button(self):
        questions = list(TestQuestion.objects.filter(test=self.test).order_by("created_at"))
        for i, q in enumerate(questions):
            # Mock questions have correct_option: [1, 1, 1, 2, 2] → letters: B, B, B, C, C
            letter = ["B", "B", "B", "C", "C"][i]
            q.user_answer = letter
            q.is_correct = _ans_is_correct(letter, q.correct_option)
            q.save()
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        response = self.client.get(reverse("testing:test", args=[self.test.test_id]))
        self.assertNotContains(response, "重新测试")


# ═══════════════════════════════════════════════════════════════
# Data Persistence Tests
# ═══════════════════════════════════════════════════════════════

class DataPersistenceTests(TestCase):
    """Test that test data is fully persisted and traceable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester7")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester7", password="testpass123")
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()

    def test_full_test_flow_persists_data(self):
        """Complete a full test flow and verify all data is traceable."""
        questions = list(TestQuestion.objects.filter(test=self.test).order_by("created_at"))
        answers = ["B", "B", "B", "C", "C"]

        for q, ans in zip(questions, answers):
            self.client.post(
                reverse("testing:answer", args=[self.test.test_id]),
                {"question_id": q.question_id, "answer": ans},
            )
            q.refresh_from_db()

        # All answers saved
        self.assertTrue(all(q.user_answer for q in TestQuestion.objects.filter(test=self.test)))

        # Finish
        self.client.post(reverse("testing:finish", args=[self.test.test_id]))
        self.test.refresh_from_db()

        # Test record
        self.assertEqual(self.test.status, Test.Status.COMPLETED)
        self.assertTrue(self.test.passed)
        self.assertEqual(self.test.correct_count, 5)
        self.assertEqual(self.test.attempt_no, 1)

        # Question records
        questions = list(TestQuestion.objects.filter(test=self.test).order_by("created_at"))
        for q in questions:
            self.assertTrue(q.user_answer)
            self.assertTrue(q.explanation)
            self.assertTrue(q.question_text)
            self.assertEqual(len(q.options), 4)

    def test_question_source_chunks_persisted(self):
        question = TestQuestion.objects.filter(test=self.test).first()
        self.assertTrue(len(question.source_chunk_ids) > 0)

    def test_test_linked_to_session(self):
        self.assertEqual(self.test.session.session_id, self.session.session_id)

    def test_test_linked_to_user(self):
        self.assertEqual(self.test.user.id, self.user.id)


# ═══════════════════════════════════════════════════════════════
# Risk Detection Tests
# ═══════════════════════════════════════════════════════════════

class RiskDetectionTests(TestCase):
    """Test risk detection during test answering."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester8")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester8", password="testpass123")
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        self.test = Test.objects.first()

    def test_normal_answer_no_risk(self):
        from teaching.services import check_keyword_risk
        triggered, _ = check_keyword_risk("使用 TIPP 温度技巧冷静下来")
        self.assertFalse(triggered)

    def test_high_risk_keyword_detected(self):
        from teaching.services import check_keyword_risk
        triggered, keywords = check_keyword_risk("我想自杀")
        self.assertTrue(triggered)
        self.assertIn("自杀", keywords)


# ═══════════════════════════════════════════════════════════════
# Flow Integration Tests
# ═══════════════════════════════════════════════════════════════

class FlowIntegrationTests(TestCase):
    """End-to-end flow: teaching completed → test → retest."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ViewTestMixin.start_service_patches()

    @classmethod
    def tearDownClass(cls):
        ViewTestMixin.stop_service_patches()
        super().tearDownClass()

    def setUp(self):
        self.user = create_student("tester9")
        self.session = create_completed_session(self.user)
        self.client.login(username="tester9", password="testpass123")

    def test_complete_flow_pass(self):
        """Start test → answer all correctly → pass."""
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        test = Test.objects.first()
        questions = list(TestQuestion.objects.filter(test=test).order_by("created_at"))

        # Mock correct answers: B, B, B, C, C
        correct_answers = ["B", "B", "B", "C", "C"]
        for q, ans in zip(questions, correct_answers):
            self.client.post(
                reverse("testing:answer", args=[test.test_id]),
                {"question_id": q.question_id, "answer": ans},
            )

        self.client.post(reverse("testing:finish", args=[test.test_id]))
        test.refresh_from_db()
        self.assertTrue(test.passed)

    def test_complete_flow_fail_then_retest_pass(self):
        """Start test → answer poorly → fail → retest → pass."""
        # First attempt — all wrong
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        test = Test.objects.first()
        questions = list(TestQuestion.objects.filter(test=test).order_by("created_at"))
        for q in questions:
            self.client.post(
                reverse("testing:answer", args=[test.test_id]),
                {"question_id": q.question_id, "answer": "A"},
            )
        self.client.post(reverse("testing:finish", args=[test.test_id]))
        test.refresh_from_db()
        self.assertFalse(test.passed)

        # Retest
        self.client.post(reverse("testing:retest", args=[test.test_id]))
        new_test = Test.objects.order_by("-created_at").first()
        self.assertEqual(new_test.attempt_no, 2)
        self.assertEqual(new_test.status, Test.Status.ONGOING)

        # New questions generated
        self.assertEqual(TestQuestion.objects.filter(test=new_test).count(), 5)

        # Answer all correctly on retest
        new_questions = list(TestQuestion.objects.filter(test=new_test).order_by("created_at"))
        correct_answers = ["B", "B", "B", "C", "C"]
        for q, ans in zip(new_questions, correct_answers):
            self.client.post(
                reverse("testing:answer", args=[new_test.test_id]),
                {"question_id": q.question_id, "answer": ans},
            )
        self.client.post(reverse("testing:finish", args=[new_test.test_id]))
        new_test.refresh_from_db()
        self.assertTrue(new_test.passed)

    def test_teaching_completed_page_has_test_button(self):
        """Verify the teaching completed page shows the start test button."""
        response = self.client.get(
            reverse("teaching:session", args=[self.session.session_id])
        )
        self.assertContains(response, "开始测试")

    def test_multiple_retests_all_persist(self):
        """Three retests all create separate records with unique questions."""
        self.client.post(reverse("testing:start", args=[self.session.session_id]))
        test = Test.objects.first()

        for attempt in range(1, 4):
            questions = list(TestQuestion.objects.filter(test=test).order_by("created_at"))
            for q in questions:
                self.client.post(
                    reverse("testing:answer", args=[test.test_id]),
                    {"question_id": q.question_id, "answer": "A"},
                )
            self.client.post(reverse("testing:finish", args=[test.test_id]))

            if attempt < 3:
                self.client.post(reverse("testing:retest", args=[test.test_id]))
                test = Test.objects.order_by("-created_at").first()
                self.assertEqual(test.attempt_no, attempt + 1)

        self.assertEqual(Test.objects.filter(session=self.session).count(), 3)


class ImageTaskDispatchTests(TestCase):
    """Tests for staggered image generation task dispatch."""

    def test_image_tasks_dispatched_with_staggered_countdown(self):
        """Questions with image_prompt get staggered countdowns (0, 3, 6...)."""
        from .tasks import generate_test_questions_async
        from .models import Test as TestModel, TestQuestion

        user = create_student("imgtester")
        session = create_completed_session(user)
        test = TestModel.objects.create(
            session=session, user=user, status=TestModel.Status.ONGOING,
        )

        # Create 3 questions with image_prompt, 2 without
        for idx, has_prompt in enumerate([True, False, True, True, False]):
            TestQuestion.objects.create(
                test=test,
                question_text=f"Q{idx + 1}",
                options=["A", "B", "C", "D"],
                correct_option="0",
                image_prompt=f"prompt {idx}" if has_prompt else "",
            )

        with patch.object(generate_test_questions_async.app, "send_task") as mock_send:
            from .tasks import generate_test_question_image_async

            # Simulate what happens inside generate_test_questions_async
            # after generate_and_save_questions returns
            saved = list(TestQuestion.objects.filter(test=test).order_by("created_at"))
            for i, q in enumerate(saved):
                if q.image_prompt:
                    generate_test_question_image_async.apply_async(
                        args=[q.question_id],
                        countdown=i * 3,
                    )

            self.assertEqual(mock_send.call_count, 3)
            calls = mock_send.call_args_list

            # Extract countdown values from each call
            countdowns = []
            for call in calls:
                kwargs = call[1]
                countdowns.append(kwargs.get("countdown", -1))

            # Question indices with prompts: 0, 2, 3 → countdowns: 0, 6, 9
            self.assertEqual(countdowns, [0, 6, 9])
