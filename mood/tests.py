"""Tests for mood recording and achievement system (Step 9).

Test classes:
  - MoodRecordingTests: manual mood, post-teaching, post-test mood flows
  - MoodHistoryTests: mood history display, empty state
  - AchievementTests: all 10 achievement unlock conditions
  - AchievementPageTests: achievement grid display with stats
  - StatsAPITests: stats aggregation endpoint
  - MoodUITests: page rendering, navigation links
"""

from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from testing.models import Test, TestQuestion
from teaching.models import ChatMessage, TeachingSession
from mood.models import Achievement, MoodRecord, UserAchievement


def create_student(username="test_student"):
    """Create a student user with profile completed."""
    user = User.objects.create_user(username=username, password="testpass123", role="student")
    user.profile_completed = True
    user.save()
    from questionnaire.forms import ProfileForm
    form = ProfileForm({
        "gender": "male", "age": 15, "grade": "grade_10",
        "hobby_tags": ["阅读", "音乐"], "concern_tags": ["学业压力"],
    })
    if form.is_valid():
        profile = form.save(commit=False)
        profile.user = user
        profile.save()
    return user


def create_session(user, status="completed", skill="观察呼吸", module="正念"):
    """Create a teaching session."""
    session = TeachingSession.objects.create(
        user=user, status=status, phase="teaching",
        selected_skill=skill, selected_module=module,
        teaching_plan={"plan_steps": []},
        completed_at=timezone.now() if status == "completed" else None,
    )
    return session


def create_test_record(user, session, passed=True, correct_count=5, attempt_no=1):
    """Create a test record with questions."""
    test = Test.objects.create(
        user=user, session=session, status="completed",
        passed=passed, correct_count=correct_count,
        total_questions=5, attempt_no=attempt_no,
    )
    for i in range(5):
        TestQuestion.objects.create(
            test=test,
            question_text=f"Question {i+1}",
            options=["A", "B", "C", "D"],
            correct_option=str(i % 4),
            user_answer="B" if passed else "A",
            is_correct=passed or (i == 0),
            explanation="Test explanation",
        )
    return test


def ensure_achievements():
    """Ensure all 10 achievement definitions exist."""
    from mood.services import ACHIEVEMENT_DEFS, ensure_achievements_exist
    ensure_achievements_exist()


# ═══════════════════════════════════════════════════════════════
# Mood recording tests
# ═══════════════════════════════════════════════════════════════

class MoodRecordingTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_achievements()

    def setUp(self):
        self.user = create_student()
        self.client.login(username="test_student", password="testpass123")

    def test_record_manual_mood(self):
        """POST to record_mood_view creates a manual MoodRecord."""
        url = reverse("mood:record")
        resp = self.client.post(url, {"mood_value": "4", "note": "今天心情不错"})
        self.assertRedirects(resp, reverse("mood:home"))

        record = MoodRecord.objects.filter(user=self.user).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.context, "manual")
        self.assertEqual(record.mood_value, 4)
        self.assertEqual(record.emoji, "🙂")
        self.assertEqual(record.note, "今天心情不错")

    def test_record_manual_mood_min_value(self):
        url = reverse("mood:record")
        self.client.post(url, {"mood_value": "1"})
        record = MoodRecord.objects.first()
        self.assertEqual(record.mood_value, 1)
        self.assertEqual(record.emoji, "😫")

    def test_record_manual_mood_max_value(self):
        url = reverse("mood:record")
        self.client.post(url, {"mood_value": "5"})
        record = MoodRecord.objects.first()
        self.assertEqual(record.mood_value, 5)
        self.assertEqual(record.emoji, "😄")

    def test_record_manual_mood_clamps_high(self):
        url = reverse("mood:record")
        self.client.post(url, {"mood_value": "999"})
        record = MoodRecord.objects.first()
        self.assertEqual(record.mood_value, 5)

    def test_record_manual_mood_clamps_low(self):
        url = reverse("mood:record")
        self.client.post(url, {"mood_value": "-1"})
        record = MoodRecord.objects.first()
        self.assertEqual(record.mood_value, 1)

    def test_record_manual_mood_default_value(self):
        url = reverse("mood:record")
        self.client.post(url, {})
        record = MoodRecord.objects.first()
        self.assertEqual(record.mood_value, 3)

    def test_record_post_teaching_mood(self):
        """Post-teaching mood links to the session."""
        session = create_session(self.user)
        url = reverse("mood:post_teaching", kwargs={"session_id": session.session_id})
        resp = self.client.post(url, {"mood_value": "2", "note": "有点累"})
        self.assertRedirects(resp, reverse("teaching:session",
                                           kwargs={"session_id": session.session_id}))

        session.refresh_from_db()
        self.assertTrue(session.post_mood_id)
        record = MoodRecord.objects.get(mood_id=session.post_mood_id)
        self.assertEqual(record.context, "after_teaching")
        self.assertEqual(record.mood_value, 2)
        self.assertEqual(record.emoji, "😟")

    def test_post_teaching_mood_already_recorded(self):
        """Cannot record post-teaching mood twice."""
        session = create_session(self.user)

        # First recording
        url = reverse("mood:post_teaching", kwargs={"session_id": session.session_id})
        self.client.post(url, {"mood_value": "3"})

        # Second recording should be rejected
        resp = self.client.post(url, {"mood_value": "4"}, follow=True)
        self.assertContains(resp, "已记录过教学后心情")

    def test_post_teaching_mood_requires_completed_session(self):
        """Only completed sessions can record post-teaching mood."""
        session = create_session(self.user, status="ongoing")
        url = reverse("mood:post_teaching", kwargs={"session_id": session.session_id})
        resp = self.client.post(url, {"mood_value": "3"}, follow=True)
        self.assertContains(resp, "教学未完成")

    def test_post_test_mood(self):
        """Post-test mood recording."""
        session = create_session(self.user)
        test = create_test_record(self.user, session)
        url = reverse("mood:post_test", kwargs={"test_id": test.test_id})
        resp = self.client.post(url, {"mood_value": "5", "note": "全部答对了！"})
        self.assertRedirects(resp, reverse("testing:test", kwargs={"test_id": test.test_id}))

        record = MoodRecord.objects.filter(context="after_testing").first()
        self.assertIsNotNone(record)
        self.assertEqual(record.mood_value, 5)
        self.assertEqual(record.note, "全部答对了！")

    def test_mood_home_page_requires_profile(self):
        """Mood home requires profile."""
        user_no_profile = User.objects.create_user(
            username="noprofile", password="testpass123", role="student"
        )
        self.client.login(username="noprofile", password="testpass123")
        url = reverse("mood:home")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)  # redirect to questionnaire

    def test_unauthenticated_redirect(self):
        self.client.logout()
        urls = [
            reverse("mood:home"),
            reverse("mood:record"),
            reverse("mood:achievements"),
        ]
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)


# ═══════════════════════════════════════════════════════════════
# Mood history tests
# ═══════════════════════════════════════════════════════════════

class MoodHistoryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_achievements()

    def setUp(self):
        self.user = create_student()
        self.client.login(username="test_student", password="testpass123")
        self.session = create_session(self.user)

    def test_mood_home_shows_records(self):
        """Mood home displays recorded moods."""
        MoodRecord.objects.create(
            user=self.user, mood_value=4, emoji="🙂", context="manual"
        )
        MoodRecord.objects.create(
            user=self.user, mood_value=2, emoji="😟", context="before_teaching",
            session=self.session
        )

        url = reverse("mood:home")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "🙂")
        self.assertContains(resp, "😟")
        self.assertContains(resp, "手动记录")
        self.assertContains(resp, "教学前")

    def test_mood_home_empty_state(self):
        """Mood home shows empty state when no records."""
        url = reverse("mood:home")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "还没有心情记录")

    def test_mood_history_ordering(self):
        """Records are ordered newest first."""
        from datetime import timedelta
        r1 = MoodRecord.objects.create(
            user=self.user, mood_value=3, emoji="😐", context="manual",
        )
        r2 = MoodRecord.objects.create(
            user=self.user, mood_value=5, emoji="😄", context="after_teaching",
            session=self.session,
        )

        from mood.services import get_mood_history
        history = get_mood_history(self.user)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["mood_id"], r2.mood_id)

    def test_record_page_loads(self):
        url = reverse("mood:record")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "记录现在的心情")


