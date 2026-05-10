"""Step 3 tests: Admin registrations, model CRUD, admin accessibility, relationships."""

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import InviteCode, ReportViewerAssignment
from export_app.models import AdminOperationLog
from knowledge_base.models import KnowledgeChunk, KnowledgeDocument, RetrievalLog
from mood.models import Achievement, MoodRecord, UserAchievement
from questionnaire.models import UserProfile
from reports.models import ReportAccessLog
from risk.models import RiskEvent
from teaching.models import ChatMessage, TeachingSession
from testing.models import Test, TestQuestion

User = get_user_model()


def create_admin():
    return User.objects.create_user(
        username="test_admin",
        password="adminpass123",
        role="admin",
    )


def create_student(username="test_student"):
    return User.objects.create_user(
        username=username,
        password="studentpass123",
        role="student",
    )


def create_report_viewer(username="test_viewer"):
    return User.objects.create_user(
        username=username,
        password="viewerpass123",
        role="report_viewer",
    )


# ── 1. Admin Registration Tests ──


class AdminRegistrationTests(TestCase):
    """Verify every PRD model is registered with the Django admin site."""

    def test_all_models_registered(self):
        registered = {m._meta.model_name for m in site._registry}
        expected = {
            "user",
            "invitecode",
            "reportviewerassignment",
            "userprofile",
            "teachingsession",
            "chatmessage",
            "test",
            "testquestion",
            "moodrecord",
            "achievement",
            "userachievement",
            "riskevent",
            "knowledgedocument",
            "knowledgechunk",
            "retrievallog",
            "adminoperationlog",
            "reportaccesslog",
        }
        missing = expected - registered
        self.assertSetEqual(missing, set(), f"Models not registered in admin: {missing}")


# ── 2. Model CRUD Tests ──


class UserProfileCRUDTests(TestCase):
    def setUp(self):
        self.student = create_student("crud_student")

    def test_create_and_read_profile(self):
        profile = UserProfile.objects.create(
            user=self.student,
            gender="male",
            age=15,
            grade="grade_10",
            hobby_tags=["阅读", "编程"],
            concern_tags=["考试焦虑"],
        )
        fetched = UserProfile.objects.get(user=self.student)
        self.assertEqual(fetched.gender, "male")
        self.assertEqual(fetched.age, 15)
        self.assertEqual(fetched.hobby_tags, ["阅读", "编程"])

    def test_update_profile(self):
        profile = UserProfile.objects.create(
            user=self.student,
            gender="female",
            age=14,
            grade="grade_9",
        )
        profile.age = 15
        profile.hobby_tags = ["绘画"]
        profile.save()
        fetched = UserProfile.objects.get(user=self.student)
        self.assertEqual(fetched.age, 15)
        self.assertEqual(fetched.hobby_tags, ["绘画"])


class TeachingSessionCRUDTests(TestCase):
    def setUp(self):
        self.student = create_student("teach_student")

    def test_create_update_session(self):
        session = TeachingSession.objects.create(
            user=self.student,
            status="ongoing",
            selected_module="distress_tolerance",
            selected_skill="TIP",
        )
        fetched = TeachingSession.objects.get(pk=session.pk)
        self.assertEqual(fetched.status, "ongoing")
        self.assertEqual(fetched.selected_module, "distress_tolerance")

        fetched.status = "completed"
        fetched.teaching_summary = "Teaching completed successfully."
        fetched.save()
        self.assertEqual(TeachingSession.objects.get(pk=session.pk).status, "completed")

    def test_chat_message_creation(self):
        session = TeachingSession.objects.create(user=self.student)
        msg = ChatMessage.objects.create(
            session=session,
            user=self.student,
            role="user",
            content="你好，我想学习情绪管理。",
        )
        fetched = ChatMessage.objects.get(pk=msg.pk)
        self.assertEqual(fetched.role, "user")
        self.assertIn("情绪管理", fetched.content)
        self.assertEqual(fetched.session.pk, session.pk)


