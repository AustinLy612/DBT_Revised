"""Mood recording and achievement system services.

Achievement checking runs after key events:
  - Session completion → check training-based achievements
  - Test completion → check test-based achievements
  - Mood recording → check mood-based achievements
  - Login → check streak-based achievements
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from django.db import models
from django.utils import timezone

logger = logging.getLogger("dbt_platform.mood")

# ═══════════════════════════════════════════════════════════════
# Mood recording
# ═══════════════════════════════════════════════════════════════

_EMOJI_MAP = {1: "😫", 2: "😟", 3: "😐", 4: "🙂", 5: "😄"}


def emoji_for_value(value: int) -> str:
    return _EMOJI_MAP.get(value, "😐")


def record_mood(
    user: models.Model,
    mood_value: int,
    context: str,
    session: models.Model | None = None,
    note: str = "",
) -> str:
    """Record a mood entry and check achievement unlocks.

    Returns the mood_id.
    """
    from .models import MoodRecord

    mood = MoodRecord.objects.create(
        user=user,
        session=session,
        mood_value=mood_value,
        emoji=emoji_for_value(mood_value),
        note=note,
        context=context,
    )
    logger.info("Mood %s recorded for user %s, context=%s, value=%d",
                mood.mood_id, user.id, context, mood_value)

    # Check achievements after mood recording
    check_and_award_achievements(user, event="mood_recorded")

    return mood.mood_id


def record_post_teaching_mood(
    session: models.Model,
    user: models.Model,
    mood_value: int,
    note: str = "",
) -> str:
    """Record the post-teaching mood and link it to the session."""
    from .models import MoodRecord

    mood_id = record_mood(user, mood_value, MoodRecord.Context.AFTER_TEACHING, session, note)
    session.post_mood_id = mood_id
    session.save(update_fields=["post_mood_id"])
    return mood_id


def record_post_test_mood(
    test: models.Model,
    user: models.Model,
    mood_value: int,
    note: str = "",
) -> str:
    """Record the post-test mood and link it to the test."""
    from .models import MoodRecord

    mood_id = record_mood(user, mood_value, MoodRecord.Context.AFTER_TESTING,
                          test.session if test else None, note)
    test.post_mood_id = mood_id
    test.save(update_fields=["post_mood_id"])
    return mood_id


def get_mood_history(user: models.Model, limit: int = 50) -> list[dict[str, Any]]:
    """Return mood history for a user."""
    from .models import MoodRecord

    records = MoodRecord.objects.filter(user=user).order_by("-created_at")[:limit]
    return [
        {
            "mood_id": r.mood_id,
            "mood_value": r.mood_value,
            "emoji": r.emoji,
            "note": r.note,
            "context": r.context,
            "context_display": r.get_context_display(),
            "created_at": r.created_at.isoformat(),
            "session_id": r.session.session_id if r.session else None,
        }
        for r in records
    ]


# ═══════════════════════════════════════════════════════════════
# Achievement definitions
# ═══════════════════════════════════════════════════════════════

ACHIEVEMENT_DEFS: list[dict[str, Any]] = [
    {
        "key": "first_step",
        "name_cn": "第一步",
        "description_cn": "完成第一次完整训练（教学+测试通过4/5）",
        "icon": "🌟",
        "trigger_rule": {"type": "successful_training_count", "threshold": 1},
    },
    {
        "key": "ten_trainings",
        "name_cn": "十次训练",
        "description_cn": "累计完成10次成功训练",
        "icon": "🔥",
        "trigger_rule": {"type": "successful_training_count", "threshold": 10},
    },
    {
        "key": "seven_day_streak",
        "name_cn": "七日坚持",
        "description_cn": "连续学习7天",
        "icon": "📅",
        "trigger_rule": {"type": "consecutive_days", "threshold": 7},
    },
    {
        "key": "fifteen_day_streak",
        "name_cn": "十五日坚持",
        "description_cn": "连续学习15天",
        "icon": "🏆",
        "trigger_rule": {"type": "consecutive_days", "threshold": 15},
    },
    {
        "key": "first_test_fail",
        "name_cn": "第一次测试未通过",
        "description_cn": "第一次测试未通过",
        "icon": "💪",
        "trigger_rule": {"type": "first_test_failed", "threshold": 1},
    },
    {
        "key": "five_test_fails",
        "name_cn": "五次测试未通过",
        "description_cn": "累计5次测试未通过",
        "icon": "🎯",
        "trigger_rule": {"type": "total_test_fails", "threshold": 5},
    },
    {
        "key": "mindfulness_start",
        "name_cn": "正念入门",
        "description_cn": "完成第一次正念技能训练",
        "icon": "🧘",
        "trigger_rule": {"type": "first_mindfulness_training", "threshold": 1},
    },
    {
        "key": "perfect_score",
        "name_cn": "满分通过",
        "description_cn": "单次测试5题全部答对",
        "icon": "✨",
        "trigger_rule": {"type": "perfect_test", "threshold": 1},
    },
    {
        "key": "comeback",
        "name_cn": "重新出发",
        "description_cn": "一次测试未通过后，重测通过",
        "icon": "🔄",
        "trigger_rule": {"type": "retest_passed", "threshold": 1},
    },
    {
        "key": "mood_start",
        "name_cn": "情绪记录开始",
        "description_cn": "第一次完成情绪记录",
        "icon": "💭",
        "trigger_rule": {"type": "mood_recorded", "threshold": 1},
    },
]


def ensure_achievements_exist() -> None:
    """Create all defined achievements if they don't exist."""
    from .models import Achievement

    existing = set(Achievement.objects.filter(
        name_cn__in=[a["name_cn"] for a in ACHIEVEMENT_DEFS]
    ).values_list("name_cn", flat=True))

    created = 0
    for ach_def in ACHIEVEMENT_DEFS:
        if ach_def["name_cn"] not in existing:
            Achievement.objects.create(
                name_cn=ach_def["name_cn"],
                description_cn=ach_def["description_cn"],
                icon=ach_def["icon"],
                trigger_rule=ach_def["trigger_rule"],
                is_active=True,
            )
            created += 1

    if created:
        logger.info("Created %d new achievement definitions", created)