# ═══════════════════════════════════════════════════════════════
# Achievement tests
# ═══════════════════════════════════════════════════════════════

class AchievementTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_achievements()

    def setUp(self):
        self.user = create_student()
        self.session = create_session(self.user)

    # ── 第一步: first successful training ──

    def test_first_step_no_tests(self):
        """No achievement before any tests."""
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertNotIn("第一步", result["newly_unlocked"])

    def test_first_step_after_successful_training(self):
        """第一步 unlocks after first teaching completed + test passed."""
        create_test_record(self.user, self.session, passed=True, correct_count=4)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("第一步", result["newly_unlocked"])

    def test_first_step_not_awarded_twice(self):
        """Achievement is only awarded once."""
        create_test_record(self.user, self.session, passed=True, correct_count=4)
        from mood.services import check_and_award_achievements
        check_and_award_achievements(self.user)
        result = check_and_award_achievements(self.user)
        self.assertNotIn("第一步", result["newly_unlocked"])

    # ── 正念入门: first mindfulness training ──

    def test_mindfulness_start_no_session(self):
        """No 正念入门 achievement for user with no completed teaching sessions."""
        fresh_user = User.objects.create_user(
            username="fresh_no_teach", password="testpass123", role="student"
        )
        fresh_user.profile_completed = True
        fresh_user.save()
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(fresh_user)
        self.assertNotIn("正念入门", result["newly_unlocked"])

    def test_mindfulness_start_after_first(self):
        create_session(self.user, skill="正念呼吸", module="正念")
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("正念入门", result["newly_unlocked"])

    # ── 满分通过: perfect test (5/5) ──

    def test_perfect_score(self):
        create_test_record(self.user, self.session, passed=True, correct_count=5)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("满分通过", result["newly_unlocked"])

    def test_perfect_score_not_4_out_of_5(self):
        create_test_record(self.user, self.session, passed=True, correct_count=4)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertNotIn("满分通过", result["newly_unlocked"])

    # ── 重新出发: retest pass after fail ──

    def test_comeback(self):
        # First test fails
        create_test_record(self.user, self.session, passed=False, correct_count=2, attempt_no=1)
        # Retest passes
        create_test_record(self.user, self.session, passed=True, correct_count=4, attempt_no=2)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("第一步", result["newly_unlocked"])
        self.assertIn("重新出发", result["newly_unlocked"])

    def test_comeback_not_without_fail_first(self):
        # Only passing tests
        create_test_record(self.user, self.session, passed=True, correct_count=4, attempt_no=1)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertNotIn("重新出发", result["newly_unlocked"])

    # ── 第一次测试未通过 ──

    def test_first_test_failed(self):
        create_test_record(self.user, self.session, passed=False, correct_count=1)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("第一次测试未通过", result["newly_unlocked"])

    # ── 情绪记录开始 ──

    def test_mood_start(self):
        MoodRecord.objects.create(
            user=self.user, mood_value=3, emoji="😐", context="manual"
        )
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("情绪记录开始", result["newly_unlocked"])

    # ── Achievements page ──

    def test_achievements_page_loads(self):
        self.client.login(username="test_student", password="testpass123")
        url = reverse("mood:achievements")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "第一步")
        self.assertContains(resp, "满分通过")
        self.assertContains(resp, "情绪记录开始")
        self.assertContains(resp, "正念入门")
        self.assertContains(resp, "重新出发")

    def test_achievements_page_shows_unlocked(self):
        create_test_record(self.user, self.session, passed=True, correct_count=5)
        from mood.services import check_and_award_achievements
        check_and_award_achievements(self.user)

        self.client.login(username="test_student", password="testpass123")
        url = reverse("mood:achievements")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "✓")

    def test_achievements_page_shows_stats(self):
        create_test_record(self.user, self.session, passed=True, correct_count=4)
        from mood.services import check_and_award_achievements
        check_and_award_achievements(self.user)

        self.client.login(username="test_student", password="testpass123")
        url = reverse("mood:achievements")
        resp = self.client.get(url)
        self.assertContains(resp, "总训练次数")
        self.assertContains(resp, "成功训练")
        self.assertContains(resp, "成就解锁")

    # ── Achievement idempotency ──

    def test_achievement_not_duplicated(self):
        create_test_record(self.user, self.session, passed=True, correct_count=4)
        from mood.services import check_and_award_achievements
        check_and_award_achievements(self.user)
        check_and_award_achievements(self.user)
        count = UserAchievement.objects.filter(user=self.user).count()
        expected = UserAchievement.objects.filter(user=self.user).values("achievement").distinct().count()
        self.assertEqual(count, expected)

    # ── Service: record_mood triggers achievement ──

    def test_record_mood_triggers_achievement(self):
        from mood.services import record_mood
        record_mood(self.user, 4, "manual")
        self.assertTrue(
            UserAchievement.objects.filter(
                user=self.user, achievement__name_cn="情绪记录开始"
            ).exists()
        )

    # ── Stats for reports ──

    def test_stats_for_reports(self):
        create_test_record(self.user, self.session, passed=True, correct_count=4)
        MoodRecord.objects.create(user=self.user, mood_value=3, emoji="😐", context="manual")

        from mood.services import get_stats_for_reports
        stats = get_stats_for_reports(self.user)
        self.assertIn("mood_trend", stats)
        self.assertIn("skill_frequency", stats)
        self.assertIn("test_performance", stats)
        self.assertIn("stats_summary", stats)
        self.assertEqual(stats["stats_summary"]["total_mood_records"], 1)
        self.assertEqual(stats["stats_summary"]["successful_trainings"], 1)