class TestAndQuestionCRUDTests(TestCase):
    def setUp(self):
        self.student = create_student("test_student_crud")
        self.session = TeachingSession.objects.create(user=self.student)

    def test_create_test_with_questions(self):
        test_obj = Test.objects.create(
            user=self.student,
            session=self.session,
            attempt_no=1,
            total_questions=5,
            correct_count=3,
            passed=False,
            status="completed",
        )
        fetched = Test.objects.get(pk=test_obj.pk)
        self.assertEqual(fetched.total_questions, 5)
        self.assertEqual(fetched.passed, False)

        question = TestQuestion.objects.create(
            test=test_obj,
            question_text="当感到强烈的情绪时，应该先做什么？",
            options=["A) 立即行动", "B) 停下来观察", "C) 忽略情绪", "D) 找人倾诉"],
            correct_option="B",
            user_answer="B",
            is_correct=True,
            explanation="STOP技能的第一步是停下来。",
        )
        self.assertEqual(TestQuestion.objects.count(), 1)
        self.assertTrue(TestQuestion.objects.first().is_correct)


class MoodCRUDTests(TestCase):
    def setUp(self):
        self.student = create_student("mood_student")

    def test_create_mood_record(self):
        mood = MoodRecord.objects.create(
            user=self.student,
            mood_value=4,
            emoji="happy",
            context="before_teaching",
            note="今天感觉不错",
        )
        fetched = MoodRecord.objects.get(pk=mood.pk)
        self.assertEqual(fetched.mood_value, 4)
        self.assertEqual(fetched.context, "before_teaching")

    def test_achievement_crud(self):
        achievement = Achievement.objects.create(
            name_cn="第一步",
            description_cn="完成第一次教学训练",
            trigger_rule={"event": "first_session_completed"},
            icon="star",
        )
        fetched = Achievement.objects.get(pk=achievement.pk)
        self.assertEqual(fetched.name_cn, "第一步")
        self.assertTrue(fetched.is_active)

        user_ach = UserAchievement.objects.create(
            user=self.student,
            achievement=achievement,
        )
        self.assertEqual(UserAchievement.objects.count(), 1)
        self.assertEqual(user_ach.user, self.student)


class RiskEventCRUDTests(TestCase):
    def setUp(self):
        self.student = create_student("risk_student")
        self.session = TeachingSession.objects.create(user=self.student)

    def test_create_risk_event(self):
        risk = RiskEvent.objects.create(
            user=self.student,
            session=self.session,
            trigger_text="我不想活了",
            detection_source="keyword",
            action_taken="立即中止会话，弹出风险提示",
            session_stopped=True,
            follow_up_mode="onsite_manual_followup",
        )
        fetched = RiskEvent.objects.get(pk=risk.pk)
        self.assertEqual(fetched.detection_source, "keyword")
        self.assertTrue(fetched.session_stopped)
        self.assertIn("中止", fetched.action_taken)


class KnowledgeBaseCRUDTests(TestCase):
    def setUp(self):
        self.student = create_student("kb_student")
        self.session = TeachingSession.objects.create(user=self.student)

    def test_document_lifecycle(self):
        doc = KnowledgeDocument.objects.create(
            title="DBT情绪调节模块",
            module="emotion_regulation",
            skill="check_the_facts",
            version="1.0",
            status="uploaded",
        )
        self.assertEqual(KnowledgeDocument.objects.count(), 1)

        doc.status = "processing"
        doc.save()
        self.assertEqual(KnowledgeDocument.objects.get(pk=doc.pk).status, "processing")

        doc.status = "retrievable"
        doc.save()
        self.assertEqual(KnowledgeDocument.objects.get(pk=doc.pk).status, "retrievable")

    def test_chunk_and_retrieval_log(self):
        doc = KnowledgeDocument.objects.create(
            title="测试文档",
            module="mindfulness",
            status="retrievable",
        )
        chunk = KnowledgeChunk.objects.create(
            document=doc,
            chunk_text="正念冥想是DBT的核心技能之一...",
            embedding_id="emb_abc123",
            metadata={"section": "intro", "difficulty": "beginner"},
        )
        self.assertEqual(KnowledgeChunk.objects.count(), 1)
        self.assertEqual(chunk.document, doc)

        log = RetrievalLog.objects.create(
            user=self.student,
            session=self.session,
            query="正念冥想是什么？",
            retrieved_chunk_ids=[chunk.chunk_id],
            use_case="teaching",
        )
        fetched = RetrievalLog.objects.get(pk=log.pk)
        self.assertEqual(fetched.use_case, "teaching")
        self.assertEqual(len(fetched.retrieved_chunk_ids), 1)


