"""Reservation workflow services."""

from datetime import date, time
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.catalog.models import ConsultingRoom, TenantDoctorProfile, TenantDoctorStatus
from apps.finance.models import StatementStatus
from apps.finance.services.statement_engine import generate_statement_for_reservation
from apps.scheduling.models import Reservation, ReservationStatus
from apps.scheduling.services import BLOCK_STATUS_FREE, generate_availability_blocks


@transaction.atomic
def create_reservation(
    *,
    room: ConsultingRoom,
    tenant_doctor: TenantDoctorProfile,
    reservation_date: date,
    start_time: time,
    end_time: time,
    notes: str = "",
    actor: Model | None = None,
) -> Reservation:
    _validate_tenant_doctor_is_authorized(tenant_doctor)
    _validate_available_block(room, reservation_date, start_time, end_time)

    reservation = Reservation(
        room=room,
        tenant_doctor=tenant_doctor,
        date=reservation_date,
        start_time=start_time,
        end_time=end_time,
        status=ReservationStatus.REQUESTED,
        notes=notes,
    )
    if actor is not None:
        reservation.created_by = cast(Any, actor)
        reservation.updated_by = cast(Any, actor)
    reservation.save()

    statement = generate_statement_for_reservation(reservation)
    record_event(
        event_type="reservation.requested",
        object_label=str(reservation),
        actor=actor,
        payload=_reservation_payload(reservation, level="operativo"),
    )
    record_event(
        event_type="statement.generated",
        object_label=str(statement),
        actor=actor,
        payload={
            "model": statement._meta.label,
            "id": str(statement.pk),
            "reservation_id": str(reservation.pk),
            "level": "financiero",
            "hash": statement.calculation_hash,
        },
    )
    return reservation


@transaction.atomic
def cancel_reservation(
    *,
    reservation: Reservation,
    reason: str,
    actor: Model | None = None,
) -> Reservation:
    reservation.status = ReservationStatus.CANCELLED
    reservation.cancel_reason = reason
    reservation.cancelled_at = timezone.now()
    if actor is not None:
        reservation.updated_by = cast(Any, actor)
    reservation.save()

    reservation.statements.filter(status=StatementStatus.CURRENT).update(
        status=StatementStatus.CANCELLED,
        updated_at=timezone.now(),
    )
    record_event(
        event_type="reservation.cancelled",
        object_label=str(reservation),
        actor=actor,
        payload={
            **_reservation_payload(reservation, level="legal_operativo"),
            "reason": reason,
        },
    )
    return reservation


@transaction.atomic
def confirm_reservation(
    *,
    reservation: Reservation,
    actor: Model | None = None,
) -> Reservation:
    reservation.status = ReservationStatus.CONFIRMED
    reservation.confirmed_at = timezone.now()
    if actor is not None:
        reservation.updated_by = cast(Any, actor)
    reservation.save()
    record_event(
        event_type="reservation.confirmed",
        object_label=str(reservation),
        actor=actor,
        payload=_reservation_payload(reservation, level="operativo"),
    )
    return reservation


def _validate_tenant_doctor_is_authorized(
    tenant_doctor: TenantDoctorProfile,
) -> None:
    if tenant_doctor.status != TenantDoctorStatus.AUTHORIZED:
        raise ValidationError(
            {"tenant_doctor": "El médico arrendatario debe estar autorizado."}
        )


def _validate_available_block(
    room: ConsultingRoom,
    reservation_date: date,
    start_time: time,
    end_time: time,
) -> None:
    if start_time >= end_time:
        raise ValidationError({"end_time": "La hora fin debe ser mayor."})

    blocks = generate_availability_blocks(room, reservation_date, reservation_date)
    matching_block = next(
        (
            block
            for block in blocks
            if block.date == reservation_date
            and block.start_time == start_time
            and block.end_time == end_time
        ),
        None,
    )
    if matching_block is None:
        raise ValidationError(
            {"start_time": "El bloque solicitado no existe en la disponibilidad."}
        )
    if matching_block.status != BLOCK_STATUS_FREE:
        raise ValidationError({"start_time": "El bloque solicitado no está libre."})


def _reservation_payload(reservation: Reservation, *, level: str) -> dict[str, str]:
    return {
        "model": reservation._meta.label,
        "id": str(reservation.pk),
        "level": level,
        "room": str(reservation.room),
        "tenant_doctor": str(reservation.tenant_doctor),
        "date": reservation.date.isoformat(),
        "start_time": reservation.start_time.isoformat(),
        "end_time": reservation.end_time.isoformat(),
        "status": reservation.status,
    }
