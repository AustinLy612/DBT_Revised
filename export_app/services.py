"""User-centered data aggregation and export for admin data export."""

import csv
import json
from io import StringIO


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
            m["created_at"] = m["created_at"].isoformat() if m["created_at"] else None
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
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
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
                "created_at": t.created_at.isoformat() if t.created_at else None,
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
        m["created_at"] = m["created_at"].isoformat() if m["created_at"] else None

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
        a["unlocked_at"] = a["unlocked_at"].isoformat() if a["unlocked_at"] else None

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
        r["trigger_time"] = r["trigger_time"].isoformat() if r["trigger_time"] else None

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
        e["created_at"] = e["created_at"].isoformat() if e["created_at"] else None

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "date_joined": user.date_joined.isoformat() if user.date_joined else None,
        },
        "profile": {
            "gender": profile.gender if profile else "",
            "age": profile.age if profile else None,
            "grade": profile.grade if profile else "",
            "hobbies": profile.hobby_tags if profile else [],
            "troubles": profile.concern_tags if profile else [],
            "other_hobby": profile.other_hobby_text if profile else "",
            "other_concern": profile.other_concern_text if profile else "",
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
    writer.writerow([])

    # Teaching sessions
    writer.writerow(["=== 教学会话 ==="])
    writer.writerow(
        ["会话ID", "技能", "模块", "状态", "阶段", "开始时间", "完成时间", "教学摘要"]
    )
    for s in data["teaching_sessions"]:
        writer.writerow(
            [
                s["session_id"],
                s["selected_skill"],
                s["selected_module"],
                s["status"],
                s["phase"],
                s["started_at"],
                s["completed_at"],
                s["teaching_summary"],
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

    return output.getvalue()
