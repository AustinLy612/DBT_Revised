"""User-centered data aggregation and export for admin data export."""

import csv
import json
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

from django.utils import timezone

# All exported timestamps are normalized to China Standard Time (UTC+8).
_EXPORT_TZ = ZoneInfo("Asia/Shanghai")


def format_datetime_shanghai(value):
    """Convert a datetime to Asia/Shanghai and return ISO-8601 with +08:00.

    Aware datetimes are converted from their original timezone (usually UTC).
    Naive datetimes are treated as UTC (Django's storage convention with USE_TZ).
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value
    if timezone.is_naive(value):
        value = timezone.make_aware(value, ZoneInfo("UTC"))
    return timezone.localtime(value, _EXPORT_TZ).isoformat()


def aggregate_user_data(user):
    """Aggregate full-chain data for a single user.

    Returns a nested dict with: profile, teaching_sessions, tests, mood_records,
    risk_events, achievements.
    """
    from questionnaire.models import UserProfile

    profile = None
    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        pass

    from teaching.models import ChatMessage, TeachingSession

    sessions = TeachingSession.objects.filter(user=user).order_by("-started_at")
    sessions_data = []
    for s in sessions:
        messages = list(
            ChatMessage.objects.filter(session=s)
            .order_by("created_at")
            .values("message_id", "role", "content", "modality", "created_at")
        )
        for m in messages:
            m["created_at"] = format_datetime_shanghai(m["created_at"])
        sessions_data.append(
            {
                "session_id": s.session_id,
                "selected_module": s.selected_module,
                "selected_skill": s.selected_skill,
                "selection_reason": s.selection_reason,
                "teaching_plan": s.teaching_plan,
                "teaching_summary": s.teaching_summary,
                "phase": s.phase,
                "status": s.status,
                "llm_provider": s.llm_provider,
                "started_at": format_datetime_shanghai(s.started_at),
                "completed_at": format_datetime_shanghai(s.completed_at),
                "messages": messages,
            }
        )

    from testing.models import Test, TestQuestion

    tests = Test.objects.filter(user=user).order_by("-created_at")
    tests_data = []
    for t in tests:
        questions = list(
            TestQuestion.objects.filter(test=t)
            .order_by("created_at")
            .values(
                "question_id",
                "scenario_text",
                "question_text",
                "options",
                "correct_option",
                "user_answer",
                "is_correct",
                "explanation",
                "source_chunk_ids",
            )
        )
        tests_data.append(
            {
                "test_id": t.test_id,
                "session_id": t.session_id,
                "attempt_no": t.attempt_no,
                "total_questions": t.total_questions,
                "correct_count": t.correct_count,
                "passed": t.passed,
                "status": t.status,
                "created_at": format_datetime_shanghai(t.created_at),
                "questions": questions,
            }
        )

    from mood.models import MoodRecord, UserAchievement

    mood_records = list(
        MoodRecord.objects.filter(user=user)
        .order_by("created_at")
        .values("mood_id", "mood_value", "emoji", "note", "context", "created_at")
    )
    for m in mood_records:
        m["created_at"] = format_datetime_shanghai(m["created_at"])

    achievements = list(
        UserAchievement.objects.filter(user=user)
        .select_related("achievement")
        .order_by("-unlocked_at")
        .values(
            "id",
            "achievement__name_cn",
            "achievement__description_cn",
            "achievement__icon",
            "unlocked_at",
        )
    )
    for a in achievements:
        a["unlocked_at"] = format_datetime_shanghai(a["unlocked_at"])

    from risk.models import RiskEvent

    risk_events = list(
        RiskEvent.objects.filter(user=user)
        .order_by("-trigger_time")
        .values(
            "risk_event_id",
            "trigger_text",
            "detection_source",
            "action_taken",
            "follow_up_mode",
            "session_stopped",
            "trigger_time",
        )
    )
    for r in risk_events:
        r["trigger_time"] = format_datetime_shanghai(r["trigger_time"])

    from ema_log.models import EMASubmission

    ema_submissions = list(
        EMASubmission.objects.filter(user=user)
        .order_by("-created_at")
        .values(
            "submission_id",
            "sad_score",
            "anxious_score",
            "angry_score",
            "calm_score",
            "hopeful_score",
            "distress_score",
            "nssi_urge_score",
            "suicide_urge_score",
            "used_dbt_skill",
            "dbt_skills_used",
            "skill_effectiveness_score",
            "medical_doctor_visit",
            "medical_group_therapy",
            "medical_medication_change",
            "created_at",
        )
    )
    for e in ema_submissions:
        e["created_at"] = format_datetime_shanghai(e["created_at"])

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "date_joined": format_datetime_shanghai(user.date_joined),
        },
        "profile": {
            "gender": profile.gender if profile else "",
            "age": profile.age if profile else None,
            "grade": profile.grade if profile else "",
            "hobbies": profile.hobby_tags if profile else [],
            "troubles": profile.concern_tags if profile else [],
            "other_hobby": profile.other_hobby_text if profile else "",
            "other_concern": profile.other_concern_text if profile else "",
            "profile_created_at": (
                format_datetime_shanghai(profile.created_at) if profile else None
            ),
            "profile_updated_at": (
                format_datetime_shanghai(profile.updated_at) if profile else None
            ),
        },
        "teaching_sessions": sessions_data,
        "tests": tests_data,
        "mood_records": mood_records,
        "risk_events": risk_events,
        "achievements": achievements,
        "ema_submissions": ema_submissions,
    }


def export_user_json(user):
    """Return JSON string of full user data."""
    data = aggregate_user_data(user)
    return json.dumps(data, ensure_ascii=False, indent=2)


def export_user_csv(user):
    """Return CSV string with flattened user data across multiple sections."""
    data = aggregate_user_data(user)
    output = StringIO()
    writer = csv.writer(output)

    # Profile section
    writer.writerow(["=== 用户信息 ==="])
    u = data["user"]
    p = data["profile"]
    writer.writerow(["用户名", "角色", "注册时间", "性别", "年龄", "年级"])
    writer.writerow(
        [u["username"], u["role"], u["date_joined"], p["gender"], p["age"], p["grade"]]
    )
    writer.writerow(["爱好", ", ".join(p["hobbies"]) if p["hobbies"] else ""])
    writer.writerow(["其他爱好", p.get("other_hobby", "")])
    writer.writerow(["困扰", ", ".join(p["troubles"]) if p["troubles"] else ""])
    writer.writerow(["其他困扰", p.get("other_concern", "")])
    writer.writerow(["问卷创建时间", p.get("profile_created_at") or ""])
    writer.writerow(["问卷更新时间", p.get("profile_updated_at") or ""])
    writer.writerow([])

    # Teaching sessions
    writer.writerow(["=== 教学会话 ==="])
    writer.writerow(
        [
            "会话ID",
            "技能",
            "模块",
            "状态",
            "阶段",
            "LLM提供方",
            "开始时间",
            "完成时间",
            "教学摘要",
        ]
    )
    for s in data["teaching_sessions"]:
        writer.writerow(
            [
                s["session_id"],
                s["selected_skill"],
                s["selected_module"],
                s["status"],
                s["phase"],
                s["llm_provider"],
                s["started_at"],
                s["completed_at"],
                s["teaching_summary"],
            ]
        )
    writer.writerow([])

    # Chat messages (full teaching record)
    writer.writerow(["=== 教学对话 ==="])
    writer.writerow(["会话ID", "消息ID", "角色", "模态", "内容", "时间"])
    for s in data["teaching_sessions"]:
        for m in s.get("messages") or []:
            writer.writerow(
                [
                    s["session_id"],
                    m.get("message_id", ""),
                    m.get("role", ""),
                    m.get("modality", ""),
                    m.get("content", ""),
                    m.get("created_at", ""),
                ]
            )
    writer.writerow([])

    # Tests
    writer.writerow(["=== 测试记录 ==="])
    writer.writerow(
        ["测试ID", "会话ID", "尝试次数", "正确数", "总题数", "通过", "状态", "时间"]
    )
    for t in data["tests"]:
        writer.writerow(
            [
                t["test_id"],
                t["session_id"],
                t["attempt_no"],
                t["correct_count"],
                t["total_questions"],
                "是" if t["passed"] else "否",
                t["status"],
                t["created_at"],
            ]
        )
    writer.writerow([])

    # Test questions
    writer.writerow(["=== 测试题目 ==="])
    writer.writerow(
        [
            "测试ID",
            "题目ID",
            "题干",
            "选项",
            "正确答案",
            "用户作答",
            "是否正确",
            "解析",
        ]
    )
    for t in data["tests"]:
        for q in t.get("questions") or []:
            writer.writerow(
                [
                    t["test_id"],
                    q.get("question_id", ""),
                    q.get("question_text", ""),
                    json.dumps(q.get("options"), ensure_ascii=False),
                    q.get("correct_option", ""),
                    q.get("user_answer", ""),
                    q.get("is_correct", ""),
                    q.get("explanation", ""),
                ]
            )
    writer.writerow([])

    # Mood records
    writer.writerow(["=== 情绪记录 ==="])
    writer.writerow(["记录ID", "情绪值", "表情", "场景", "备注", "时间"])
    for m in data["mood_records"]:
        writer.writerow(
            [
                m["mood_id"],
                m["mood_value"],
                m["emoji"],
                m["context"],
                m["note"],
                m["created_at"],
            ]
        )
    writer.writerow([])

    # Risk events
    writer.writerow(["=== 风险事件 ==="])
    writer.writerow(
        ["事件ID", "触发文本", "检测来源", "动作", "后续模式", "会话中止", "时间"]
    )
    for r in data["risk_events"]:
        writer.writerow(
            [
                r["risk_event_id"],
                r["trigger_text"],
                r["detection_source"],
                r["action_taken"],
                r["follow_up_mode"],
                "是" if r["session_stopped"] else "否",
                r["trigger_time"],
            ]
        )
    writer.writerow([])

    # Achievements
    writer.writerow(["=== 成就 ==="])
    writer.writerow(["成就名称", "描述", "解锁时间"])
    for a in data["achievements"]:
        writer.writerow(
            [
                a["achievement__name_cn"],
                a["achievement__description_cn"],
                a["unlocked_at"],
            ]
        )
    writer.writerow([])

    # EMA diary submissions
    writer.writerow(["=== EMA日志 ==="])
    writer.writerow(
        [
            "提交ID",
            "悲伤",
            "焦虑",
            "愤怒",
            "平静",
            "希望",
            "痛苦",
            "自伤冲动",
            "自杀冲动",
            "是否使用DBT技能",
            "使用的技能",
            "技能有效性",
            "就医",
            "团体治疗",
            "用药变更",
            "时间",
        ]
    )
    for e in data["ema_submissions"]:
        skills = e.get("dbt_skills_used") or []
        if isinstance(skills, list):
            skills = ", ".join(str(s) for s in skills)
        writer.writerow(
            [
                e.get("submission_id", ""),
                e.get("sad_score", ""),
                e.get("anxious_score", ""),
                e.get("angry_score", ""),
                e.get("calm_score", ""),
                e.get("hopeful_score", ""),
                e.get("distress_score", ""),
                e.get("nssi_urge_score", ""),
                e.get("suicide_urge_score", ""),
                e.get("used_dbt_skill", ""),
                skills,
                e.get("skill_effectiveness_score", ""),
                e.get("medical_doctor_visit", ""),
                e.get("medical_group_therapy", ""),
                e.get("medical_medication_change", ""),
                e.get("created_at", ""),
            ]
        )

    return output.getvalue()


def is_excluded_test_username(username: str) -> bool:
    """Return True for load-test / synthetic test accounts that should not be exported."""
    name = (username or "").strip().lower()
    if not name:
        return True
    if name.startswith("loadtest"):
        return True
    if name == "test" or name.startswith("test_") or name.startswith("test"):
        # Covers test, test1, test_user, teststudent, etc.
        return True
    return False


def iter_exportable_students():
    """Yield student users excluding loadtest/test synthetic accounts."""
    from accounts.models import User

    qs = User.objects.filter(role="student").order_by("username")
    for user in qs:
        if not is_excluded_test_username(user.username):
            yield user
