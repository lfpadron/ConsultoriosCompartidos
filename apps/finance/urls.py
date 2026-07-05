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
    path("pagos/", views.PaymentListView.as_view(), name="payments"),
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
