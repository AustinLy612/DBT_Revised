"""Report data aggregation for individual student reports."""

from collections import Counter
from django.utils.translation import gettext as _


def get_student_report_data(student):
    """Aggregate all report data for a single student.

    Returns a dict with keys: profile, mood_history, mood_svg_chart,
    skill_counts, test_summary, achievements, summary, session_count, etc.
    """
    from mood.models import MoodRecord, UserAchievement

    # ── Profile ──
    profile = _get_profile(student)

    # ── Mood history + SVG chart ──
    mood_qs = (
        MoodRecord.objects.filter(user=student)
        .order_by("created_at")
        .values("mood_value", "emoji", "note", "context", "created_at")
    )
    mood_history = list(mood_qs)
    mood_values = [m["mood_value"] for m in mood_history]
    mood_svg = _render_mood_chart_svg(mood_values)

    # ── Teaching sessions & skill counts ──
    from teaching.models import TeachingSession

    sessions = TeachingSession.objects.filter(user=student).order_by("-started_at")
    completed_sessions = sessions.filter(status="completed")
    skill_counter = Counter()
    for s in completed_sessions:
        if s.selected_skill:
            skill_counter[s.selected_skill] += 1
    skill_counts = [
        {"skill": skill, "count": count}
        for skill, count in skill_counter.most_common()
    ]

    # ── Tests ──
    from testing.models import Test

    tests = Test.objects.filter(user=student).order_by("-created_at")
    test_summary = []
    total_correct = 0
    total_questions = 0
    passed_count = 0
    for t in tests:
        test_summary.append(
            {
                "test_id": t.test_id,
                "session_skill": t.session.selected_skill or "",
                "attempt_no": t.attempt_no,
                "total_questions": t.total_questions,
                "correct_count": t.correct_count,
                "passed": t.passed,
                "status": t.status,
                "created_at": t.created_at,
            }
        )
        if t.status == "completed":
            total_correct += t.correct_count
            total_questions += t.total_questions
            if t.passed:
                passed_count += 1

    overall_pass_rate = None
    if total_questions > 0:
        overall_pass_rate = round(total_correct / total_questions * 100, 1)

    retest_count = tests.filter(attempt_no__gt=1).count()
    total_test_count = tests.count()
    completed_test_count = tests.filter(status="completed").count()

    # ── Achievements ──
    achievements = list(
        UserAchievement.objects.filter(user=student)
        .select_related("achievement")
        .order_by("-unlocked_at")
    )

    session_count = sessions.count()
    completed_session_count = completed_sessions.count()

    # ── Risk events ──
    from risk.models import RiskEvent

    risk_count = RiskEvent.objects.filter(user=student).count()

    # ── Summary ──
    summary = _build_summary(
        completed_session_count=completed_session_count,
        total_test_count=completed_test_count,
        passed_count=passed_count,
        overall_pass_rate=overall_pass_rate,
        mood_values=mood_values,
        skill_count=len(skill_counts),
        achievement_count=len(achievements),
        risk_count=risk_count,
        retest_count=retest_count,
    )

    return {
        "student": student,
        "profile": profile,
        "mood_history": mood_history,
        "mood_svg_chart": mood_svg,
        "skill_counts": skill_counts,
        "test_summary": test_summary,
        "achievements": achievements,
        "summary": summary,
        "session_count": session_count,
        "completed_session_count": completed_session_count,
        "total_test_count": total_test_count,
        "retest_count": retest_count,
        "overall_pass_rate": overall_pass_rate,
        "risk_count": risk_count,
        "passed_count": passed_count,
    }


def _get_profile(student):
    """Return profile dict, or empty defaults if no profile exists."""
    try:
        from questionnaire.models import UserProfile

        p = student.profile  # OneToOneField
        return {
            "gender": p.get_gender_display(),
            "age": p.age,
            "grade": p.get_grade_display(),
            "hobbies": p.hobby_tags if p.hobby_tags else [],
            "concerns": p.concern_tags if p.concern_tags else [],
            "other_hobby": p.other_hobby_text or "",
            "other_concern": p.other_concern_text or "",
        }
    except Exception:
        return {
            "gender": "",
            "age": None,
            "grade": "",
            "hobbies": [],
            "concerns": [],
            "other_hobby": "",
            "other_concern": "",
        }


