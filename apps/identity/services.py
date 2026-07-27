"""Operational services for user invitations."""

from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Model
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from apps.astrotrace.services import record_event


def send_user_invitation(
    *,
    user: Any,
    actor: Model | None = None,
    request: HttpRequest | None = None,
    temporary_password: str = "",
) -> bool:
    """Send a first-access invitation email and store the send timestamp."""

    login_url = _build_login_url(request)
    password_text = (
        f"\nContraseña temporal: {temporary_password}\n"
        if temporary_password
        else "\nUsa la contraseña temporal que te proporcionó el administrador.\n"
    )
    message = (
        f"Hola {user.full_name or user.email},\n\n"
        "Se creó o actualizó tu acceso a Consultorios Compartidos.\n\n"
        f"Correo de acceso: {user.email}\n"
        f"{password_text}\n"
        f"Ingresa aquí: {login_url}\n\n"
        "Por seguridad, el sistema te pedirá cambiar tu contraseña al entrar.\n"
    )
    sent_count = send_mail(
        subject="Invitación a Consultorios Compartidos",
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
    user.invitation_sent_at = timezone.now()
    user.save(update_fields=["invitation_sent_at"])
    record_event(
        event_type="identity.user_invited",
        object_label=user.email,
        actor=actor,
        payload={"user_id": str(user.pk), "email_sent": bool(sent_count)},
    )
    return bool(sent_count)


def _build_login_url(request: HttpRequest | None) -> str:
    if request is not None:
        return request.build_absolute_uri(reverse("login"))
    base_url = settings.INVITATION_BASE_URL.rstrip("/")
    if base_url:
        return f"{base_url}{reverse('login')}"
    return reverse("login")