# ═══════════════════════════════════════════════════════════════
# Achievement checking
# ═══════════════════════════════════════════════════════════════

def check_and_award_achievements(user: models.Model, event: str = "") -> dict[str, Any]:
    """Check all achievement conditions and award any newly earned.

    Call this after: session completion, test completion, mood recording, login.
    """
    from .models import Achievement, UserAchievement

    ensure_achievements_exist()

    # Gather stats
    stats = _gather_user_stats(user)
    newly_unlocked: list[str] = []

    all_achievements = Achievement.objects.filter(is_active=True)

    for achievement in all_achievements:
        # Skip already unlocked
        if UserAchievement.objects.filter(user=user, achievement=achievement).exists():
            continue

        rule = achievement.trigger_rule or {}
        if _check_condition(user, rule, stats):
            UserAchievement.objects.create(user=user, achievement=achievement)
            newly_unlocked.append(achievement.name_cn)
            logger.info("Achievement unlocked: %s for user %s", achievement.name_cn, user.id)

    if newly_unlocked:
        logger.info("User %s unlocked %d new achievements: %s",
                    user.id, len(newly_unlocked), newly_unlocked)

    return {"newly_unlocked": newly_unlocked, "stats": stats}


def _check_condition(user: models.Model, rule: dict[str, Any], stats: dict[str, Any]) -> bool:
    """Evaluate a single achievement rule against current stats."""
    rule_type = rule.get("type", "")
    threshold = rule.get("threshold", 1)

    if rule_type == "successful_training_count":
        return stats["successful_trainings"] >= threshold

    elif rule_type == "consecutive_days":
        return stats["consecutive_days"] >= threshold

    elif rule_type == "first_test_failed":
        return stats["first_test_failed"]

    elif rule_type == "total_test_fails":
        return stats["total_test_fails"] >= threshold

    elif rule_type == "first_mindfulness_training":
        return stats["has_mindfulness_training"]

    elif rule_type == "perfect_test":
        return stats["has_perfect_test"]

    elif rule_type == "retest_passed":
        return stats["has_retest_passed"]

    elif rule_type == "mood_recorded":
        return stats["total_mood_records"] >= threshold

    return False


def _gather_user_stats(user: models.Model) -> dict[str, Any]:
    """Gather all stats needed for achievement checking."""
    from datetime import date as dt_date

    from mood.models import MoodRecord
    from teaching.models import TeachingSession

    # ── Successful training count ──
    # "成功训练" = teaching completed + test passed (4+/5)
    from testing.models import Test

    all_tests = Test.objects.filter(user=user).order_by("created_at")
    successful_trainings = sum(1 for t in all_tests if t.passed)
    total_tests = all_tests.count()

    # ── First test failed ──
    first_test_failed = False
    if total_tests > 0:
        first_test = all_tests.first()
        first_test_failed = not first_test.passed

    # ── Total test fails ──
    total_test_fails = sum(1 for t in all_tests if not t.passed)

    # ── Has mindfulness training ──
    # Must have completed a session about a 正念 (mindfulness) skill, not just any skill.
    has_mindfulness_training = TeachingSession.objects.filter(
        user=user, status=TeachingSession.Status.COMPLETED
    ).filter(
        models.Q(selected_module__icontains="正念") | models.Q(selected_skill__icontains="正念")
    ).exists()

    # ── Has perfect test ──
    has_perfect_test = any(t.correct_count == 5 and t.total_questions == 5 for t in all_tests)

    # ── Has retest passed ──
    # Retest passed = a session has multiple tests, first failed, a later one passed
    has_retest_passed = False
    session_test_map: dict[str, list[models.Model]] = {}
    for t in all_tests:
        sid = t.session_id if t.session_id else str(t.session.session_id)
        session_test_map.setdefault(sid, []).append(t)
    for tests in session_test_map.values():
        if len(tests) >= 2:
            failed_first = any(not t.passed and t.attempt_no == 1 for t in tests)
            passed_later = any(t.passed and t.attempt_no > 1 for t in tests)
            if failed_first and passed_later:
                has_retest_passed = True
                break

    # ── Consecutive learning days ──
    consecutive_days = _compute_consecutive_learning_days(user)

    # ── Total mood records ──
    total_mood_records = MoodRecord.objects.filter(user=user).count()

    # ── Total trainings (teaching sessions completed) ──
    total_trainings = TeachingSession.objects.filter(
        user=user, status=TeachingSession.Status.COMPLETED
    ).count()

    # ── Total teaching sessions ever ──
    total_sessions = TeachingSession.objects.filter(user=user).count()

    return {
        "total_trainings": total_trainings,
        "successful_trainings": successful_trainings,
        "total_sessions": total_sessions,
        "total_tests": total_tests,
        "first_test_failed": first_test_failed,
        "total_test_fails": total_test_fails,
        "has_mindfulness_training": has_mindfulness_training,
        "has_perfect_test": has_perfect_test,
        "has_retest_passed": has_retest_passed,
        "consecutive_days": consecutive_days,
        "consecutive_learning_days": consecutive_days,
        "total_mood_records": total_mood_records,
    }


