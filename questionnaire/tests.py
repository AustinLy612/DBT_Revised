from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import UserProfile

User = get_user_model()

VALID_PROFILE_DATA = {
    "gender": "male",
    "age": 15,
    "grade": "grade_9",
    "hobby_tags": ["体育运动", "音乐", "阅读"],
    "concern_tags": ["学业压力", "考试焦虑"],
    "other_hobby_text": "",
    "other_concern_text": "",
}


def create_user(username, password="testpass123", role="student"):
    return User.objects.create_user(username=username, password=password, role=role)


# ── Profile Page Display Tests ──


class ProfilePageTests(TestCase):
    def setUp(self):
        self.user = create_user("student1")
        self.profile_url = reverse("questionnaire:profile")

    def test_profile_page_loads_for_new_user(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "请先完成问卷")

    def test_profile_page_redirects_if_unauthenticated(self):
        resp = self.client.get(self.profile_url)
        self.assertRedirects(resp, f"/accounts/login/?next={self.profile_url}")

    def test_profile_page_shows_modify_title_when_completed(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "修改问卷")

    def test_profile_page_prefills_existing_data(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="female",
            age=14,
            grade="grade_8",
            hobby_tags=["阅读", "写作"],
            concern_tags=["拖延"],
            other_hobby_text="弹吉他",
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        self.assertContains(resp, "female")
        self.assertContains(resp, "14")
        self.assertContains(resp, "grade_8")
        self.assertContains(resp, "弹吉他")


# ── Profile Submission Tests ──


class ProfileSubmissionTests(TestCase):
    def setUp(self):
        self.user = create_user("student1")
        self.profile_url = reverse("questionnaire:profile")
        self.client.force_login(self.user)

    def test_submit_valid_profile_first_time(self):
        resp = self.client.post(self.profile_url, VALID_PROFILE_DATA)
        self.assertRedirects(resp, reverse("index"))

        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.gender, "male")
        self.assertEqual(profile.age, 15)
        self.assertEqual(profile.grade, "grade_9")
        self.assertEqual(profile.hobby_tags, ["体育运动", "音乐", "阅读"])
        self.assertEqual(profile.concern_tags, ["学业压力", "考试焦虑"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_completed)

    def test_submit_valid_profile_modification(self):
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
            hobby_tags=["阅读"],
            concern_tags=["拖延"],
        )
        self.user.profile_completed = True
        self.user.save()

        first_profile = UserProfile.objects.get(user=self.user)
        first_updated_at = first_profile.updated_at

        modified_data = {**VALID_PROFILE_DATA, "age": 16, "grade": "grade_10"}
        resp = self.client.post(self.profile_url, modified_data)
        self.assertRedirects(resp, reverse("index"))

        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.age, 16)
        self.assertEqual(profile.grade, "grade_10")
        # Profile should be updated (updated_at changes)
        self.assertGreater(profile.updated_at, first_updated_at)

    def test_submit_profile_preserves_completed_flag(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user, gender="male", age=15, grade="grade_9"
        )
        resp = self.client.post(self.profile_url, VALID_PROFILE_DATA)
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_completed)

    def test_submit_invalid_age_too_young(self):
        data = {**VALID_PROFILE_DATA, "age": 5}
        resp = self.client.post(self.profile_url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "请输入合理的年龄")

    def test_submit_invalid_age_too_old(self):
        data = {**VALID_PROFILE_DATA, "age": 30}
        resp = self.client.post(self.profile_url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "请输入合理的年龄")

    def test_submit_too_many_hobby_tags(self):
        data = {
            **VALID_PROFILE_DATA,
            "hobby_tags": ["体育运动", "音乐", "绘画/手工", "阅读", "写作", "游戏"],
        }
        resp = self.client.post(self.profile_url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "最多选择 5 项爱好")

    def test_submit_too_many_concern_tags(self):
        data = {
            **VALID_PROFILE_DATA,
            "concern_tags": [
                "学业压力", "考试焦虑", "注意力不集中", "拖延",
                "和同学/朋友关系不好", "感到孤独或被排斥",
            ],
        }
        resp = self.client.post(self.profile_url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "最多选择 5 项困扰")

    def test_submit_empty_tags_is_valid(self):
        """Empty hobby and concern tags are allowed."""
        data = {
            "gender": "male",
            "age": 15,
            "grade": "grade_9",
            "hobby_tags": [],
            "concern_tags": [],
            "other_hobby_text": "",
            "other_concern_text": "",
        }
        resp = self.client.post(self.profile_url, data)
        self.assertRedirects(resp, reverse("index"))
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.hobby_tags, [])
        self.assertEqual(profile.concern_tags, [])

    def test_submit_with_other_text(self):
        data = {
            **VALID_PROFILE_DATA,
            "hobby_tags": ["其他"],
            "other_hobby_text": "打羽毛球",
            "concern_tags": ["其他"],
            "other_concern_text": "和弟弟的关系不太好",
        }
        resp = self.client.post(self.profile_url, data)
        self.assertRedirects(resp, reverse("index"))
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.other_hobby_text, "打羽毛球")
        self.assertEqual(profile.other_concern_text, "和弟弟的关系不太好")

    def test_submit_concern_prefer_not_to_answer(self):
        data = {
            **VALID_PROFILE_DATA,
            "concern_tags": ["不想回答"],
        }
        resp = self.client.post(self.profile_url, data)
        self.assertRedirects(resp, reverse("index"))
        profile = UserProfile.objects.get(user=self.user)
        self.assertIn("不想回答", profile.concern_tags)

    def test_profile_completed_flag_after_first_submit(self):
        self.assertFalse(self.user.profile_completed)
        resp = self.client.post(self.profile_url, VALID_PROFILE_DATA)
        self.assertRedirects(resp, reverse("index"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_completed)


# ── Profile-Required Redirect Tests ──


class ProfileRequiredTests(TestCase):
    def setUp(self):
        self.user = create_user("student1")
        self.index_url = reverse("index")
        self.profile_url = reverse("questionnaire:profile")

    def test_index_shows_questionnaire_prompt_without_profile(self):
        """Index page shows prompt to complete questionnaire, not teaching button."""
        self.client.force_login(self.user)
        resp = self.client.get(self.index_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "填写问卷")
        self.assertNotContains(resp, "开始教学")

    def test_index_shows_teaching_button_with_profile(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user, gender="male", age=15, grade="grade_9"
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.index_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "开始教学")

    def test_profile_page_not_blocked_by_itself(self):
        """The questionnaire page must be accessible without a completed profile."""
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_index_shows_login_register(self):
        resp = self.client.get(self.index_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "登录")
        self.assertContains(resp, "注册")


# ── Profile Data Persistence Tests ──


class ProfileDataTests(TestCase):
    def setUp(self):
        self.user = create_user("student1")
        self.profile_url = reverse("questionnaire:profile")
        self.client.force_login(self.user)

    def test_full_profile_persists_all_fields(self):
        data = {
            "gender": "female",
            "age": 13,
            "grade": "grade_7",
            "hobby_tags": ["动漫/影视", "游戏", "编程/科技", "宠物", "独处/安静活动"],
            "concern_tags": ["不自信/自我评价低", "外貌或身体形象焦虑", "拖延"],
            "other_hobby_text": "cosplay",
            "other_concern_text": "最近转学了不适应",
        }
        self.client.post(self.profile_url, data)

        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.gender, "female")
        self.assertEqual(profile.age, 13)
        self.assertEqual(profile.grade, "grade_7")
        self.assertEqual(len(profile.hobby_tags), 5)
        self.assertIn("动漫/影视", profile.hobby_tags)
        self.assertIn("cosplay", profile.other_hobby_text)
        self.assertIn("最近转学了不适应", profile.other_concern_text)

    def test_profile_updated_at_changes(self):
        self.client.post(self.profile_url, VALID_PROFILE_DATA)
        first = UserProfile.objects.get(user=self.user)
        first_updated = first.updated_at

        # Modify
        mod_data = {**VALID_PROFILE_DATA, "age": 16}
        self.client.post(self.profile_url, mod_data)
        second = UserProfile.objects.get(user=self.user)

        self.assertGreater(second.updated_at, first_updated)

    def test_profile_created_at_does_not_change(self):
        self.client.post(self.profile_url, VALID_PROFILE_DATA)
        first = UserProfile.objects.get(user=self.user)
        first_created = first.created_at

        mod_data = {**VALID_PROFILE_DATA, "age": 16}
        self.client.post(self.profile_url, mod_data)
        second = UserProfile.objects.get(user=self.user)

        self.assertEqual(second.created_at, first_created)


