"""Presentation URL patterns."""

from django.urls import include, path

from apps.presentation import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/", views.dashboard, name="dashboard_page"),
    path("", include("apps.catalog.urls")),
    path("", include("apps.scheduling.urls")),
    path("", include("apps.finance.urls")),
    path("", include("apps.vault.urls")),
    path("", include("apps.astrotrace.urls")),
    path("", include("apps.integration.urls")),
    path("", include("apps.reports.urls")),
    path(
        "estados-de-cuenta/",
        views.construction_page,
        {"page_key": "statements"},
        name="statements",
    ),
    path(
        "administracion/",
        views.construction_page,
        {"page_key": "administration"},
        name="administration",
    ),
]
