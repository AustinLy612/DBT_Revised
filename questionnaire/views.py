from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import ProfileForm


@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request):
    """Registration questionnaire — mandatory on first login, modifiable later.

    GET: show form pre-filled with existing profile data (if any).
    POST: create or update profile, mark user profile_completed, redirect to index.
    """
    profile = getattr(request.user, "profile", None)
    is_first_time = not request.user.profile_completed

    if request.method == "POST":
        form = ProfileForm(request.POST, instance=profile)
        # Preserve "other" textarea visibility when validation fails
        post_hobby = request.POST.getlist("hobby_tags", [])
        post_concern = request.POST.getlist("concern_tags", [])
        hobby_has_other = "其他" in post_hobby
        concern_has_other = "其他" in post_concern

        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()

            if not request.user.profile_completed:
                request.user.profile_completed = True
                request.user.save(update_fields=["profile_completed"])

            return redirect("index")
    else:
        form = ProfileForm(instance=profile)
        existing_hobby = profile.hobby_tags if profile else []
        existing_concern = profile.concern_tags if profile else []
        hobby_has_other = "其他" in existing_hobby
        concern_has_other = "其他" in existing_concern

    return render(
        request,
        "questionnaire/profile.html",
        {
            "form": form,
            "is_first_time": is_first_time,
            "hobby_has_other": hobby_has_other,
            "concern_has_other": concern_has_other,
        },
    )