# ── Teaching Entry Point Enforcement Tests ──


class TeachingEnforcementTests(TestCase):
    """Verify profile_required decorator actually blocks teaching, and that
    the teaching page consumes the latest UserProfile data."""

    def setUp(self):
        self.user = create_user("student1")
        self.teaching_url = reverse("teaching:home")
        self.profile_url = reverse("questionnaire:profile")

    def test_teaching_redirects_without_profile(self):
        """profile_required must redirect to questionnaire when no profile."""
        self.client.force_login(self.user)
        resp = self.client.get(self.teaching_url)
        self.assertRedirects(resp, self.profile_url)

    def test_teaching_accessible_with_profile(self):
        """Teaching page loads when profile is completed."""
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.teaching_url)
        self.assertEqual(resp.status_code, 200)

    def test_teaching_displays_profile_data(self):
        """Teaching page reads and displays the latest UserProfile data."""
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="female",
            age=14,
            grade="grade_8",
            hobby_tags=["阅读", "写作"],
            concern_tags=["拖延"],
            other_hobby_text="弹吉他",
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.teaching_url)
        self.assertContains(resp, "女")
        self.assertContains(resp, "14 岁")
        self.assertContains(resp, "初二")
        self.assertContains(resp, "阅读")
        self.assertContains(resp, "写作")
        self.assertContains(resp, "拖延")
        self.assertContains(resp, "弹吉他")

    def test_teaching_reflects_updated_profile(self):
        """After modifying the questionnaire, the teaching page shows new data."""
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
            hobby_tags=["游戏"],
        )
        self.client.force_login(self.user)

        # Modify the profile
        self.client.post(
            self.profile_url,
            {**VALID_PROFILE_DATA, "age": 16, "hobby_tags": ["音乐", "阅读"]},
        )

        resp = self.client.get(self.teaching_url)
        self.assertContains(resp, "16 岁")
        self.assertContains(resp, "音乐")
        self.assertNotContains(resp, "游戏")

    def test_teaching_unauthenticated_redirects_to_login(self):
        resp = self.client.get(self.teaching_url)
        self.assertRedirects(resp, f"/accounts/login/?next={self.teaching_url}")


