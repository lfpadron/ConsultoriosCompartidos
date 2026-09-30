"""Payment deadline calculation, exceptions and automatic expiration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Model, Q
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.catalog.models import ConsultingRoom
from apps.core.permissions import can_edit_screen
from apps.identity.models import UserRole
from apps.scheduling.models import (
    PaymentDeadlineException,
    PaymentDeadlineExceptionType,
    ReservationBatch,
    ReservationBatchStatus,
    ReservationDeadlinePolicy,
    ReservationPaymentPolicy,
    ReservationStatus,
)

EXPIRABLE_BATCH_STATUSES = {
    ReservationBatchStatus.REQUESTED,
    ReservationBatchStatus.PARTIALLY_CANCELLED,
}
EXPIRABLE_RESERVATION_STATUSES = {
    ReservationStatus.REQUESTED,
    ReservationStatus.PENDING_PAYMENT,
}


@dataclass(frozen=True)
class PaymentDeadlineQuote:
    deadline_at: datetime
    deadline_policy: str
    policy: ReservationPaymentPolicy
    snapshot: dict[str, Any]
    exception_type: str | None = None
    previous_deadline_at: datetime | None = None
    exception_reason: str = ""


def clinic_timezone(room: ConsultingRoom) -> ZoneInfo:
    timezone_name = room.clinic.timezone or settings.TIME_ZONE
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(
            f"La zona horaria {timezone_name!r} de la clínica no es válida."
        ) from exc


def resolve_payment_policy(
    *,
    room: ConsultingRoom,
    at: datetime,
) -> ReservationPaymentPolicy | None:
    local_at = _local_datetime(at, clinic_timezone(room))
    queryset = ReservationPaymentPolicy.objects.filter(
        clinic=room.clinic,
        is_active=True,
        is_deleted=False,
        start_date__lte=local_at.date(),
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=local_at.date()))
    room_policy = queryset.filter(room=room).order_by("-start_date", "-version").first()
    if room_policy is not None:
        return room_policy
    return (
        queryset.filter(room__isnull=True).order_by("-start_date", "-version").first()
    )


def calculate_payment_deadline(
    *,
    room: ConsultingRoom,
    first_reservation_date: date,
    first_start_time: time,
    requested_at: datetime,
    actor: Model | None = None,
    late_exception_reason: str = "",
) -> PaymentDeadlineQuote | None:
    policy = resolve_payment_policy(room=room, at=requested_at)
    if policy is None:
        return None

    zone = clinic_timezone(room)
    local_requested_at = _local_datetime(requested_at, zone)
    reservation_start = datetime.combine(
        first_reservation_date,
        first_start_time,
        tzinfo=zone,
    )
    advance_days = (first_reservation_date - local_requested_at.date()).days
    if advance_days <= 1 or not policy.advance_rule_enabled:
        deadline_policy = ReservationDeadlinePolicy.HOURS_BEFORE
        deadline_at = reservation_start - timedelta(hours=policy.hours_before_start)
    else:
        deadline_policy = ReservationDeadlinePolicy.PREVIOUS_DAY
        deadline_at = datetime.combine(
            first_reservation_date - timedelta(days=1),
            time(23, 59, 59),
            tzinfo=zone,
        )

    snapshot = _policy_snapshot(
        policy,
        deadline_policy=deadline_policy,
        advance_days=advance_days,
        requested_at=local_requested_at,
        reservation_start=reservation_start,
    )
    quote = PaymentDeadlineQuote(
        deadline_at=deadline_at,
        deadline_policy=deadline_policy,
        policy=policy,
        snapshot=snapshot,
    )
    if deadline_at > local_requested_at:
        return quote

    reason = late_exception_reason.strip()
    if not reason or not _actor_can_manage_deadlines(actor):
        raise ValidationError(
            "El plazo de pago ya venció para este horario. Un administrador de "
            "negocio debe registrar una excepción para solicitarlo."
        )

    override_snapshot = {
        **snapshot,
        "base_deadline_at": deadline_at.isoformat(),
        "exception_type": PaymentDeadlineExceptionType.LATE_BOOKING,
        "exception_reason": reason,
        "deadline_policy": ReservationDeadlinePolicy.ADMIN_OVERRIDE,
    }
    return replace(
        quote,
        deadline_at=reservation_start,
        deadline_policy=ReservationDeadlinePolicy.ADMIN_OVERRIDE,
        snapshot=override_snapshot,
        exception_type=PaymentDeadlineExceptionType.LATE_BOOKING,
        previous_deadline_at=deadline_at,
        exception_reason=reason,
    )


def persist_deadline_exception(
    *,
    batch: ReservationBatch,
    quote: PaymentDeadlineQuote,
    actor: Model | None,
) -> PaymentDeadlineException | None:
    if quote.exception_type is None or quote.previous_deadline_at is None:
        return None
    if actor is None or not _actor_can_manage_deadlines(actor):
        raise ValidationError("La excepción requiere un administrador autorizado.")
    exception = PaymentDeadlineException.objects.create(
        batch=batch,
        exception_type=quote.exception_type,
        reason=quote.exception_reason,
        previous_deadline_at=quote.previous_deadline_at,
        replacement_deadline_at=quote.deadline_at,
        authorized_by=cast(Any, actor),
        created_by=cast(Any, actor),
        updated_by=cast(Any, actor),
    )
    snapshot = {
        **batch.deadline_policy_snapshot,
        "exception_id": str(exception.pk),
        "authorized_by_id": str(actor.pk),
    }
    batch.deadline_policy_snapshot = snapshot
    batch.save(update_fields=["deadline_policy_snapshot", "updated_at"])
    _record_exception_event(exception, actor=actor)
    return exception


@transaction.atomic
def apply_hours_deadline_exception(
    *,
    batch: ReservationBatch,
    reason: str,
    actor: Model,
    applied_at: datetime | None = None,
) -> PaymentDeadlineException:
    locked_batch = (
        ReservationBatch.objects.select_for_update()
        .select_related("room", "room__clinic")
        .get(pk=batch.pk)
    )
    if not _actor_can_manage_deadlines(actor):
        raise ValidationError("La excepción requiere un administrador autorizado.")
    if locked_batch.status not in EXPIRABLE_BATCH_STATUSES:
        raise ValidationError(
            "La excepción sólo se puede aplicar a grupos pendientes de confirmación."
        )
    if locked_batch.deadline_policy != ReservationDeadlinePolicy.PREVIOUS_DAY:
        raise ValidationError(
            "La excepción de horas sólo aplica a la regla del día anterior."
        )
    if hasattr(locked_batch, "deadline_exception"):
        raise ValidationError("Este grupo ya tiene una excepción de vencimiento.")
    if locked_batch.payment_proof_submitted_at or _batch_has_payment_submission(
        locked_batch
    ):
        raise ValidationError("El grupo ya tiene un comprobante o pago registrado.")
    if locked_batch.payment_deadline_at is None:
        raise ValidationError("El grupo no tiene un vencimiento calculado.")

    first_reservation = locked_batch.reservations.order_by("date", "start_time").first()
    if first_reservation is None:
        raise ValidationError("El grupo no tiene reservaciones.")
    hours_before = int(
        locked_batch.deadline_policy_snapshot.get("hours_before_start", 0)
    )
    zone = clinic_timezone(locked_batch.room)
    reservation_start = datetime.combine(
        first_reservation.date,
        first_reservation.start_time,
        tzinfo=zone,
    )
    replacement_deadline = reservation_start - timedelta(hours=hours_before)
    now = _local_datetime(applied_at or timezone.now(), zone)
    if replacement_deadline <= locked_batch.payment_deadline_at:
        raise ValidationError(
            "La regla de horas no extiende el vencimiento vigente para este horario."
        )
    if replacement_deadline <= now:
        raise ValidationError(
            "La fecha límite resultante ya venció; no se puede aplicar la excepción."
        )

    exception = PaymentDeadlineException.objects.create(
        batch=locked_batch,
        exception_type=PaymentDeadlineExceptionType.HOURS_RULE,
        reason=reason,
        previous_deadline_at=locked_batch.payment_deadline_at,
        replacement_deadline_at=replacement_deadline,
        authorized_by=cast(Any, actor),
        created_by=cast(Any, actor),
        updated_by=cast(Any, actor),
    )
    locked_batch.payment_deadline_at = replacement_deadline
    locked_batch.deadline_policy = ReservationDeadlinePolicy.EXCEPTION_HOURS
    locked_batch.deadline_policy_snapshot = {
        **locked_batch.deadline_policy_snapshot,
        "base_deadline_at": exception.previous_deadline_at.isoformat(),
        "deadline_at": replacement_deadline.isoformat(),
        "deadline_policy": ReservationDeadlinePolicy.EXCEPTION_HOURS,
        "exception_type": PaymentDeadlineExceptionType.HOURS_RULE,
        "exception_reason": reason,
        "exception_id": str(exception.pk),
        "authorized_by_id": str(actor.pk),
    }
    locked_batch.updated_by = cast(Any, actor)
    locked_batch.save(
        update_fields=[
            "payment_deadline_at",
            "deadline_policy",
            "deadline_policy_snapshot",
            "updated_by",
            "updated_at",
        ]
    )
    _record_exception_event(exception, actor=actor)
    return exception


def expire_overdue_reservation_batches(*, as_of: datetime | None = None) -> int:
    cutoff = as_of or timezone.now()
    candidate_ids = list(
        ReservationBatch.objects.filter(
            status__in=EXPIRABLE_BATCH_STATUSES,
            payment_deadline_at__isnull=False,
            payment_deadline_at__lte=cutoff,
            payment_proof_submitted_at__isnull=True,
            expired_at__isnull=True,
            is_deleted=False,
        ).values_list("pk", flat=True)
    )
    expired_count = 0
    for batch_id in candidate_ids:
        if _expire_batch(batch_id=batch_id, as_of=cutoff):
            expired_count += 1
    return expired_count


@transaction.atomic
def _expire_batch(*, batch_id: Any, as_of: datetime) -> bool:
    batch = (
        ReservationBatch.objects.select_for_update()
        .select_related(
            "room",
            "room__clinic",
            "tenant_doctor",
            "tenant_doctor__user",
        )
        .filter(
            pk=batch_id,
            status__in=EXPIRABLE_BATCH_STATUSES,
            payment_deadline_at__lte=as_of,
            payment_proof_submitted_at__isnull=True,
            expired_at__isnull=True,
            is_deleted=False,
        )
        .first()
    )
    if batch is None:
        return False
    deadline_at = batch.payment_deadline_at
    if deadline_at is None:
        return False
    if not bool(batch.deadline_policy_snapshot.get("automatic_cancellation", False)):
        return False
    if _batch_has_payment_submission(batch):
        return False

    from apps.scheduling.services.reservation_service import cancel_reservation

    cancelled_ids: list[str] = []
    reservations = batch.reservations.filter(
        status__in=EXPIRABLE_RESERVATION_STATUSES,
        is_deleted=False,
    ).order_by("date", "start_time")
    for reservation in reservations:
        cancel_reservation(
            reservation=reservation,
            reason="Vencimiento automático por falta de comprobante de pago.",
        )
        cancelled_ids.append(str(reservation.pk))
    if not cancelled_ids:
        return False

    previous_status = batch.status
    batch.status = ReservationBatchStatus.EXPIRED
    batch.expired_at = as_of
    batch.save(update_fields=["status", "expired_at", "updated_at"])
    record_event(
        event_type="reservation_batch.expired",
        object_label=str(batch),
        payload={
            "model": batch._meta.label,
            "id": str(batch.pk),
            "reference": batch.reference,
            "previous_status": previous_status,
            "status": batch.status,
            "deadline_at": deadline_at.isoformat(),
            "expired_at": as_of.isoformat(),
            "cancelled_reservation_ids": cancelled_ids,
            "reason": "payment_deadline_elapsed_without_submission",
        },
    )
    _send_expiration_email(batch)
    return True


def _batch_has_payment_submission(batch: ReservationBatch) -> bool:
    from apps.finance.models import PaymentStatus

    return batch.reservations.filter(
        payments__status__in={PaymentStatus.REGISTERED, PaymentStatus.VALIDATED},
        payments__is_deleted=False,
    ).exists()


def _send_expiration_email(batch: ReservationBatch) -> None:
    user_model = get_user_model()
    admin_roles = {UserRole.SUPERADMIN, UserRole.ADMIN}
    admins = (
        user_model.objects.filter(is_active=True)
        .filter(
            Q(role__in=admin_roles)
            | Q(
                role_assignments__role__in=admin_roles,
                role_assignments__is_active=True,
                role_assignments__is_deleted=False,
            )
        )
        .filter(
            Q(assigned_clinics=batch.room.clinic) | Q(assigned_clinics__isnull=True)
        )
        .values_list("email", flat=True)
        .distinct()
    )
    recipients = [batch.tenant_doctor.user.email, *admins]
    recipient_list = list(dict.fromkeys(email for email in recipients if email))
    if not recipient_list:
        return
    room_label = batch.room.number.strip() or batch.room.name
    send_mail(
        subject=f"Consultorio {room_label}, reservación cancelada por vencimiento",
        message=(
            f"La reservación {batch.reference} fue cancelada automáticamente "
            "porque venció el plazo para registrar el comprobante de pago."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
    )


def _record_exception_event(
    exception: PaymentDeadlineException,
    *,
    actor: Model,
) -> None:
    record_event(
        event_type="payment_deadline_exception.created",
        object_label=str(exception),
        actor=actor,
        payload={
            "model": exception._meta.label,
            "id": str(exception.pk),
            "batch_id": str(exception.batch_id),
            "batch_reference": exception.batch.reference,
            "exception_type": exception.exception_type,
            "reason": exception.reason,
            "previous_deadline_at": exception.previous_deadline_at.isoformat(),
            "replacement_deadline_at": (exception.replacement_deadline_at.isoformat()),
            "authorized_by_id": str(exception.authorized_by_id),
        },
    )


def _policy_snapshot(
    policy: ReservationPaymentPolicy,
    *,
    deadline_policy: str,
    advance_days: int,
    requested_at: datetime,
    reservation_start: datetime,
) -> dict[str, Any]:
    return {
        "id": str(policy.pk),
        "version": policy.version,
        "clinic_id": str(policy.clinic_id),
        "room_id": str(policy.room_id) if policy.room_id else None,
        "scope": "room" if policy.room_id else "clinic",
        "hours_before_start": policy.hours_before_start,
        "advance_rule_enabled": policy.advance_rule_enabled,
        "automatic_cancellation": policy.automatic_cancellation,
        "start_date": policy.start_date.isoformat(),
        "end_date": policy.end_date.isoformat() if policy.end_date else None,
        "deadline_policy": deadline_policy,
        "advance_days": advance_days,
        "requested_at": requested_at.isoformat(),
        "reservation_start": reservation_start.isoformat(),
    }


def _local_datetime(value: datetime, zone: ZoneInfo) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, zone)
    return timezone.localtime(value, zone)


def _actor_can_manage_deadlines(actor: Model | None) -> bool:
    if actor is None:
        return False
    return can_edit_screen(actor, "payment_deadlines")
