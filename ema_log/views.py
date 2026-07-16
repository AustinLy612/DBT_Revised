import datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from questionnaire.decorators import profile_required

from .forms import EMAForm
from .models import EMASubmission


@profile_required
def ema_log_view(request):
    """EMA daily log — GET shows form with daily counter, POST saves submission."""
    today = timezone.now().date()
    today_count = EMASubmission.objects.filter(
        user=request.user,
        created_at__gte=today,
        created_at__lt=today + datetime.timedelta(days=1),
    ).count()

    if request.method == "POST":
        form = EMAForm(request.POST)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.user = request.user
            submission.save()
            form.save_m2m()
            messages.success(request, "EMA日志已成功提交！")
            return redirect("ema_log:log")
    else:
        form = EMAForm()

    return render(
        request,
        "ema_log/ema_form.html",
        {
            "form": form,
            "today_count": today_count,
            "display_count": today_count + 1,
        },
    )