# ── "Other" Textarea Visibility Tests ──


class OtherTextareaVisibilityTests(TestCase):
    """Verify the 「其他」 supplementary textareas are visible on edit when
    the user has previously selected the 「其他」 checkbox."""

    def setUp(self):
        self.user = create_user("student1")
        self.profile_url = reverse("questionnaire:profile")

    def test_edit_page_shows_other_hobby_textarea(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
            hobby_tags=["其他"],
            other_hobby_text="打羽毛球",
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        # The textarea should be visible (x-show=true, no display:none)
        self.assertContains(resp, "打羽毛球")
        # Alpine initial state should be true
        self.assertContains(resp, "hobbyOther: true")

    def test_edit_page_shows_other_concern_textarea(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
            concern_tags=["其他"],
            other_concern_text="和弟弟的关系不太好",
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        self.assertContains(resp, "和弟弟的关系不太好")
        self.assertContains(resp, "concernOther: true")

    def test_edit_page_hides_textarea_when_not_selected(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
            hobby_tags=["阅读"],
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        self.assertContains(resp, "hobbyOther: false")
        self.assertContains(resp, "concernOther: false")

    def test_edit_page_shows_both_textareas_when_both_other_selected(self):
        self.user.profile_completed = True
        self.user.save()
        UserProfile.objects.create(
            user=self.user,
            gender="male",
            age=15,
            grade="grade_9",
            hobby_tags=["其他"],
            concern_tags=["其他"],
            other_hobby_text="徒步",
            other_concern_text="对未来感到迷茫",
        )
        self.client.force_login(self.user)
        resp = self.client.get(self.profile_url)
        self.assertContains(resp, "hobbyOther: true")
        self.assertContains(resp, "concernOther: true")
        self.assertContains(resp, "徒步")
        self.assertContains(resp, "对未来感到迷茫")