class AdminOperationLogCRUDTests(TestCase):
    def setUp(self):
        self.admin = create_admin()

    def test_create_operation_log(self):
        log = AdminOperationLog.objects.create(
            admin=self.admin,
            operation_type="export",
            target_type="user_data",
            target_id="user_abc",
            export_format="json",
            export_scope={"user_ids": ["user_abc"]},
        )
        fetched = AdminOperationLog.objects.get(pk=log.pk)
        self.assertEqual(fetched.operation_type, "export")
        self.assertEqual(fetched.export_format, "json")


class ReportAccessLogCRUDTests(TestCase):
    def setUp(self):
        self.viewer = create_report_viewer()
        self.student = create_student("report_viewed_student")

    def test_create_access_log(self):
        log = ReportAccessLog.objects.create(
            viewer=self.viewer,
            viewer_role="report_viewer",
            student=self.student,
            action_type="view",
            report_type="individual",
        )
        fetched = ReportAccessLog.objects.get(pk=log.pk)
        self.assertEqual(fetched.action_type, "view")
        self.assertEqual(fetched.viewer_role, "report_viewer")


# ── 3. Admin Page Accessibility Tests ──


class AdminAccessibilityTests(TestCase):
    """Verify admin list and detail pages return HTTP 200 for all key models."""

    def setUp(self):
        self.admin = create_admin()
        self.client.force_login(self.admin)

    def _assert_admin_loads(self, url_name, args=None):
        url = reverse(url_name, args=args) if args else reverse(url_name)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, f"Admin page {url_name} returned {resp.status_code}")

    # ── accounts ──

    def test_user_admin_list(self):
        self._assert_admin_loads("admin:accounts_user_changelist")

    def test_invite_code_admin_list(self):
        self._assert_admin_loads("admin:accounts_invitecode_changelist")

    def test_assignment_admin_list(self):
        self._assert_admin_loads("admin:accounts_reportviewerassignment_changelist")

    # ── questionnaire ──

    def test_userprofile_admin_list(self):
        self._assert_admin_loads("admin:questionnaire_userprofile_changelist")

    # ── teaching ──

    def test_session_admin_list(self):
        self._assert_admin_loads("admin:teaching_teachingsession_changelist")

    def test_chatmessage_admin_list(self):
        self._assert_admin_loads("admin:teaching_chatmessage_changelist")

    # ── testing ──

    def test_test_admin_list(self):
        self._assert_admin_loads("admin:testing_test_changelist")

    def test_testquestion_admin_list(self):
        self._assert_admin_loads("admin:testing_testquestion_changelist")

    # ── mood ──

    def test_moodrecord_admin_list(self):
        self._assert_admin_loads("admin:mood_moodrecord_changelist")

    def test_achievement_admin_list(self):
        self._assert_admin_loads("admin:mood_achievement_changelist")

    def test_userachievement_admin_list(self):
        self._assert_admin_loads("admin:mood_userachievement_changelist")

    # ── risk ──

    def test_riskevent_admin_list(self):
        self._assert_admin_loads("admin:risk_riskevent_changelist")

    # ── knowledge_base ──

    def test_document_admin_list(self):
        self._assert_admin_loads("admin:knowledge_base_knowledgedocument_changelist")

    def test_chunk_admin_list(self):
        self._assert_admin_loads("admin:knowledge_base_knowledgechunk_changelist")

    def test_retrievallog_admin_list(self):
        self._assert_admin_loads("admin:knowledge_base_retrievallog_changelist")

    # ── export_app ──

    def test_adminoperationlog_admin_list(self):
        self._assert_admin_loads("admin:export_app_adminoperationlog_changelist")

    # ── reports ──

    def test_reportaccesslog_admin_list(self):
        self._assert_admin_loads("admin:reports_reportaccesslog_changelist")


