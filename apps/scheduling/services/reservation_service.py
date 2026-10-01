"""Reservation workflow services."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Model, Q
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.billing.models import CancellationPolicy
from apps.catalog.models import ConsultingRoom, TenantDoctorProfile, TenantDoctorStatus
from apps.finance.models import PriceType, StatementStatus
from apps.finance.services.discount_service import calculate_discount_quote
from apps.finance.services.pricing_engine import (
    BlockPrice,
    PricingConfigurationError,
    calculate_block_price,
)
from apps.finance.services.statement_engine import generate_statement_for_reservation
from apps.scheduling.models import (
    Reservation,
    ReservationBatch,
    ReservationBatchStatus,
    ReservationBatchType,
    ReservationDeadlinePolicy,
    ReservationStatus,
)
from apps.scheduling.services import BLOCK_STATUS_FREE, generate_availability_blocks
from apps.scheduling.services.deadline_service import (
    calculate_payment_deadline,
    persist_deadline_exception,
)

MAX_RECURRING_OCCURRENCES = 53


@dataclass(frozen=True)
class ReservationOccurrencePreview:
    reservation_date: date
    start_time: time
    end_time: time
    tariff_total: Decimal | None
    tariff_final: Decimal | None
    currency: str
    conflict: bool
    message: str = ""


@dataclass(frozen=True)
class ReservationBatchPreview:
    occurrences: tuple[ReservationOccurrencePreview, ...]
    tariff_total: Decimal
    tariff_final: Decimal
    currency: str

    @property
    def has_conflicts(self) -> bool:
        return any(occurrence.conflict for occurrence in self.occurrences)


def generate_weekly_occurrence_dates(
    *,
    start_date: date,
    end_date: date | None,
) -> list[date]:
    if end_date is None or end_date == start_date:
        return [start_date]
    if end_date < start_date:
        raise ValidationError(
            {
                "recurrence_end_date": (
                    "La fecha fin no puede ser menor que la fecha inicial."
                )
            }
        )
    if end_date > start_date + timedelta(days=366):
        raise ValidationError(
            {"recurrence_end_date": "La recurrencia no puede exceder un año."}
        )

    dates: list[date] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        if len(dates) > MAX_RECURRING_OCCURRENCES:
            raise ValidationError(
                {
                    "recurrence_end_date": (
                        "La recurrencia no puede exceder 53 ocurrencias."
                    )
                }
            )
        current += timedelta(days=7)
    return dates


def preview_reservation_batch(
    *,
    room: ConsultingRoom,
    tenant_doctor: TenantDoctorProfile,
    start_date: date,
    start_time: time,
    end_time: time,
    recurrence_end_date: date | None = None,
) -> ReservationBatchPreview:
    _validate_tenant_doctor_is_authorized(tenant_doctor)
    _validate_tenant_doctor_room_assignment(tenant_doctor, room)

    dates = generate_weekly_occurrence_dates(
        start_date=start_date,
        end_date=recurrence_end_date,
    )
    occurrences: list[ReservationOccurrencePreview] = []
    tariff_total = Decimal("0.00")
    tariff_final = Decimal("0.00")
    batch_currency = ""
    for reservation_date in dates:
        try:
            pricing = _validate_available_block(
                room,
                reservation_date,
                start_time,
                end_time,
            )
            if pricing.applied_rule is None or pricing.subtotal is None:
                raise ValidationError(
                    "No hay tarifa configurada para el horario solicitado."
                )
            quote = calculate_discount_quote(
                room=room,
                tenant_doctor=tenant_doctor,
                rate_rule=pricing.applied_rule,
                reservation_date=reservation_date,
                tariff_total=pricing.subtotal,
            )
            if batch_currency and batch_currency != pricing.currency:
                raise ValidationError(
                    "Todas las ocurrencias del grupo deben usar la misma moneda."
                )
            batch_currency = pricing.currency
            tariff_total += pricing.subtotal
            tariff_final += quote.tariff_final
            occurrences.append(
                ReservationOccurrencePreview(
                    reservation_date=reservation_date,
                    start_time=start_time,
                    end_time=end_time,
                    tariff_total=pricing.subtotal,
                    tariff_final=quote.tariff_final,
                    currency=pricing.currency,
                    conflict=False,
                )
            )
        except (ValidationError, PricingConfigurationError) as exc:
            occurrences.append(
                ReservationOccurrencePreview(
                    reservation_date=reservation_date,
                    start_time=start_time,
                    end_time=end_time,
                    tariff_total=None,
                    tariff_final=None,
                    currency=batch_currency or "MXN",
                    conflict=True,
                    message=_validation_error_text(exc),
                )
            )

    return ReservationBatchPreview(
        occurrences=tuple(occurrences),
        tariff_total=tariff_total,
        tariff_final=tariff_final,
        currency=batch_currency or "MXN",
    )


@transaction.atomic
def create_reservation_batch(
    *,
    room: ConsultingRoom,
    tenant_doctor: TenantDoctorProfile,
    start_date: date,
    start_time: time,
    end_time: time,
    recurrence_end_date: date | None = None,
    notes: str = "",
    actor: Model | None = None,
    requested_at: datetime | None = None,
    deadline_exception_reason: str = "",
) -> ReservationBatch:
    request_timestamp = requested_at or timezone.now()
    locked_room = (
        ConsultingRoom.objects.select_for_update()
        .select_related("clinic", "owner", "owner__user")
        .get(pk=room.pk)
    )
    preview = preview_reservation_batch(
        room=locked_room,
        tenant_doctor=tenant_doctor,
        start_date=start_date,
        start_time=start_time,
        end_time=end_time,
        recurrence_end_date=recurrence_end_date,
    )
    if preview.has_conflicts:
        conflicts = [
            f"{occurrence.reservation_date:%d/%m/%Y}: {occurrence.message}"
            for occurrence in preview.occurrences
            if occurrence.conflict
        ]
        raise ValidationError(
            {
                "__all__": [
                    "No se creó ninguna reservación porque el grupo tiene conflictos.",
                    *conflicts,
                ]
            }
        )

    occurrence_dates = [
        occurrence.reservation_date for occurrence in preview.occurrences
    ]
    deadline_quote = calculate_payment_deadline(
        room=locked_room,
        first_reservation_date=occurrence_dates[0],
        first_start_time=start_time,
        requested_at=request_timestamp,
        actor=actor,
        late_exception_reason=deadline_exception_reason,
    )
    batch_type = (
        ReservationBatchType.RECURRING
        if len(occurrence_dates) > 1
        else ReservationBatchType.SINGLE
    )
    cancellation_policy = _active_cancellation_policy(locked_room)
    accepted_at = timezone.now() if cancellation_policy is not None else None
    batch = ReservationBatch(
        room=locked_room,
        tenant_doctor=tenant_doctor,
        batch_type=batch_type,
        status=ReservationBatchStatus.REQUESTED,
        recurrence_rule=_recurrence_rule(occurrence_dates),
        occurrence_count=len(occurrence_dates),
        currency=preview.currency,
        tariff_total=preview.tariff_total,
        tariff_final=preview.tariff_final,
        cancellation_policy=cancellation_policy,
        cancellation_policy_snapshot=_cancellation_policy_snapshot(cancellation_policy),
        cancellation_terms_accepted_at=accepted_at,
        payment_deadline_at=(
            deadline_quote.deadline_at if deadline_quote is not None else None
        ),
        deadline_policy=(
            deadline_quote.deadline_policy
            if deadline_quote is not None
            else ReservationDeadlinePolicy.PENDING_CONFIGURATION
        ),
        deadline_policy_snapshot=(
            deadline_quote.snapshot if deadline_quote is not None else {}
        ),
        requested_at=request_timestamp,
        notes=notes,
    )
    if actor is not None:
        batch.created_by = cast(Any, actor)
        batch.updated_by = cast(Any, actor)
    batch.save()
    if deadline_quote is not None:
        persist_deadline_exception(
            batch=batch,
            quote=deadline_quote,
            actor=actor,
        )

    reservations: list[Reservation] = []
    for occurrence in preview.occurrences:
        reservation = _create_reservation_occurrence(
            batch=batch,
            room=locked_room,
            tenant_doctor=tenant_doctor,
            reservation_date=occurrence.reservation_date,
            start_time=occurrence.start_time,
            end_time=occurrence.end_time,
            notes=notes,
            actor=actor,
        )
        reservations.append(reservation)

    actual_tariff_total = sum(
        (reservation.tariff_total for reservation in reservations),
        Decimal("0.00"),
    )
    actual_tariff_final = sum(
        (reservation.tariff_final for reservation in reservations),
        Decimal("0.00"),
    )
    if (
        batch.tariff_total != actual_tariff_total
        or batch.tariff_final != actual_tariff_final
    ):
        batch.tariff_total = actual_tariff_total
        batch.tariff_final = actual_tariff_final
        batch.save(update_fields=["tariff_total", "tariff_final", "updated_at"])

    record_event(
        event_type="reservation_batch.created",
        object_label=str(batch),
        actor=actor,
        payload=_batch_payload(batch),
    )
    if batch.batch_type == ReservationBatchType.SINGLE:
        _send_reservation_registered_email(reservations[0])
    else:
        _send_reservation_batch_registered_email(batch, reservations)
    return batch


def create_reservation(
    *,
    room: ConsultingRoom,
    tenant_doctor: TenantDoctorProfile,
    reservation_date: date,
    start_time: time,
    end_time: time,
    notes: str = "",
    actor: Model | None = None,
    requested_at: datetime | None = None,
    deadline_exception_reason: str = "",
) -> Reservation:
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=tenant_doctor,
        start_date=reservation_date,
        start_time=start_time,
        end_time=end_time,
        notes=notes,
        actor=actor,
        requested_at=requested_at,
        deadline_exception_reason=deadline_exception_reason,
    )
    return batch.reservations.get()


def _create_reservation_occurrence(
    *,
    batch: ReservationBatch,
    room: ConsultingRoom,
    tenant_doctor: TenantDoctorProfile,
    reservation_date: date,
    start_time: time,
    end_time: time,
    notes: str,
    actor: Model | None,
) -> Reservation:

    reservation = Reservation(
        batch=batch,
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
    _sync_reservation_financial_fields(reservation, statement)
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


def _sync_reservation_financial_fields(
    reservation: Reservation, statement: Any
) -> None:
    reservation.room_discount_percentage = statement.room_discount_percentage
    reservation.tenant_discount_percentage = statement.tenant_discount_percentage
    reservation.tariff_total = statement.tariff_total
    reservation.tariff_final = statement.tariff_final
    reservation.save(
        update_fields=[
            "room_discount_percentage",
            "tenant_discount_percentage",
            "tariff_total",
            "tariff_final",
            "updated_at",
        ]
    )


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
    _refresh_batch_status(reservation.batch, actor=actor)
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
    _refresh_batch_status(reservation.batch, actor=actor)
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
) -> BlockPrice:
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
    return pricing


def _validation_error_text(exc: ValidationError | PricingConfigurationError) -> str:
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return "; ".join(
                str(message)
                for messages in exc.message_dict.values()
                for message in messages
            )
        return "; ".join(str(message) for message in exc.messages)
    return str(exc)


def _active_cancellation_policy(room: ConsultingRoom) -> CancellationPolicy | None:
    today = timezone.localdate()
    queryset = (
        CancellationPolicy.objects.filter(
            clinic=room.clinic,
            is_active=True,
            is_deleted=False,
            start_date__lte=today,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .prefetch_related("penalty_rules")
    )
    room_policy = queryset.filter(room=room).order_by("-start_date", "-version").first()
    if room_policy is not None:
        return room_policy
    return (
        queryset.filter(room__isnull=True).order_by("-start_date", "-version").first()
    )


def _cancellation_policy_snapshot(
    policy: CancellationPolicy | None,
) -> dict[str, Any]:
    if policy is None:
        return {}
    return {
        "id": str(policy.pk),
        "name": policy.name,
        "version": policy.version,
        "clinic_id": str(policy.clinic_id),
        "room_id": str(policy.room_id) if policy.room_id else None,
        "start_date": policy.start_date.isoformat(),
        "end_date": policy.end_date.isoformat() if policy.end_date else None,
        "penalties": [
            {
                "days_before": penalty.days_before,
                "percentage": str(penalty.percentage),
            }
            for penalty in policy.penalty_rules.filter(
                is_active=True,
                is_deleted=False,
            ).order_by("days_before")
        ],
    }


def _recurrence_rule(occurrence_dates: list[date]) -> dict[str, Any]:
    if len(occurrence_dates) <= 1:
        return {}
    return {
        "frequency": "weekly",
        "interval": 1,
        "weekday": occurrence_dates[0].weekday(),
        "start_date": occurrence_dates[0].isoformat(),
        "end_date": occurrence_dates[-1].isoformat(),
        "occurrence_count": len(occurrence_dates),
    }


def _batch_payload(batch: ReservationBatch) -> dict[str, Any]:
    return {
        "model": batch._meta.label,
        "id": str(batch.pk),
        "reference": batch.reference,
        "level": "operativo_financiero",
        "room_id": str(batch.room_id),
        "tenant_doctor_id": str(batch.tenant_doctor_id),
        "batch_type": batch.batch_type,
        "status": batch.status,
        "occurrence_count": batch.occurrence_count,
        "tariff_total": str(batch.tariff_total),
        "tariff_final": str(batch.tariff_final),
        "currency": batch.currency,
        "deadline_policy": batch.deadline_policy,
        "payment_deadline_at": (
            batch.payment_deadline_at.isoformat()
            if batch.payment_deadline_at is not None
            else None
        ),
        "deadline_policy_snapshot": batch.deadline_policy_snapshot,
        "cancellation_policy_id": (
            str(batch.cancellation_policy_id) if batch.cancellation_policy_id else None
        ),
    }


def _refresh_batch_status(
    batch: ReservationBatch | None,
    *,
    actor: Model | None,
) -> None:
    if batch is None:
        return
    statuses = list(batch.reservations.values_list("status", flat=True))
    if statuses and all(status == ReservationStatus.CANCELLED for status in statuses):
        new_status = ReservationBatchStatus.CANCELLED
    elif ReservationStatus.CANCELLED in statuses:
        new_status = ReservationBatchStatus.PARTIALLY_CANCELLED
    elif statuses and all(status == ReservationStatus.CONFIRMED for status in statuses):
        new_status = ReservationBatchStatus.CONFIRMED
    else:
        new_status = ReservationBatchStatus.REQUESTED

    if batch.status == new_status:
        return
    previous_status = batch.status
    batch.status = new_status
    if actor is not None:
        batch.updated_by = cast(Any, actor)
    batch.save(update_fields=["status", "updated_by", "updated_at"])
    record_event(
        event_type="reservation_batch.status_changed",
        object_label=str(batch),
        actor=actor,
        payload={
            **_batch_payload(batch),
            "previous_status": previous_status,
        },
    )


def _send_reservation_batch_registered_email(
    batch: ReservationBatch,
    reservations: list[Reservation],
) -> None:
    owner = batch.room.owner
    recipients = [
        batch.tenant_doctor.user.email,
        owner.user.email if owner else "",
    ]
    recipient_list = list(dict.fromkeys(email for email in recipients if email))
    if not recipient_list:
        return

    first = reservations[0]
    last = reservations[-1]
    room_label = batch.room.number.strip() or batch.room.name
    message = (
        f"Estimado Dr. {batch.tenant_doctor}, se registraron "
        f"{batch.occurrence_count} reservaciones semanales para el consultorio "
        f"{room_label}, del {first.date:%d/%m/%Y} al {last.date:%d/%m/%Y}, "
        f"de las {first.start_time:%H:%M} hasta las {first.end_time:%H:%M}. "
        f"Total: {batch.tariff_final} {batch.currency}. "
        f"Referencia: {batch.reference}."
    )
    send_mail(
        subject=f"Consultorio {room_label}, reservaciones registradas",
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
    )


def _send_reservation_registered_email(reservation: Reservation) -> None:
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
        subject=f"Consultorio {room_label}, reservación registrada",
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
    )


def _reservation_payload(reservation: Reservation, *, level: str) -> dict[str, Any]:
    batch = reservation.batch if reservation.batch_id else None
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
        "batch_id": str(reservation.batch_id) if batch is not None else None,
        "batch_reference": batch.reference if batch is not None else None,
    }