def _compute_consecutive_learning_days(user: models.Model) -> int:
    """Compute consecutive learning days from completed teaching session dates.

    "连续学习天数" = number of consecutive days (going backwards from today)
    where the user completed at least one teaching session each day.
    """
    from teaching.models import TeachingSession

    completed_dates = (
        TeachingSession.objects
        .filter(user=user, status=TeachingSession.Status.COMPLETED)
        .values_list("completed_at", flat=True)
    )

    unique_dates = sorted(set(
        d.date() for d in completed_dates if d is not None
    ), reverse=True)

    if not unique_dates:
        return 0

    today = date.today()
    most_recent = unique_dates[0]

    # The most recent learning day must be today or yesterday for a streak to be active
    if (today - most_recent).days > 1:
        return 0

    consecutive = 1
    for i in range(1, len(unique_dates)):
        if (unique_dates[i - 1] - unique_dates[i]).days == 1:
            consecutive += 1
        else:
            break

    return consecutive


def get_user_achievements(user: models.Model) -> dict[str, Any]:
    """Get all achievements with unlock status for a user."""
    from .models import Achievement, UserAchievement

    ensure_achievements_exist()

    unlocked = set(
        UserAchievement.objects.filter(user=user)
        .values_list("achievement__name_cn", flat=True)
    )

    all_achievements = Achievement.objects.filter(is_active=True).order_by("name_cn")
    achievement_list = []
    for ach in all_achievements:
        achievement_list.append({
            "achievement_id": ach.achievement_id,
            "name_cn": ach.name_cn,
            "description_cn": ach.description_cn,
            "icon": ach.icon,
            "trigger_rule": ach.trigger_rule,
            "unlocked": ach.name_cn in unlocked,
            "unlocked_at": (
                UserAchievement.objects.filter(
                    user=user, achievement=ach
                ).first().unlocked_at.isoformat()
                if ach.name_cn in unlocked else None
            ),
        })

    stats = _gather_user_stats(user)

    return {
        "achievements": achievement_list,
        "unlocked_count": len(unlocked),
        "total_count": len(all_achievements),
        **stats,
    }


def get_stats_for_reports(user: models.Model) -> dict[str, Any]:
    """Aggregated stats for individual visualization reports (Step 12)."""
    from mood.models import MoodRecord
    from teaching.models import TeachingSession

    stats = _gather_user_stats(user)

    # Mood trend data (last 30 days)
    mood_records = MoodRecord.objects.filter(user=user).order_by("-created_at")[:100]
    mood_trend = [
        {
            "date": r.created_at.date().isoformat(),
            "value": r.mood_value,
            "emoji": r.emoji,
            "context": r.context,
        }
        for r in mood_records
    ]

    # Skill learning frequency
    from collections import Counter
    completed_sessions = TeachingSession.objects.filter(
        user=user, status=TeachingSession.Status.COMPLETED
    )
    skill_counts = Counter(
        s.selected_skill for s in completed_sessions if s.selected_skill
    )
    skill_frequency = [{"skill": k, "count": v} for k, v in skill_counts.most_common()]

    # Test performance over time
    from testing.models import Test
    tests = Test.objects.filter(user=user).order_by("created_at")
    test_performance = [
        {
            "date": t.created_at.date().isoformat() if t.created_at else None,
            "correct_count": t.correct_count,
            "total_questions": t.total_questions,
            "passed": t.passed,
            "attempt_no": t.attempt_no,
            "skill": t.session.selected_skill if t.session else "",
        }
        for t in tests
    ]

    achievements = get_user_achievements(user)

    return {
        "mood_trend": mood_trend,
        "skill_frequency": skill_frequency,
        "test_performance": test_performance,
        "stats_summary": {
            "total_trainings": stats["total_trainings"],
            "successful_trainings": stats["successful_trainings"],
            "total_tests": stats["total_tests"],
            "total_mood_records": stats["total_mood_records"],
            "achievements_unlocked": achievements["unlocked_count"],
            "consecutive_days": stats["consecutive_days"],
        },
    }
