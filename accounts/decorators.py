from functools import wraps

from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse


def role_required(allowed_roles, redirect_url=None):
    """Require user to have one of the allowed roles."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if request.user.role not in allowed_roles:
                if redirect_url:
                    return redirect(redirect_url)
                raise PermissionDenied("你没有权限访问此页面。")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def student_required(view_func=None, *, redirect_url=None):
    """Require user to have the 'student' role."""
    if view_func is not None:
        return role_required(["student"], redirect_url=redirect_url)(view_func)
    return role_required(["student"], redirect_url=redirect_url)


def admin_required(view_func=None, *, redirect_url=None):
    """Require user to have the 'admin' role."""
    if view_func is not None:
        return role_required(["admin"], redirect_url=redirect_url)(view_func)
    return role_required(["admin"], redirect_url=redirect_url)


def report_viewer_required(view_func=None, *, redirect_url=None):
    """Require user to have the 'report_viewer' role."""
    if view_func is not None:
        return role_required(["report_viewer"], redirect_url=redirect_url)(view_func)
    return role_required(["report_viewer"], redirect_url=redirect_url)