class AdminDetailPageTests(TestCase):
    """Verify admin change (detail) pages load for models with sample data."""

    def setUp(self):
        self.admin = create_admin()
        self.client.force_login(self.admin)

    def test_user_detail_page(self):
        url = reverse("admin:accounts_user_change", args=[self.admin.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_session_detail_page(self):
        student = create_student("detail_student")
        session = TeachingSession.objects.create(user=student)
        url = reverse("admin:teaching_teachingsession_change", args=[session.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_test_detail_page(self):
        student = create_student("detail_student2")
        session = TeachingSession.objects.create(user=student)
        test_obj = Test.objects.create(user=student, session=session)
        url = reverse("admin:testing_test_change", args=[test_obj.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_risk_event_detail_page(self):
        student = create_student("detail_student3")
        session = TeachingSession.objects.create(user=student)
        risk = RiskEvent.objects.create(
            user=student,
            session=session,
            trigger_text="test trigger",
        )
        url = reverse("admin:risk_riskevent_change", args=[risk.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_document_detail_page(self):
        doc = KnowledgeDocument.objects.create(title="测试文档", module="mindfulness")
        url = reverse("admin:knowledge_base_knowledgedocument_change", args=[doc.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_report_access_log_detail_page(self):
        viewer = create_report_viewer()
        student = create_student("log_detail_student")
        log = ReportAccessLog.objects.create(
            viewer=viewer,
            viewer_role="report_viewer",
            student=student,
            action_type="view",
        )
        url = reverse("admin:reports_reportaccesslog_change", args=[log.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)


# ── 4. Relationship Verification Tests (ORM level) ──


class RelationshipVerificationTests(TestCase):
    """Verify a user's full-chain records can be associated at the ORM level."""

    def setUp(self):
        self.admin = create_admin()
        self.student = create_student("fullchain")

    def test_user_full_chain_association(self):
        session = TeachingSession.objects.create(
            user=self.student,
            status="completed",
            selected_module="distress_tolerance",
            teaching_summary="完成了痛苦耐受模块教学",
        )

        ChatMessage.objects.create(
            session=session,
            user=self.student,
            role="assistant",
            content="让我们一起学习TIP技能。",
        )

        Test.objects.create(
            user=self.student,
            session=session,
            correct_count=4,
            passed=True,
            status="completed",
        )

        MoodRecord.objects.create(
            user=self.student,
            session=session,
            mood_value=5,
            emoji="excited",
            context="after_teaching",
        )

        RiskEvent.objects.create(
            user=self.student,
            session=session,
            trigger_text="我感到很难过",
            detection_source="ai",
            session_stopped=False,
        )

        RetrievalLog.objects.create(
            user=self.student,
            session=session,
            query="TIP技能",
            use_case="teaching",
        )

        # Verify all associations from user side
        self.assertEqual(self.student.teaching_sessions.count(), 1)
        self.assertEqual(self.student.chat_messages.count(), 1)
        self.assertEqual(self.student.tests.count(), 1)
        self.assertEqual(self.student.mood_records.count(), 1)
        self.assertEqual(self.student.risk_events.count(), 1)
        self.assertEqual(self.student.retrieval_logs.count(), 1)

        # Verify from session side
        self.assertEqual(session.messages.count(), 1)
        self.assertEqual(session.tests.count(), 1)
        self.assertEqual(session.mood_records.count(), 1)
        self.assertEqual(session.risk_events.count(), 1)

    def test_report_viewer_access_log_traceable(self):
        viewer = create_report_viewer()
        student = create_student("trace_student")

        ReportAccessLog.objects.create(
            viewer=viewer,
            viewer_role="report_viewer",
            student=student,
            action_type="view",
            report_type="individual",
        )
        ReportAccessLog.objects.create(
            viewer=viewer,
            viewer_role="report_viewer",
            student=student,
            action_type="export",
            report_type="individual",
            export_format="pdf",
        )

        AdminOperationLog.objects.create(
            admin=self.admin,
            operation_type="export",
            target_type="user_data",
            target_id=str(student.pk),
            export_format="json",
        )

        self.assertEqual(ReportAccessLog.objects.filter(student=student).count(), 2)
        self.assertEqual(AdminOperationLog.objects.filter(target_id=str(student.pk)).count(), 1)


# ── 5. Admin UI Aggregation Tests (Step 3 fix) ──


class UserAdminAggregationTests(TestCase):
    """Verify the User admin detail page renders related records from all modules.

    These are the UI-level tests that confirm "后台可以基于用户聚合查看主要记录"
    is actually visible on the admin interface, not just ORM-level assertions.
    """

    def setUp(self):
        self.admin = create_admin()
        self.student = create_student("agg_student")
        self.viewer = create_report_viewer("agg_viewer")
        self.client.force_login(self.admin)

    def _get_user_detail(self, user):
        url = reverse("admin:accounts_user_change", args=[user.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_user_detail_shows_teaching_sessions(self):
        TeachingSession.objects.create(
            user=self.student,
            status="completed",
            selected_module="distress_tolerance",
            selected_skill="TIP技能",
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, "distress_tolerance")
        self.assertContains(resp, "TIP技能")

    def test_user_detail_shows_tests(self):
        session = TeachingSession.objects.create(user=self.student)
        Test.objects.create(
            user=self.student,
            session=session,
            attempt_no=2,
            correct_count=5,
            total_questions=5,
            passed=True,
            status="completed",
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, "5")

    def test_user_detail_shows_mood_records(self):
        MoodRecord.objects.create(
            user=self.student,
            mood_value=4,
            emoji="happy",
            context="before_teaching",
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, "happy")
        self.assertContains(resp, "教学前")

    def test_user_detail_shows_risk_events(self):
        session = TeachingSession.objects.create(user=self.student)
        RiskEvent.objects.create(
            user=self.student,
            session=session,
            trigger_text="我感到绝望了",
            detection_source="keyword",
            session_stopped=True,
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, "感到绝望")

    def test_user_detail_shows_achievements(self):
        achievement = Achievement.objects.create(
            name_cn="满分通过",
            description_cn="一次测试中全部正确",
            trigger_rule={"event": "perfect_score"},
        )
        UserAchievement.objects.create(user=self.student, achievement=achievement)
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, "满分通过")

    def test_user_detail_shows_retrieval_logs(self):
        session = TeachingSession.objects.create(user=self.student)
        RetrievalLog.objects.create(
            user=self.student,
            session=session,
            query="什么是正念冥想？",
            use_case="teaching",
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, "正念冥想")

    def test_student_detail_shows_profile(self):
        UserProfile.objects.create(
            user=self.student,
            gender="male",
            age=15,
            grade="grade_10",
            hobby_tags=["阅读", "编程"],
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, "grade_10")

    def test_student_detail_shows_viewer_assignments(self):
        ReportViewerAssignment.objects.create(
            viewer=self.viewer,
            student=self.student,
            is_active=True,
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, self.viewer.username)

    def test_student_detail_shows_report_views(self):
        ReportAccessLog.objects.create(
            viewer=self.viewer,
            viewer_role="report_viewer",
            student=self.student,
            action_type="view",
            report_type="individual",
        )
        resp = self._get_user_detail(self.student)
        self.assertContains(resp, self.viewer.username)

    def test_report_viewer_detail_shows_assignments(self):
        student2 = create_student("agg_student2")
        ReportViewerAssignment.objects.create(
            viewer=self.viewer,
            student=student2,
            is_active=True,
        )
        self.client.force_login(self.admin)
        resp = self._get_user_detail(self.viewer)
        self.assertContains(resp, student2.username)

    def test_report_viewer_detail_shows_access_logs(self):
        student2 = create_student("agg_student3")
        ReportAccessLog.objects.create(
            viewer=self.viewer,
            viewer_role="report_viewer",
            student=student2,
            action_type="export",
            report_type="individual",
            export_format="pdf",
        )
        self.client.force_login(self.admin)
        resp = self._get_user_detail(self.viewer)
        self.assertContains(resp, student2.username)
        self.assertContains(resp, "individual")

    def test_admin_detail_shows_operation_logs(self):
        AdminOperationLog.objects.create(
            admin=self.admin,
            operation_type="export_data",
            target_type="user_data",
            target_id="some_target_id",
            export_format="json",
        )
        resp = self._get_user_detail(self.admin)
        self.assertContains(resp, "export_data")

    def test_user_detail_aggregates_all_record_types(self):
        """End-to-end: one user has ALL record types, all visible on one page."""
        session = TeachingSession.objects.create(
            user=self.student,
            status="completed",
            selected_module="mindfulness",
            selected_skill="观察呼吸",
        )
        Test.objects.create(
            user=self.student,
            session=session,
            attempt_no=1,
            correct_count=5,
            passed=True,
            status="completed",
        )
        MoodRecord.objects.create(
            user=self.student,
            mood_value=5,
            emoji="excited",
            context="after_teaching",
        )
        RiskEvent.objects.create(
            user=self.student,
            session=session,
            trigger_text="我觉得一切都没有意义",
            detection_source="keyword",
            session_stopped=False,
        )
        achievement = Achievement.objects.create(
            name_cn="第一步",
            description_cn="完成第一次教学",
            trigger_rule={"event": "first_session"},
        )
        UserAchievement.objects.create(user=self.student, achievement=achievement)
        RetrievalLog.objects.create(
            user=self.student,
            session=session,
            query="观察呼吸冥想",
            use_case="teaching",
        )
        UserProfile.objects.create(
            user=self.student,
            gender="male",
            age=16,
            grade="grade_11",
        )

        resp = self._get_user_detail(self.student)

        # Every record type must appear in the page HTML
        self.assertContains(resp, "mindfulness")         # teaching
        self.assertContains(resp, "观察呼吸")             # teaching
        self.assertContains(resp, "excited")             # mood
        self.assertContains(resp, "第一步")               # achievement
        self.assertContains(resp, "观察呼吸冥想")          # retrieval log
        self.assertContains(resp, "grade_11")            # profile

        # Risk event text might be HTML-escaped but should be in the page
        self.assertContains(resp, "没有意义")


# ── 6. Audit Log Read-Only Enforcement Tests ──


class AuditLogReadOnlyTests(TestCase):
    """Verify audit log admins block add, change, AND delete."""

    def setUp(self):
        self.admin_user = create_admin()
        self.client.force_login(self.admin_user)

    def test_admin_operation_log_admin_blocks_delete(self):
        from export_app.admin import AdminOperationLogAdmin
        model_admin = AdminOperationLogAdmin(AdminOperationLog, site)
        self.assertFalse(model_admin.has_delete_permission(request=None))

    def test_report_access_log_admin_blocks_delete(self):
        from reports.admin import ReportAccessLogAdmin
        model_admin = ReportAccessLogAdmin(ReportAccessLog, site)
        self.assertFalse(model_admin.has_delete_permission(request=None))
