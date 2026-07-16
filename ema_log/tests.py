import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import EMASubmission

User = get_user_model()

VALID_EMA_DATA = {
    "sad_score": 3,
    "anxious_score": 5,
    "angry_score": 2,
    "calm_score": 6,
    "hopeful_score": 7,
    "distress_score": 4,
    "nssi_urge_score": 1,
    "suicide_urge_score": 1,
    "used_dbt_skill": False,
    "dbt_skills_used": [],
    "skill_effectiveness_score": "",
    "medical_doctor_visit": False,
    "medical_group_therapy": False,
    "medical_medication_change": False,
}


def create_user(username, password="testpass123", role="student"):
    user = User.objects.create_user(username=username, password=password, role=role)
    user.profile_completed = True
    user.save()
    from questionnaire.models import UserProfile
    UserProfile.objects.create(
        user=user, gender="male", age=15, grade="grade_9"
    )
    return user


def create_admin(username="ema_admin"):
    return User.objects.create_user(
        username=username, password="testpass123", role="admin"
    )


# ── Page Display Tests ──


class EMALogPageTests(TestCase):
    def setUp(self):
        self.user = create_user("emastudent")
        self.url = reverse("ema_log:log")

    def test_page_loads_for_authenticated_user(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "DBT日志")

    def test_page_redirects_if_unauthenticated(self):
        resp = self.client.get(self.url)
        self.assertRedirects(resp, f"/accounts/login/?next={self.url}")

    def test_page_redirects_without_profile(self):
        user = User.objects.create_user(username="noprofile", password="testpass123", role="student")
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertRedirects(resp, reverse("questionnaire:profile"))

    def test_page_shows_daily_counter(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, "今日第")
        self.assertContains(resp, "1")  # first of the day
        self.assertContains(resp, "次填写")

    def test_daily_counter_reflects_existing_submissions(self):
        EMASubmission.objects.create(
            user=self.user, sad_score=3, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=False,
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, "今日第")
        self.assertContains(resp, "2")  # display_count = today_count + 1

    def test_daily_counter_only_counts_today(self):
        old = EMASubmission.objects.create(
            user=self.user, sad_score=3, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=False,
        )
        # Manually backdate the old submission
        EMASubmission.objects.filter(submission_id=old.submission_id).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, "1")  # display_count = 0 + 1

    def test_page_shows_all_form_sections(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, "实时情绪状态")
        self.assertContains(resp, "主观痛苦水平")
        self.assertContains(resp, "自伤冲动")
        self.assertContains(resp, "自杀冲动")
        self.assertContains(resp, "DBT技能使用")
        self.assertContains(resp, "医疗接触记录")


# ── Submission Tests ──


