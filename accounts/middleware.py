from django.shortcuts import redirect


class AdminAccessMiddleware:
    """Block non-admin users from accessing /admin/ URLs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/") and request.user.is_authenticated:
            if request.user.role != "admin":
                return redirect("index")
        return self.get_response(request)
