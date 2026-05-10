from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, RegisterForm

User = get_user_model()


@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect("index")

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        invite = form.cleaned_data["invite_code"]
        user = form.save(commit=False)
        user.set_password(form.cleaned_data["password"])
        user.invite_code = invite.code
        user.role = User.Role.STUDENT
        try:
            user.save()
        except IntegrityError:
            form.add_error("username", "该用户名已被使用，请换一个")
            return render(request, "accounts/register.html", {"form": form})

        invite.status = invite.Status.USED
        invite.used_by = str(user.id)
        invite.used_at = timezone.now()
        invite.save()

        login(request, user)
        return redirect("questionnaire:profile")

    return render(request, "accounts/register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("index")

    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        next_url = request.GET.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url, allowed_hosts={request.get_host()}
        ):
            return redirect(next_url)
        return redirect("index")

    return render(request, "accounts/login.html", {"form": form})


@require_http_methods(["GET", "POST"])
def logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect("accounts:login")
    return render(request, "accounts/logout_confirm.html")
