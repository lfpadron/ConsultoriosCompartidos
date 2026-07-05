"""AstroTrace timeline URL patterns."""

from django.urls import path

from apps.astrotrace import views

urlpatterns = [
    path("timeline/", views.GlobalTimelineView.as_view(), name="timeline"),
    path(
        "timeline/eventos/<uuid:pk>/",
        views.TraceEventDetailView.as_view(),
        name="timeline_event_detail",
    ),
    path(
        "reservaciones/<uuid:pk>/timeline/",
        views.ReservationTimelineView.as_view(),
        name="reservation_timeline",
    ),
    path(
        "consultorios/<uuid:pk>/timeline/",
        views.ConsultingRoomTimelineView.as_view(),
        name="room_timeline",
    ),
    path(
        "propietarios/<uuid:pk>/timeline/",
        views.OwnerTimelineView.as_view(),
        name="owner_timeline",
    ),
    path(
        "medicos-arrendatarios/<uuid:pk>/timeline/",
        views.TenantDoctorTimelineView.as_view(),
        name="tenant_doctor_timeline",
    ),
    path(
        "pagos/<uuid:pk>/timeline/",
        views.PaymentTimelineView.as_view(),
        name="payment_timeline",
    ),
    path(
        "liquidaciones/<uuid:pk>/timeline/",
        views.SettlementTimelineView.as_view(),
        name="settlement_timeline",
    ),
    path(
        "documentos/<uuid:pk>/timeline/",
        views.DocumentTimelineView.as_view(),
        name="document_timeline",
    ),
]
