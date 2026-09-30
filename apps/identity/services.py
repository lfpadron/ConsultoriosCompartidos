"""Operational services for identity, roles and invitations."""

from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Model
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.identity.models import UserRoleAssignment


@transaction.atomic
def sync_user_roles(
    *,
    user: Any,
    roles: set[str],
    actor: Model | None = None,
) -> None:
    """Make active role assignments match the requested set."""

    requested_roles = set(roles) | {user.role}
    existing = {
        assignment.role: assignment
        for assignment in UserRoleAssignment.objects.filter(user=user)
    }
    previous_roles = {
        role
        for role, assignment in existing.items()
        if assignment.is_active and not assignment.is_deleted
    }

    for role in requested_roles:
        assignment = existing.get(role)
        if assignment is None:
            assignment = UserRoleAssignment(user=user, role=role)
            if actor is not None:
                assignment.created_by = actor  # type: ignore[assignment]
                assignment.updated_by = actor  # type: ignore[assignment]
            assignment.save()
            continue
        update_fields = []
        if not assignment.is_active:
            assignment.is_active = True
            update_fields.append("is_active")
        if assignment.is_deleted:
            assignment.is_deleted = False
            update_fields.append("is_deleted")
        if actor is not None:
            assignment.updated_by = actor  # type: ignore[assignment]
            update_fields.append("updated_by")
        if update_fields:
            assignment.save(update_fields=[*update_fields, "updated_at"])

    for role, assignment in existing.items():
        if role in requested_roles or not assignment.is_active:
            continue
        assignment.is_active = False
        if actor is not None:
            assignment.updated_by = actor  # type: ignore[assignment]
        assignment.save(update_fields=["is_active", "updated_by", "updated_at"])

    if previous_roles != requested_roles:
        record_event(
            event_type="identity.user_roles.updated",
            object_label=user.email,
            actor=actor,
            payload={
                "model": user._meta.label,
                "id": str(user.pk),
                "level": "legal",
                "previous_roles": sorted(previous_roles),
                "roles": sorted(requested_roles),
            },
        )


def assign_user_role(
    *,
    user: Any,
    role: str,
    actor: Model | None = None,
) -> None:
    sync_user_roles(
        user=user,
        roles=user.get_role_values() | {role},
        actor=actor,
    )


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