class EMASubmissionTests(TestCase):
    def setUp(self):
        self.user = create_user("emastudent")
        self.url = reverse("ema_log:log")
        self.client.force_login(self.user)

    def test_valid_submission_saves_all_fields(self):
        data = {
            **VALID_EMA_DATA,
            "used_dbt_skill": True,
            "dbt_skills_used": ["正念", "情绪调节"],
            "skill_effectiveness_score": 8,
        }
        resp = self.client.post(self.url, data)
        self.assertRedirects(resp, self.url)

        submission = EMASubmission.objects.get(user=self.user)
        self.assertEqual(submission.sad_score, 3)
        self.assertEqual(submission.anxious_score, 5)
        self.assertEqual(submission.angry_score, 2)
        self.assertEqual(submission.calm_score, 6)
        self.assertEqual(submission.hopeful_score, 7)
        self.assertEqual(submission.distress_score, 4)
        self.assertEqual(submission.nssi_urge_score, 1)
        self.assertEqual(submission.suicide_urge_score, 1)
        self.assertTrue(submission.used_dbt_skill)
        self.assertEqual(submission.dbt_skills_used, ["正念", "情绪调节"])
        self.assertEqual(submission.skill_effectiveness_score, 8)
        self.assertFalse(submission.medical_doctor_visit)
        self.assertFalse(submission.medical_group_therapy)
        self.assertFalse(submission.medical_medication_change)

    def test_valid_submission_without_dbt_skill(self):
        resp = self.client.post(self.url, VALID_EMA_DATA)
        self.assertRedirects(resp, self.url)

        submission = EMASubmission.objects.get(user=self.user)
        self.assertFalse(submission.used_dbt_skill)
        self.assertEqual(submission.dbt_skills_used, [])
        self.assertIsNone(submission.skill_effectiveness_score)

    def test_valid_submission_with_medical_contacts(self):
        data = {
            **VALID_EMA_DATA,
            "medical_doctor_visit": True,
            "medical_group_therapy": True,
            "medical_medication_change": True,
        }
        resp = self.client.post(self.url, data)
        self.assertRedirects(resp, self.url)

        submission = EMASubmission.objects.get(user=self.user)
        self.assertTrue(submission.medical_doctor_visit)
        self.assertTrue(submission.medical_group_therapy)
        self.assertTrue(submission.medical_medication_change)

    def test_submission_creates_correct_timestamp(self):
        resp = self.client.post(self.url, VALID_EMA_DATA)
        submission = EMASubmission.objects.get(user=self.user)
        self.assertIsNotNone(submission.created_at)
        self.assertEqual(submission.created_at.date(), timezone.now().date())

    def test_submission_records_user(self):
        resp = self.client.post(self.url, VALID_EMA_DATA)
        submission = EMASubmission.objects.get(user=self.user)
        self.assertEqual(submission.user, self.user)

    def test_multiple_submissions_all_saved(self):
        for i in range(4):
            data = {**VALID_EMA_DATA, "sad_score": i + 1}
            self.client.post(self.url, data)

        submissions = EMASubmission.objects.filter(user=self.user).order_by("sad_score")
        self.assertEqual(submissions.count(), 4)
        self.assertEqual(submissions[0].sad_score, 1)
        self.assertEqual(submissions[3].sad_score, 4)

    def test_submission_shows_success_message(self):
        resp = self.client.post(self.url, VALID_EMA_DATA, follow=True)
        self.assertContains(resp, "EMA日志已成功提交")

    def test_post_redirects_to_same_page(self):
        resp = self.client.post(self.url, VALID_EMA_DATA)
        self.assertRedirects(resp, self.url)


# ── Validation Tests ──


class EMAValidationTests(TestCase):
    def setUp(self):
        self.user = create_user("emastudent")
        self.url = reverse("ema_log:log")
        self.client.force_login(self.user)

    def test_sad_score_below_1_rejected(self):
        data = {**VALID_EMA_DATA, "sad_score": 0}
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1-10")

    def test_sad_score_above_10_rejected(self):
        data = {**VALID_EMA_DATA, "sad_score": 11}
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1-10")

    def test_distress_score_above_10_rejected(self):
        data = {**VALID_EMA_DATA, "distress_score": 15}
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1-10")

    def test_nssi_urge_score_above_10_rejected(self):
        data = {**VALID_EMA_DATA, "nssi_urge_score": 99}
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1-10")

    def test_suicide_urge_score_above_10_rejected(self):
        data = {**VALID_EMA_DATA, "suicide_urge_score": 99}
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1-10")

    def test_missing_suicide_urge_score_rejected(self):
        data = {**VALID_EMA_DATA}
        del data["suicide_urge_score"]
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "这个字段是必填项")

    def test_missing_vas_score_rejected(self):
        data = {**VALID_EMA_DATA}
        del data["sad_score"]
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)

    def test_dbt_skill_yes_requires_skills_selected(self):
        data = {
            **VALID_EMA_DATA,
            "used_dbt_skill": True,
            "dbt_skills_used": [],
            "skill_effectiveness_score": "",
        }
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "至少一项")

    def test_dbt_skill_yes_requires_effectiveness(self):
        data = {
            **VALID_EMA_DATA,
            "used_dbt_skill": True,
            "dbt_skills_used": ["正念"],
            "skill_effectiveness_score": "",
        }
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "帮助程度")

    def test_dbt_skill_no_does_not_require_skills_or_effectiveness(self):
        data = {
            **VALID_EMA_DATA,
            "used_dbt_skill": False,
            "dbt_skills_used": [],
            "skill_effectiveness_score": "",
        }
        resp = self.client.post(self.url, data)
        self.assertRedirects(resp, self.url)
        submission = EMASubmission.objects.get(user=self.user)
        self.assertFalse(submission.used_dbt_skill)

    def test_boundary_scores_accepted(self):
        for score in [1, 5, 10]:
            data = {**VALID_EMA_DATA, "sad_score": score}
            EMASubmission.objects.filter(user=self.user).delete()
            resp = self.client.post(self.url, data)
            self.assertRedirects(
                resp, self.url, msg_prefix=f"Score {score} should be accepted"
            )

    def test_effectiveness_score_out_of_range_rejected(self):
        data = {
            **VALID_EMA_DATA,
            "used_dbt_skill": True,
            "dbt_skills_used": ["正念"],
            "skill_effectiveness_score": 15,
        }
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1-10")


