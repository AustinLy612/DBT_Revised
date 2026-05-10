"""Risk views — risk popup display."""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@login_required
def risk_popup_view(request: HttpRequest) -> HttpResponse:
    """Render the risk popup with hotline information.

    This is shown when a high-risk message is detected during teaching
    or testing.  The popup displays the PRD-specified hotline numbers
    and guidance text.
    """
    return render(request, "risk/popup.html")