def _build_summary(
    completed_session_count,
    total_test_count,
    passed_count,
    overall_pass_rate,
    mood_values,
    skill_count,
    achievement_count,
    risk_count,
    retest_count,
):
    """Build a human-readable summary text from aggregated data."""
    parts = []

    if completed_session_count == 0 and total_test_count == 0:
        parts.append(_("该学生暂未开始教学或测试。"))
        return "\n".join(parts)

    parts.append(_("该学生共完成 %(lessons)s 次教学会话，参与了 %(tests)s 次测试。") % {
        "lessons": completed_session_count, "tests": total_test_count,
    })

    if overall_pass_rate is not None:
        if overall_pass_rate >= 80:
            parts.append(_("测试总正确率为 %(rate)s%%，表现良好。") % {"rate": overall_pass_rate})
        elif overall_pass_rate >= 60:
            parts.append(_("测试总正确率为 %(rate)s%%，仍有提升空间。") % {"rate": overall_pass_rate})
        else:
            parts.append(_("测试总正确率为 %(rate)s%%，需要更多练习。") % {"rate": overall_pass_rate})

    if passed_count > 0 and total_test_count > 0:
        parts.append(_("%(passed)s/%(total)s 次测试通过。") % {"passed": passed_count, "total": total_test_count})

    if retest_count > 0:
        parts.append(_("共进行了 %(count)s 次重测，体现了持续学习的意愿。") % {"count": retest_count})

    if mood_values:
        first_half = mood_values[: len(mood_values) // 2] if len(mood_values) >= 2 else mood_values
        second_half = mood_values[len(mood_values) // 2 :] if len(mood_values) >= 2 else mood_values
        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)
        if second_avg > first_avg + 0.5:
            parts.append(_("情绪呈上升趋势，整体状态在改善。"))
        elif second_avg < first_avg - 0.5:
            parts.append(_("近期情绪有所下降，建议关注学生的心理状态。"))
        else:
            parts.append(_("情绪整体保持稳定。"))

    if skill_count > 0:
        parts.append(_("已学习 %(count)s 种技能。") % {"count": skill_count})

    if achievement_count > 0:
        parts.append(_("已解锁 %(count)s 个成就。") % {"count": achievement_count})

    if risk_count > 0:
        parts.append(_("历史风险事件 %(count)s 次，请结合风险事件记录进行综合评估。") % {"count": risk_count})
    else:
        parts.append(_("无风险事件记录。"))

    return "\n".join(parts)


def _render_mood_chart_svg(mood_values, width=560, height=120):
    """Render an inline SVG sparkline chart for mood values (1-5 scale)."""
    if not mood_values:
        return ""

    n = len(mood_values)
    if n == 1:
        # Single point: draw a dot
        x = width // 2
        y = _mood_y(mood_values[0], height)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img">'
            f'<text x="{width//2}" y="16" text-anchor="middle" font-size="10" fill="#6b7280">'
            f'{_("情绪记录（共1条）")}</text>'
            f'<circle cx="{x}" cy="{y}" r="5" fill="#6366f1"/>'
            f'<text x="{x}" y="{y - 10}" text-anchor="middle" font-size="10" fill="#6366f1">'
            f'{mood_values[0]}</text>'
            f'</svg>'
        )

    # Path for sparkline
    step_x = width / (n - 1) if n > 1 else width
    points = []
    for i, v in enumerate(mood_values):
        x = i * step_x
        y = _mood_y(v, height)
        points.append(f"{x:.1f},{y:.1f}")

    polyline = " ".join(points)

    # Y-axis guide lines
    guides = ""
    for level in range(1, 6):
        gy = _mood_y(level, height)
        guides += (
            f'<line x1="0" y1="{gy:.1f}" x2="{width}" y2="{gy:.1f}" '
            f'stroke="#e5e7eb" stroke-width="0.5"/>'
        )

    # Dots and labels on first/last and min/max
    dots = ""
    labels = ""
    min_idx = mood_values.index(min(mood_values))
    max_idx = mood_values.index(max(mood_values))
    for i in (0, min_idx, max_idx, n - 1):
        x = i * step_x
        y = _mood_y(mood_values[i], height)
        color = "#6366f1"
        if i == min_idx:
            color = "#ef4444"
        elif i == max_idx:
            color = "#22c55e"
        dots += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>'
        if i == min_idx or i == max_idx:
            labels += (
                f'<text x="{x:.1f}" y="{y - 8}" text-anchor="middle" '
                f'font-size="9" fill="{color}">{mood_values[i]}</text>'
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{_("情绪变化趋势图")}">'
        f'<text x="{width//2}" y="14" text-anchor="middle" font-size="10" fill="#6b7280">'
        f'{_("情绪变化趋势（共%(count)s条记录，1=很差 5=很好）") % {"count": n}}</text>'
        f'<g transform="translate(0,20)">{guides}'
        f'<polyline points="{polyline}" fill="none" stroke="#6366f1" stroke-width="2" stroke-linejoin="round"/>'
        f'{dots}{labels}</g>'
        f'</svg>'
    )


def _mood_y(value, height, padding=24):
    """Map mood value (1-5) to SVG y coordinate (inverted: 5=top)."""
    usable = height - padding * 2
    return padding + usable * (1 - (value - 1) / 4)
