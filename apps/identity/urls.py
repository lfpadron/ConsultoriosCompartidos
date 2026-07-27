"""Identity URL patterns."""

from django.urls import path

from apps.identity import views

urlpatterns = [
    path("usuarios/", views.UserListView.as_view(), name="users"),
    path("usuarios/nuevo/", views.UserCreateView.as_view(), name="user_create"),
    path("usuarios/<uuid:pk>/", views.UserDetailView.as_view(), name="user_detail"),
    path(
        "usuarios/<uuid:pk>/editar/",
        views.UserUpdateView.as_view(),
        name="user_update",
    ),
    path(
        "usuarios/<uuid:pk>/desactivar/",
        views.UserDeactivateView.as_view(),
        name="user_deactivate",
    ),
    path(
        "usuarios/<uuid:pk>/reenviar-invitacion/",
        views.UserSendInvitationView.as_view(),
        name="user_send_invitation",
    ),
    path(
        "cambiar-contrasena/",
        views.ForcedPasswordChangeView.as_view(),
        name="password_change_required",
    ),
]
