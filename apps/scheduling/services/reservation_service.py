"""Reservation workflow services."""

from datetime import date, time
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Model
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.catalog.models import ConsultingRoom, TenantDoctorProfile, TenantDoctorStatus
from apps.finance.models import PriceType, StatementStatus
from apps.finance.services.pricing_engine import (
    PricingConfigurationError,
    calculate_block_price,
)
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
    _validate_tenant_doctor_room_assignment(tenant_doctor, room)
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
    _send_reservation_confirmation_email(reservation)
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


def _validate_tenant_doctor_room_assignment(
    tenant_doctor: TenantDoctorProfile,
    room: ConsultingRoom,
) -> None:
    assigned_rooms = tenant_doctor.assigned_rooms.filter(is_deleted=False)
    if assigned_rooms.exists() and not assigned_rooms.filter(pk=room.pk).exists():
        raise ValidationError(
            {"room": "El médico arrendatario no está asignado a este consultorio."}
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
            and block.status == BLOCK_STATUS_FREE
            and block.start_time <= start_time
            and end_time <= block.end_time
        ),
        None,
    )
    if matching_block is None:
        raise ValidationError(
            {"start_time": "El horario solicitado no está libre en la disponibilidad."}
        )
    try:
        pricing = calculate_block_price(
            consulting_room=room,
            date=reservation_date,
            start_time=start_time,
            end_time=end_time,
        )
    except PricingConfigurationError as exc:
        raise ValidationError({"start_time": str(exc)}) from exc

    if pricing.applied_rule is None:
        raise ValidationError(
            {"start_time": "No hay tarifa configurada para el horario solicitado."}
        )
    if pricing.price_type == PriceType.BLOCK and (
        matching_block.start_time != start_time or matching_block.end_time != end_time
    ):
        raise ValidationError(
            {"start_time": "La tarifa por bloque requiere reservar el bloque completo."}
        )


def _send_reservation_confirmation_email(reservation: Reservation) -> None:
    owner = reservation.room.owner
    recipients = [
        reservation.tenant_doctor.user.email,
        owner.user.email if owner else "",
    ]
    recipient_list = list(dict.fromkeys(email for email in recipients if email))
    if not recipient_list:
        return

    room_label = reservation.room.number.strip() or reservation.room.name
    message = (
        f"Estimado Dr. {reservation.tenant_doctor}, su reservación ha quedado "
        f"registrada para el día {reservation.date:%d/%m/%Y} de las "
        f"{reservation.start_time:%H:%M} hasta las {reservation.end_time:%H:%M}"
    )
    send_mail(
        subject=f"Consultorio {room_label}, reservación confirmada",
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
    )


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
