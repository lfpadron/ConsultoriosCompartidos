"""Presentation views."""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.finance.models import PaymentStatus
from apps.presentation.navigation import PAGE_TITLES
from apps.presentation.services.dashboard_service import (
    get_dashboard_alerts,
    get_dashboard_metrics,
    get_recent_trace_events,
)


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    context = {
        "page_title": "Dashboard",
        "metrics": get_dashboard_metrics(),
        "alerts": get_dashboard_alerts(),
        "recent_trace_events": get_recent_trace_events(limit=10),
        "quick_links": _quick_links(),
    }
    return render(request, "presentation/dashboard.html", context)


@require_GET
@login_required
def construction_page(request: HttpRequest, page_key: str) -> HttpResponse:
    page_title = PAGE_TITLES[page_key]
    return render(
        request,
        "presentation/under_construction.html",
        {"page_title": page_title},
    )


def _quick_links() -> list[dict[str, str]]:
    return [
        {
            "label": "Nueva clínica",
            "url": reverse("clinic_create"),
            "icon": "bi-hospital",
        },
        {
            "label": "Nuevo consultorio",
            "url": reverse("room_create"),
            "icon": "bi-door-open",
        },
        {
            "label": "Calendario",
            "url": reverse("calendar_week"),
            "icon": "bi-calendar3",
        },
        {
            "label": "Nueva tarifa",
            "url": reverse("rate_create"),
            "icon": "bi-cash-coin",
        },
        {
            "label": "Reservaciones",
            "url": reverse("reservations"),
            "icon": "bi-calendar-check",
        },
        {
            "label": "Pagos pendientes",
            "url": f"{reverse('payments')}?status={PaymentStatus.REGISTERED}",
            "icon": "bi-credit-card",
        },
        {
            "label": "Documentos",
            "url": reverse("documents"),
            "icon": "bi-file-earmark-pdf",
        },
        {
            "label": "Timeline",
            "url": reverse("timeline"),
            "icon": "bi-diagram-3",
        },
    ]