# ═══════════════════════════════════════════════════════════════
# Achievement trigger condition edge cases
# ═══════════════════════════════════════════════════════════════

class AchievementEdgeCaseTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_achievements()

    def setUp(self):
        self.user = create_student()

    def test_ten_trainings_not_at_five(self):
        """十次训练 requires 10, not 5."""
        for i in range(5):
            sess = create_session(self.user, skill=f"skill_{i}")
            create_test_record(self.user, sess, passed=True)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertNotIn("十次训练", result["newly_unlocked"])

    def test_ten_trainings_at_ten(self):
        """十次训练 unlocks at 10 successful trainings."""
        for i in range(10):
            sess = create_session(self.user, skill=f"skill_{i}")
            create_test_record(self.user, sess, passed=True)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("十次训练", result["newly_unlocked"])

    def test_five_test_fails_not_at_two(self):
        """五次测试未通过 requires 5 cumulative fails."""
        for i in range(2):
            sess = create_session(self.user)
            create_test_record(self.user, sess, passed=False, correct_count=2)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertNotIn("五次测试未通过", result["newly_unlocked"])

    def test_five_test_fails_at_five(self):
        """五次测试未通过 unlocks at 5 fails."""
        for i in range(5):
            sess = create_session(self.user)
            create_test_record(self.user, sess, passed=False, correct_count=2)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertIn("五次测试未通过", result["newly_unlocked"])

    def test_consecutive_days_zero_with_no_sessions(self):
        from mood.services import _gather_user_stats
        stats = _gather_user_stats(self.user)
        self.assertEqual(stats["consecutive_days"], 0)

    def test_achievement_not_awarded_when_inactive(self):
        """Inactive achievements should not be awarded."""
        ach = Achievement.objects.filter(name_cn="第一步").first()
        if ach:
            ach.is_active = False
            ach.save()

        sess = create_session(self.user)
        create_test_record(self.user, sess, passed=True, correct_count=4)
        from mood.services import check_and_award_achievements
        result = check_and_award_achievements(self.user)
        self.assertNotIn("第一步", result["newly_unlocked"])

        # Restore
        if ach:
            ach.is_active = True
            ach.save()


# ═══════════════════════════════════════════════════════════════
# Post-mood integration tests (from teaching/testing flow)
# ═══════════════════════════════════════════════════════════════

class PostMoodIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_achievements()

    def setUp(self):
        self.user = create_student()
        self.client.login(username="test_student", password="testpass123")
        self.session = create_session(self.user)

    def test_session_completed_shows_post_mood_link(self):
        """Session completed state includes post-mood recording link."""
        url = reverse("teaching:session", kwargs={"session_id": self.session.session_id})
        resp = self.client.get(url)
        self.assertContains(resp, "记录教学后心情")

    def test_session_with_post_mood_shows_recorded(self):
        """When post_mood already recorded, shows recorded indicator."""
        MoodRecord.objects.create(
            user=self.user, session=self.session,
            mood_value=3, emoji="😐", context="after_teaching"
        )
        self.session.post_mood_id = "some-mood-id"
        self.session.save()

        url = reverse("teaching:session", kwargs={"session_id": self.session.session_id})
        resp = self.client.get(url)
        self.assertContains(resp, "已记录教学后心情")

    def test_post_mood_page_loads(self):
        """GET to post_teaching mood page renders form."""
        url = reverse("mood:post_teaching", kwargs={"session_id": self.session.session_id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "教学后心情记录")

    def test_achievement_checked_on_session_completion(self):
        """Achievements are checked when end_session is called via view."""
        # Simulate teaching flow: teaching phase with messages
        self.session.phase = "teaching"
        self.session.status = "ongoing"
        self.session.save()
        ChatMessage.objects.create(
            session=self.session, user=self.user, role="user", content="什么是正念？"
        )

        # Patch RAG chains to avoid real API calls
        from unittest.mock import patch
        from knowledge_base.rag.schemas import TeachingSummary

        mock_summary = TeachingSummary(
            skill_covered="正念呼吸",
            summary_text="学生学习了正念呼吸技能。",
            key_points=["正念呼吸是核心技能", "观察呼吸是基础"],
            student_understanding="良好",
            recommendations=["继续练习"],
        )

        with patch("knowledge_base.rag.chains.generate_teaching_summary",
                   return_value=mock_summary):
            with patch("knowledge_base.rag.retriever.get_retriever"):
                url = reverse("teaching:end_session", kwargs={"session_id": self.session.session_id})
                resp = self.client.post(url, follow=True)

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "completed")


# ═══════════════════════════════════════════════════════════════
# Stats API tests
# ═══════════════════════════════════════════════════════════════

class StatsAPITests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_achievements()

    def setUp(self):
        self.user = create_student()
        self.client.login(username="test_student", password="testpass123")

    def _make_staff(self):
        """Make user staff without the save() override clearing it."""
        User.objects.filter(pk=self.user.pk).update(is_staff=True, is_superuser=True)
        self.user.refresh_from_db()

    def test_stats_api_requires_staff(self):
        """Stats API requires staff access."""
        url = reverse("mood:stats")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_stats_api_staff_access(self):
        """Staff user can access stats API."""
        self._make_staff()

        session = create_session(self.user)
        create_test_record(self.user, session, passed=True)
        MoodRecord.objects.create(user=self.user, mood_value=4, emoji="🙂", context="manual")

        url = reverse("mood:stats")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("mood_trend", data)
        self.assertIn("stats_summary", data)
        self.assertEqual(len(data["mood_trend"]), 1)
        self.assertEqual(data["stats_summary"]["total_mood_records"], 1)

    def test_stats_api_for_specific_user(self):
        """Staff can query stats for a specific user by ID."""
        self._make_staff()

        url = f"{reverse('mood:stats')}?user_id={self.user.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_stats_api_invalid_user(self):
        """Stats API returns 404 for non-existent user."""
        self._make_staff()

        url = f"{reverse('mood:stats')}?user_id=nonexistent"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


# ═══════════════════════════════════════════════════════════════
# Achievement definition tests
# ═══════════════════════════════════════════════════════════════

class AchievementDefinitionTests(TestCase):
    def test_all_ten_achievements_exist(self):
        """All 10 PRD achievements are created."""
        from mood.services import ACHIEVEMENT_DEFS, ensure_achievements_exist
        ensure_achievements_exist()

        names = set(Achievement.objects.filter(is_active=True).values_list("name_cn", flat=True))
        expected = {a["name_cn"] for a in ACHIEVEMENT_DEFS}
        missing = expected - names
        self.assertFalse(missing, f"Missing achievements: {missing}")

    def test_ensure_achievements_idempotent(self):
        """Calling ensure_achievements_exist multiple times doesn't create duplicates."""
        from mood.services import ensure_achievements_exist

        ensure_achievements_exist()  # first call
        count_before = Achievement.objects.count()
        ensure_achievements_exist()  # second call should not create more
        count_after = Achievement.objects.count()
        self.assertEqual(count_before, count_after)
