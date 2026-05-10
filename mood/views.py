"""Mood tracking and achievement views.

Views:
  - mood_home_view — mood history + manual record entry + quick access
  - record_mood_view — standalone manual mood recording
  - record_post_teaching_mood_view — post-teaching mood (from teaching completed flow)
  - record_post_test_mood_view — post-test mood (from test completed flow)
  - achievements_view — achievement grid (locked + unlocked)
  - mood_stats_view — JSON stats for future reports
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import services
from questionnaire.decorators import profile_required
from teaching.models import TeachingSession

logger = logging.getLogger("dbt_platform.mood")


# ═══════════════════════════════════════════════════════════════
# Mood home — history + quick record
# ═══════════════════════════════════════════════════════════════

@profile_required
def mood_home_view(request: HttpRequest) -> HttpResponse:
    """Display mood history and manual recording button."""
    mood_history = services.get_mood_history(request.user, limit=50)
    achievements = services.get_user_achievements(request.user)

    return render(request, "mood/home.html", {
        "mood_history": mood_history,
        "achievements": achievements,
    })


# ═══════════════════════════════════════════════════════════════
# Manual mood recording
# ═══════════════════════════════════════════════════════════════

@profile_required
def record_mood_view(request: HttpRequest) -> HttpResponse:
    """Standalone manual mood recording page."""
    if request.method == "GET":
        return render(request, "mood/record.html", {
            "context_label": "手动记录",
        })

    try:
        mood_value = int(request.POST.get("mood_value", 3))
    except (ValueError, TypeError):
        mood_value = 3
    mood_value = max(1, min(5, mood_value))
    note = request.POST.get("note", "").strip()

    services.record_mood(request.user, mood_value, "manual", note=note)
    messages.success(request, "心情已记录！")
    return redirect("mood:home")


# ═══════════════════════════════════════════════════════════════
# Post-teaching mood recording
# ═══════════════════════════════════════════════════════════════

@profile_required
def record_post_teaching_mood_view(request: HttpRequest, session_id: str) -> HttpResponse:
    """Record post-teaching mood. Serves as a modal-like page.

    Redirected to automatically after session completion.
    User can record mood or skip.
    """
    session = get_object_or_404(TeachingSession, session_id=session_id, user=request.user)

    if session.status != TeachingSession.Status.COMPLETED:
        messages.warning(request, "教学未完成，无法记录教学后心情。")
        return redirect("teaching:session", session_id=session_id)

    if session.post_mood_id:
        messages.info(request, "已记录过教学后心情。")
        return redirect("teaching:session", session_id=session_id)

    if request.method == "GET":
        return render(request, "mood/post_mood.html", {
            "session": session,
            "context_type": "after_teaching",
            "context_label": "教学后心情记录",
            "back_url": reverse("teaching:session", kwargs={"session_id": session_id}),
        })

    try:
        mood_value = int(request.POST.get("mood_value", 3))
    except (ValueError, TypeError):
        mood_value = 3
    mood_value = max(1, min(5, mood_value))
    note = request.POST.get("note", "").strip()

    services.record_post_teaching_mood(session, request.user, mood_value, note)
    messages.success(request, "教学后心情已记录！")
    return redirect("teaching:session", session_id=session_id)


# ═══════════════════════════════════════════════════════════════
# Post-test mood recording
# ═══════════════════════════════════════════════════════════════

@profile_required
def record_post_test_mood_view(request: HttpRequest, test_id: str) -> HttpResponse:
    """Record post-test mood. Serves as a modal-like page.

    Redirected to automatically after test completion.
    User can record mood or skip.
    """
    from testing.models import Test

    test = get_object_or_404(Test, test_id=test_id, user=request.user)

    if test.status != Test.Status.COMPLETED:
        messages.warning(request, "测试未完成，无法记录测试后心情。")
        return redirect("testing:test", test_id=test_id)

    if test.post_mood_id:
        messages.info(request, "已记录过测试后心情。")
        return redirect("testing:test", test_id=test_id)

    if request.method == "GET":
        return render(request, "mood/post_mood.html", {
            "test": test,
            "context_type": "after_testing",
            "context_label": "测试后心情记录",
            "back_url": reverse("testing:test", kwargs={"test_id": test_id}),
        })

    try:
        mood_value = int(request.POST.get("mood_value", 3))
    except (ValueError, TypeError):
        mood_value = 3
    mood_value = max(1, min(5, mood_value))
    note = request.POST.get("note", "").strip()

    services.record_post_test_mood(test, request.user, mood_value, note)
    messages.success(request, "测试后心情已记录！")
    return redirect("testing:test", test_id=test_id)


# ═══════════════════════════════════════════════════════════════
# Achievement page
# ═══════════════════════════════════════════════════════════════

@profile_required
def achievements_view(request: HttpRequest) -> HttpResponse:
    """Display all achievements (unlocked + locked) with stats."""
    data = services.get_user_achievements(request.user)

    return render(request, "mood/achievements.html", {
        "achievements": data["achievements"],
        "unlocked_count": data["unlocked_count"],
        "total_count": data["total_count"],
        "total_trainings": data["total_trainings"],
        "successful_trainings": data["successful_trainings"],
        "consecutive_learning_days": data["consecutive_learning_days"],
        "username": request.user.username,
    })


# ═══════════════════════════════════════════════════════════════
# Stats API (for future reports — Step 12)
# ═══════════════════════════════════════════════════════════════

@login_required
def mood_stats_view(request: HttpRequest) -> JsonResponse:
    """Return aggregated stats for reports. Admin/staff only."""
    if not request.user.is_staff:
        return JsonResponse({"error": "Forbidden"}, status=403)

    user_id = request.GET.get("user_id", "").strip()
    if user_id:
        from accounts.models import User
        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=404)
    else:
        target_user = request.user

    stats = services.get_stats_for_reports(target_user)
    return JsonResponse(stats)
