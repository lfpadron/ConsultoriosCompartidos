"""Integration URL patterns."""

from django.urls import path

from apps.integration import views

urlpatterns = [
    path(
        "integraciones/accesos/",
        views.AccessCredentialListView.as_view(),
        name="access_credentials",
    ),
    path(
        "integraciones/accesos/expirar/",
        views.AccessExpireView.as_view(),
        name="access_credentials_expire",
    ),
    path(
        "reservaciones/<uuid:reservation_pk>/acceso/habilitar/",
        views.AccessProvisionView.as_view(),
        name="access_provision",
    ),
    path(
        "accesos/<uuid:pk>/",
        views.AccessCredentialDetailView.as_view(),
        name="access_credential_detail",
    ),
    path(
        "accesos/<uuid:pk>/usar/",
        views.AccessUseView.as_view(),
        name="access_credential_use",
    ),
    path(
        "accesos/<uuid:pk>/revocar/",
        views.AccessRevokeView.as_view(),
        name="access_credential_revoke",
    ),
]
