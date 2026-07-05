"""Simulated access control workflow."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model, QuerySet
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.integration.models import (
    ACTIVE_ACCESS_CREDENTIAL_STATUSES,
    AccessCredential,
    AccessCredentialStatus,
)
from apps.scheduling.models import Reservation, ReservationStatus

ACCESS_WINDOW_PADDING_MINUTES = 15


@dataclass(frozen=True)
class ReservationAccessStatus:
    reservation: Reservation
    credential: AccessCredential | None
    status_label: str
    can_provision: bool


@transaction.atomic
def provision_access_for_reservation(
    reservation: Reservation,
    user: Model | None = None,
) -> AccessCredential:
    if reservation.status != ReservationStatus.CONFIRMED:
        raise ValidationError(
            {
                "reservation": (
                    "Sólo se puede habilitar acceso para una reservación confirmada."
                )
            }
        )
    if _active_credentials_for_reservation(reservation).exists():
        raise ValidationError(
            {"reservation": "La reservación ya tiene una credencial activa."}
        )

    valid_from, valid_until = _validity_window_for_reservation(reservation)
    credential = AccessCredential(
        reservation=reservation,
        tenant_doctor=reservation.tenant_doctor,
        room=reservation.room,
        status=AccessCredentialStatus.ENABLED,
        simulated_code=_generate_access_code(reservation),
        valid_from=valid_from,
        valid_until=valid_until,
        enabled_at=timezone.now(),
    )
    if user is not None:
        credential.created_by = cast(Any, user)
        credential.updated_by = cast(Any, user)
    credential.save()

    record_event(
        event_type="access.provisioned",
        object_label=str(credential),
        actor=user,
        payload=_credential_payload(credential, user=user),
    )
    return credential


@transaction.atomic
def simulate_access_use(
    credential: AccessCredential,
    user: Model | None = None,
) -> AccessCredential:
    now = timezone.now()
    if credential.status in {
        AccessCredentialStatus.REVOKED,
        AccessCredentialStatus.EXPIRED,
    }:
        raise ValidationError({"status": "La credencial no está vigente."})
    if credential.status != AccessCredentialStatus.ENABLED:
        raise ValidationError(
            {"status": "Sólo se puede usar una credencial habilitada."}
        )
    if now < credential.valid_from or now > credential.valid_until:
        raise ValidationError(
            {"valid_from": "La credencial está fuera de la ventana de validez."}
        )

    credential.status = AccessCredentialStatus.USED
    credential.used_at = now
    if user is not None:
        credential.updated_by = cast(Any, user)
    credential.save()

    record_event(
        event_type="access.used",
        object_label=str(credential),
        actor=user,
        payload=_credential_payload(credential, user=user),
    )
    return credential


@transaction.atomic
def revoke_access_credential(
    credential: AccessCredential,
    user: Model | None = None,
    reason: str = "",
) -> AccessCredential:
    if not reason.strip():
        raise ValidationError({"reason": "El motivo de revocación es obligatorio."})

    credential.status = AccessCredentialStatus.REVOKED
    credential.revoked_at = timezone.now()
    credential.notes = _append_note(credential.notes, f"Revocada: {reason.strip()}")
    if user is not None:
        credential.updated_by = cast(Any, user)
    credential.save()

    record_event(
        event_type="access.revoked",
        object_label=str(credential),
        actor=user,
        payload={
            **_credential_payload(credential, user=user),
            "reason": reason.strip(),
        },
    )
    return credential


@transaction.atomic
def expire_old_credentials(now: datetime | None = None) -> int:
    current_time = now or timezone.now()
    credentials = AccessCredential.objects.filter(
        status__in=ACTIVE_ACCESS_CREDENTIAL_STATUSES,
        valid_until__lt=current_time,
        is_deleted=False,
    ).select_related("reservation", "tenant_doctor", "room")
    expired_count = 0
    for credential in credentials:
        credential.status = AccessCredentialStatus.EXPIRED
        credential.expired_at = current_time
        credential.save(update_fields=["status", "expired_at", "updated_at"])
        record_event(
            event_type="access.expired",
            object_label=str(credential),
            payload=_credential_payload(credential, user=None),
        )
        expired_count += 1
    return expired_count


def get_access_status_for_reservation(
    reservation: Reservation,
) -> ReservationAccessStatus:
    credential = (
        AccessCredential.objects.filter(reservation=reservation, is_deleted=False)
        .select_related("reservation", "tenant_doctor", "room")
        .order_by("-created_at")
        .first()
    )
    active_exists = _active_credentials_for_reservation(reservation).exists()
    can_provision = (
        reservation.status == ReservationStatus.CONFIRMED and not active_exists
    )
    status_label = (
        credential.get_status_display() if credential is not None else "Sin credencial"
    )
    return ReservationAccessStatus(
        reservation=reservation,
        credential=credential,
        status_label=status_label,
        can_provision=can_provision,
    )


def _active_credentials_for_reservation(
    reservation: Reservation,
) -> QuerySet[AccessCredential]:
    return AccessCredential.objects.filter(
        reservation=reservation,
        status__in=ACTIVE_ACCESS_CREDENTIAL_STATUSES,
        is_deleted=False,
    )


def _validity_window_for_reservation(
    reservation: Reservation,
) -> tuple[datetime, datetime]:
    current_timezone = timezone.get_current_timezone()
    starts_at = timezone.make_aware(
        datetime.combine(reservation.date, reservation.start_time),
        current_timezone,
    )
    ends_at = timezone.make_aware(
        datetime.combine(reservation.date, reservation.end_time),
        current_timezone,
    )
    padding = timedelta(minutes=ACCESS_WINDOW_PADDING_MINUTES)
    return starts_at - padding, ends_at + padding


def _generate_access_code(reservation: Reservation) -> str:
    date_fragment = reservation.date.strftime("%Y%m%d")
    for _ in range(10):
        candidate = f"ACC-{date_fragment}-{secrets.token_hex(3).upper()}"
        if not AccessCredential.objects.filter(simulated_code=candidate).exists():
            return candidate
    raise ValidationError({"simulated_code": "No se pudo generar un código único."})


def _append_note(notes: str, extra_note: str) -> str:
    if not notes:
        return extra_note
    return f"{notes}\n{extra_note}"


def _credential_payload(
    credential: AccessCredential,
    *,
    user: Model | None,
) -> dict[str, str]:
    return {
        "model": credential._meta.label,
        "id": str(credential.pk),
        "level": "operativo",
        "reservation_id": str(credential.reservation_id),
        "reservation": str(credential.reservation),
        "tenant_doctor_id": str(credential.tenant_doctor_id),
        "tenant_doctor": str(credential.tenant_doctor),
        "room_id": str(credential.room_id),
        "room": str(credential.room),
        "status": credential.status,
        "simulated_code": credential.simulated_code,
        "valid_from": credential.valid_from.isoformat(),
        "valid_until": credential.valid_until.isoformat(),
        "actor_id": str(user.pk) if user is not None else "",
    }
