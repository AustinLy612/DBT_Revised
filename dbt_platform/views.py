from django.shortcuts import render


def index_view(request):
    """Home page — role-aware, prompts profile completion before teaching."""
    return render(request, "index.html")
