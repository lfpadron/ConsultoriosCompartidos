"""Finance URL patterns."""

from django.urls import path

from apps.finance import views

urlpatterns = [
    path("tarifas/", views.RateRuleListView.as_view(), name="rates"),
    path("tarifas/nueva/", views.RateRuleCreateView.as_view(), name="rate_create"),
    path("tarifas/<uuid:pk>/", views.RateRuleDetailView.as_view(), name="rate_detail"),
    path(
        "tarifas/<uuid:pk>/editar/",
        views.RateRuleUpdateView.as_view(),
        name="rate_update",
    ),
    path(
        "tarifas/<uuid:pk>/desactivar/",
        views.RateRuleDeactivateView.as_view(),
        name="rate_deactivate",
    ),
    path(
        "descuentos-consultorio/",
        views.RoomRateDiscountListView.as_view(),
        name="room_rate_discounts",
    ),
    path(
        "descuentos-consultorio/nuevo/",
        views.RoomRateDiscountCreateView.as_view(),
        name="room_rate_discount_create",
    ),
    path(
        "descuentos-consultorio/<uuid:pk>/activar-desactivar/",
        views.RoomRateDiscountToggleView.as_view(),
        name="room_rate_discount_toggle",
    ),
    path(
        "descuentos-arrendatario/",
        views.TenantDoctorDiscountListView.as_view(),
        name="tenant_doctor_discounts",
    ),
    path(
        "descuentos-arrendatario/nuevo/",
        views.TenantDoctorDiscountCreateView.as_view(),
        name="tenant_doctor_discount_create",
    ),
    path(
        "descuentos-arrendatario/<uuid:pk>/activar-desactivar/",
        views.TenantDoctorDiscountToggleView.as_view(),
        name="tenant_doctor_discount_toggle",
    ),
    path("pagos/", views.PaymentListView.as_view(), name="payments"),
    path(
        "reservaciones/grupos/<uuid:batch_pk>/comprobante/",
        views.BatchPaymentSubmitView.as_view(),
        name="payment_batch_submit",
    ),
    path(
        "reservaciones/<uuid:reservation_pk>/pagos/nuevo/",
        views.PaymentRegisterView.as_view(),
        name="payment_register",
    ),
    path("pagos/<uuid:pk>/", views.PaymentDetailView.as_view(), name="payment_detail"),
    path(
        "pagos/<uuid:pk>/validar/",
        views.PaymentValidateView.as_view(),
        name="payment_validate",
    ),
    path(
        "pagos/<uuid:pk>/rechazar/",
        views.PaymentRejectView.as_view(),
        name="payment_reject",
    ),
    path(
        "pagos/<uuid:pk>/cancelar/",
        views.PaymentCancelView.as_view(),
        name="payment_cancel",
    ),
    path("liquidaciones/", views.SettlementListView.as_view(), name="settlements"),
    path(
        "reservaciones/<uuid:reservation_pk>/liquidaciones/generar/",
        views.SettlementGenerateView.as_view(),
        name="settlement_generate",
    ),
    path(
        "liquidaciones/<uuid:pk>/",
        views.SettlementDetailView.as_view(),
        name="settlement_detail",
    ),
    path(
        "liquidaciones/<uuid:pk>/pagar/",
        views.SettlementPaidView.as_view(),
        name="settlement_paid",
    ),
    path(
        "liquidaciones/<uuid:pk>/cancelar/",
        views.SettlementCancelView.as_view(),
        name="settlement_cancel",
    ),
]