# ── Export Integration Tests ──


class EMAExportTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.student = create_user("emaexport")

    def test_json_export_includes_ema_submissions(self):
        EMASubmission.objects.create(
            user=self.student, sad_score=3, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=True, dbt_skills_used=["正念"],
            skill_effectiveness_score=8,
        )
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertIn("ema_submissions", data)
        self.assertEqual(len(data["ema_submissions"]), 1)
        ema = data["ema_submissions"][0]
        self.assertEqual(ema["sad_score"], 3)
        self.assertEqual(ema["distress_score"], 4)
        self.assertEqual(ema["suicide_urge_score"], 1)
        self.assertTrue(ema["used_dbt_skill"])
        self.assertEqual(ema["dbt_skills_used"], ["正念"])
        self.assertEqual(ema["skill_effectiveness_score"], 8)

    def test_json_export_empty_ema_submissions(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertIn("ema_submissions", data)
        self.assertEqual(data["ema_submissions"], [])

    def test_json_export_multiple_ema_submissions(self):
        EMASubmission.objects.create(
            user=self.student, sad_score=1, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=False,
        )
        EMASubmission.objects.create(
            user=self.student, sad_score=9, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=8, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=False,
        )
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertEqual(len(data["ema_submissions"]), 2)
        scores = {s["sad_score"] for s in data["ema_submissions"]}
        self.assertEqual(scores, {1, 9})

    def test_csv_export_includes_ema_section(self):
        EMASubmission.objects.create(
            user=self.student, sad_score=3, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=False,
        )
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_csv", args=[self.student.id])
        resp = self.client.get(url)
        content = resp.content.decode("utf-8-sig")
        self.assertIn("=== 用户信息 ===", content)
        self.assertIn("=== 教学会话 ===", content)

    def test_bulk_json_includes_ema_submissions(self):
        EMASubmission.objects.create(
            user=self.student, sad_score=3, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=False,
        )
        self.client.force_login(self.admin)
        url = reverse("export_app:export_users_json")
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertIn(self.student.id, data)
        self.assertIn("ema_submissions", data[self.student.id])


# ── Permission Tests ──


class EMAPermissionTests(TestCase):
    def setUp(self):
        self.student = create_user("ema_perm_student")
        self.url = reverse("ema_log:log")

    def test_student_can_access_ema_log(self):
        self.client.force_login(self.student)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_redirected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


# ── Service Tests ──


class EMAExportServiceTests(TestCase):
    def test_aggregate_includes_ema_submissions(self):
        student = create_user("svc_ema")
        EMASubmission.objects.create(
            user=student, sad_score=3, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=True, dbt_skills_used=["痛苦耐受"],
            skill_effectiveness_score=7,
        )
        from export_app.services import aggregate_user_data

        data = aggregate_user_data(student)
        self.assertIn("ema_submissions", data)
        self.assertEqual(len(data["ema_submissions"]), 1)
        ema = data["ema_submissions"][0]
        self.assertEqual(ema["sad_score"], 3)
        self.assertEqual(ema["suicide_urge_score"], 1)
        self.assertEqual(ema["dbt_skills_used"], ["痛苦耐受"])
        self.assertEqual(ema["skill_effectiveness_score"], 7)
        self.assertTrue(ema["used_dbt_skill"])

    def test_aggregate_empty_ema_submissions(self):
        student = create_user("svc_ema_empty")
        from export_app.services import aggregate_user_data

        data = aggregate_user_data(student)
        self.assertIn("ema_submissions", data)
        self.assertEqual(data["ema_submissions"], [])

    def test_aggregate_ema_created_at_is_isoformat(self):
        student = create_user("svc_ema_time")
        EMASubmission.objects.create(
            user=student, sad_score=3, anxious_score=5, angry_score=2,
            calm_score=6, hopeful_score=7, distress_score=4, nssi_urge_score=1,
            suicide_urge_score=1,
            used_dbt_skill=False,
        )
        from export_app.services import aggregate_user_data

        data = aggregate_user_data(student)
        ema = data["ema_submissions"][0]
        self.assertIsNotNone(ema["created_at"])
        self.assertIsInstance(ema["created_at"], str)
        self.assertIn("T", ema["created_at"])
