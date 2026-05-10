from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def profile_required(view_func):
    """Decorator that redirects to questionnaire if profile is not completed.

    Must be placed AFTER login_required so request.user is available.
    """
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.profile_completed:
            return redirect("questionnaire:profile")
        return view_func(request, *args, **kwargs)
    return wrapper
