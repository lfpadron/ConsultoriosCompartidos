"""Scheduling URL patterns."""

from django.urls import path

from apps.scheduling import views

urlpatterns = [
    path(
        "disponibilidad/",
        views.AvailabilityRuleListView.as_view(),
        name="availability",
    ),
    path(
        "disponibilidad/reglas/nueva/",
        views.AvailabilityRuleCreateView.as_view(),
        name="availability_rule_create",
    ),
    path(
        "disponibilidad/reglas/<uuid:pk>/",
        views.AvailabilityRuleDetailView.as_view(),
        name="availability_rule_detail",
    ),
    path(
        "disponibilidad/reglas/<uuid:pk>/editar/",
        views.AvailabilityRuleUpdateView.as_view(),
        name="availability_rule_update",
    ),
    path(
        "disponibilidad/reglas/<uuid:pk>/desactivar/",
        views.AvailabilityRuleDeactivateView.as_view(),
        name="availability_rule_deactivate",
    ),
    path(
        "disponibilidad/excepciones/",
        views.AvailabilityExceptionListView.as_view(),
        name="availability_exceptions",
    ),
    path(
        "disponibilidad/excepciones/nueva/",
        views.AvailabilityExceptionCreateView.as_view(),
        name="availability_exception_create",
    ),
    path(
        "disponibilidad/excepciones/<uuid:pk>/",
        views.AvailabilityExceptionDetailView.as_view(),
        name="availability_exception_detail",
    ),
    path(
        "disponibilidad/excepciones/<uuid:pk>/editar/",
        views.AvailabilityExceptionUpdateView.as_view(),
        name="availability_exception_update",
    ),
    path(
        "disponibilidad/excepciones/<uuid:pk>/desactivar/",
        views.AvailabilityExceptionDeactivateView.as_view(),
        name="availability_exception_deactivate",
    ),
    path("calendario/", views.WeeklyCalendarView.as_view(), name="calendar_week"),
    path(
        "calendario/vista-rapida/",
        views.QuickCalendarView.as_view(),
        name="calendar_quick",
    ),
    path("reservaciones/", views.ReservationListView.as_view(), name="reservations"),
    path(
        "reservaciones/solicitar/",
        views.ReservationRequestView.as_view(),
        name="reservation_request",
    ),
    path(
        "reservaciones/<uuid:pk>/",
        views.ReservationDetailView.as_view(),
        name="reservation_detail",
    ),
    path(
        "reservaciones/<uuid:pk>/cancelar/",
        views.ReservationCancelView.as_view(),
        name="reservation_cancel",
    ),
    path(
        "reservaciones/<uuid:pk>/confirmar/",
        views.ReservationConfirmView.as_view(),
        name="reservation_confirm",
    ),
]
