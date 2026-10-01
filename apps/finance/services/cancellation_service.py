"""Cancellation penalties, refunds and tenant-credit workflow."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Model, Q, Sum
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.finance.models import (
    CancellationCase,
    CancellationCaseStatus,
    CancellationItem,
    CancellationResolutionMethod,
    Payment,
    PaymentAllocation,
    PaymentStatus,
    Settlement,
    SettlementStatus,
    TenantCredit,
    TenantCreditStatus,
)
from apps.finance.services.payment_service import reject_payment
from apps.finance.services.settlement_service import cancel_settlement
from apps.identity.models import UserRole
from apps.scheduling.models import (
    ACTIVE_RESERVATION_STATUSES,
    Reservation,
    ReservationBatch,
    ReservationBatchType,
)
from apps.scheduling.services.reservation_service import cancel_reservation

MONEY_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class CancellationQuoteItem:
    reservation: Reservation
    days_before: int
    penalty_percentage: Decimal
    paid_amount: Decimal
    penalty_amount: Decimal
    refundable_amount: Decimal


@dataclass(frozen=True)
class CancellationQuote:
    items: tuple[CancellationQuoteItem, ...]
    currency: str
    total_paid: Decimal
    penalty_amount: Decimal
    refundable_amount: Decimal


def calculate_cancellation_quote(
    *,
    reservations: list[Reservation],
    as_of: datetime | None = None,
) -> CancellationQuote:
    if not reservations:
        raise ValidationError({"reservations": "Selecciona al menos una reservación."})
    tenant_ids = {item.tenant_doctor_id for item in reservations}
    batch_ids = {item.batch_id for item in reservations}
    if len(tenant_ids) != 1 or len(batch_ids) != 1:
        raise ValidationError(
            {"reservations": "Las reservaciones deben pertenecer al mismo grupo."}
        )
    batch = reservations[0].batch
    currency = batch.currency if batch is not None else "MXN"
    cancellation_time = as_of or timezone.now()
    items = tuple(
        _quote_item(reservation, cancellation_time=cancellation_time)
        for reservation in sorted(
            reservations,
            key=lambda item: (item.date, item.start_time),
        )
    )
    return CancellationQuote(
        items=items,
        currency=currency,
        total_paid=sum((item.paid_amount for item in items), Decimal("0.00")),
        penalty_amount=sum(
            (item.penalty_amount for item in items), Decimal("0.00")
        ),
        refundable_amount=sum(
            (item.refundable_amount for item in items), Decimal("0.00")
        ),
    )


@transaction.atomic
def create_cancellation_case(
    *,
    reservations: list[Reservation],
    reason: str,
    actor: Model | None = None,
    as_of: datetime | None = None,
) -> CancellationCase:
    if not reason.strip():
        raise ValidationError({"reason": "El motivo de cancelación es obligatorio."})
    if not reservations:
        raise ValidationError({"reservations": "Selecciona al menos una reservación."})

    reservation_ids = [item.pk for item in reservations]
    locked_reservations = list(
        Reservation.objects.select_for_update()
        .filter(pk__in=reservation_ids, is_deleted=False)
        .select_related(
            "batch",
            "room",
            "room__clinic",
            "tenant_doctor",
            "tenant_doctor__user",
        )
        .order_by("date", "start_time")
    )
    if len(locked_reservations) != len(set(reservation_ids)):
        raise ValidationError({"reservations": "No se encontraron todas las fechas."})
    batch = locked_reservations[0].batch
    if any(
        item.batch_id != locked_reservations[0].batch_id
        for item in locked_reservations
    ):
        raise ValidationError(
            {"reservations": "Las reservaciones deben pertenecer al mismo grupo."}
        )
    if any(
        item.status not in ACTIVE_RESERVATION_STATUSES
        for item in locked_reservations
    ):
        raise ValidationError(
            {"reservations": "Sólo se pueden cancelar reservaciones activas."}
        )
    if CancellationItem.objects.filter(
        reservation__in=locked_reservations,
        is_deleted=False,
    ).exists():
        raise ValidationError(
            {"reservations": "Una de las reservaciones ya tiene una cancelación."}
        )

    if batch is not None:
        locked_batch = ReservationBatch.objects.select_for_update().get(pk=batch.pk)
        active_count = locked_batch.reservations.filter(
            status__in=ACTIVE_RESERVATION_STATUSES,
            is_deleted=False,
        ).count()
        if (
            locked_batch.batch_type == ReservationBatchType.SINGLE
            and len(locked_reservations) != active_count
        ):
            raise ValidationError(
                {
                    "reservations": (
                        "Una reservación única debe cancelarse completa."
                    )
                }
            )
        _reject_pending_group_payment(locked_batch, actor=actor)

    cancellation_time = as_of or timezone.now()
    quote = calculate_cancellation_quote(
        reservations=locked_reservations,
        as_of=cancellation_time,
    )
    case_status = (
        CancellationCaseStatus.PENDING
        if quote.refundable_amount > Decimal("0.00")
        else CancellationCaseStatus.NO_REFUND
    )
    cancellation_case = CancellationCase(
        batch=batch,
        tenant_doctor=locked_reservations[0].tenant_doctor,
        requested_by=cast(Any, actor) if actor is not None else None,
        status=case_status,
        resolution_method=CancellationResolutionMethod.NOT_APPLICABLE,
        reason=reason,
        currency=quote.currency,
        total_paid=quote.total_paid,
        penalty_amount=quote.penalty_amount,
        refundable_amount=quote.refundable_amount,
        requested_at=cancellation_time,
        resolved_at=(
            cancellation_time
            if case_status == CancellationCaseStatus.NO_REFUND
            else None
        ),
    )
    _set_audit_users(cancellation_case, actor, created=True)
    cancellation_case.save()

    quote_by_id = {item.reservation.pk: item for item in quote.items}
    for reservation in locked_reservations:
        item_quote = quote_by_id[reservation.pk]
        _cancel_open_settlements(reservation, actor=actor)
        case_item = CancellationItem(
            cancellation_case=cancellation_case,
            reservation=reservation,
            cancellation_policy=batch.cancellation_policy if batch else None,
            policy_snapshot=(batch.cancellation_policy_snapshot if batch else {}),
            days_before=item_quote.days_before,
            penalty_percentage=item_quote.penalty_percentage,
            paid_amount=item_quote.paid_amount,
            penalty_amount=item_quote.penalty_amount,
            refundable_amount=item_quote.refundable_amount,
            cancelled_at=cancellation_time,
        )
        _set_audit_users(case_item, actor, created=True)
        case_item.save()
        cancel_reservation(
            reservation=reservation,
            reason=reason,
            actor=actor,
        )
        record_event(
            event_type="cancellation.item_created",
            object_label=str(case_item),
            actor=actor,
            payload=_item_payload(case_item, actor=actor),
        )

    record_event(
        event_type="cancellation.case_created",
        object_label=str(cancellation_case),
        actor=actor,
        payload=_case_payload(cancellation_case, actor=actor),
    )
    transaction.on_commit(lambda: _notify_admins(cancellation_case))
    return cancellation_case


@transaction.atomic
def resolve_with_manual_refund(
    *,
    cancellation_case: CancellationCase,
    reference: str,
    refund_date: Any,
    receipt: Any,
    notes: str = "",
    actor: Model | None = None,
) -> CancellationCase:
    _require_admin(actor)
    case = CancellationCase.objects.select_for_update().select_related(
        "tenant_doctor",
        "tenant_doctor__user",
        "batch",
    ).get(pk=cancellation_case.pk, is_deleted=False)
    _validate_pending_case(case)
    if not reference.strip():
        raise ValidationError({"reference": "La referencia es obligatoria."})
    if refund_date is None:
        raise ValidationError({"refund_date": "La fecha es obligatoria."})
    if not receipt:
        raise ValidationError({"receipt": "El comprobante es obligatorio."})

    case.status = CancellationCaseStatus.RESOLVED
    case.resolution_method = CancellationResolutionMethod.MANUAL_REFUND
    case.refund_reference = reference
    case.refund_date = refund_date
    case.refund_receipt = receipt
    case.notes = notes or case.notes
    case.resolved_at = timezone.now()
    case.managed_by = cast(Any, actor) if actor is not None else None
    _set_audit_users(case, actor)
    case.save()
    record_event(
        event_type="cancellation.refund_completed",
        object_label=str(case),
        actor=actor,
        payload=_case_payload(case, actor=actor),
    )
    transaction.on_commit(lambda: _notify_tenant_resolution(case))
    return case


@transaction.atomic
def resolve_with_future_credit(
    *,
    cancellation_case: CancellationCase,
    actor: Model | None = None,
) -> tuple[CancellationCase, TenantCredit]:
    _require_admin(actor)
    case = CancellationCase.objects.select_for_update().select_related(
        "tenant_doctor",
        "tenant_doctor__user",
        "batch",
    ).get(pk=cancellation_case.pk, is_deleted=False)
    _validate_pending_case(case)
    if case.batch is None or case.batch.batch_type != ReservationBatchType.RECURRING:
        raise ValidationError(
            {"resolution_method": "Sólo las reservaciones repetitivas generan saldo."}
        )

    credit = TenantCredit(
        cancellation_case=case,
        tenant_doctor=case.tenant_doctor,
        original_amount=case.refundable_amount,
        remaining_amount=case.refundable_amount,
        currency=case.currency,
        status=TenantCreditStatus.ACTIVE,
    )
    _set_audit_users(credit, actor, created=True)
    credit.save()

    case.status = CancellationCaseStatus.RESOLVED
    case.resolution_method = CancellationResolutionMethod.FUTURE_CREDIT
    case.resolved_at = timezone.now()
    case.managed_by = cast(Any, actor) if actor is not None else None
    _set_audit_users(case, actor)
    case.save()
    record_event(
        event_type="tenant_credit.issued",
        object_label=str(credit),
        actor=actor,
        payload={
            **_case_payload(case, actor=actor),
            "credit_id": str(credit.pk),
            "credit_amount": str(credit.original_amount),
        },
    )
    record_event(
        event_type="cancellation.credit_resolved",
        object_label=str(case),
        actor=actor,
        payload=_case_payload(case, actor=actor),
    )
    transaction.on_commit(lambda: _notify_tenant_resolution(case))
    return case, credit


def available_credit_for_tenant(*, tenant_doctor: Any, currency: str) -> Decimal:
    return TenantCredit.objects.filter(
        tenant_doctor=tenant_doctor,
        currency=currency,
        status=TenantCreditStatus.ACTIVE,
        remaining_amount__gt=0,
        is_deleted=False,
    ).aggregate(total=Sum("remaining_amount"))["total"] or Decimal("0.00")


def _quote_item(
    reservation: Reservation,
    *,
    cancellation_time: datetime,
) -> CancellationQuoteItem:
    clinic_timezone = ZoneInfo(reservation.room.clinic.timezone)
    local_time = cancellation_time.astimezone(clinic_timezone)
    reservation_start = datetime.combine(
        reservation.date,
        reservation.start_time,
        tzinfo=clinic_timezone,
    )
    if local_time >= reservation_start:
        raise ValidationError(
            {
                "reservations": (
                    f"La reservación del {reservation.date:%d/%m/%Y} ya inició."
                )
            }
        )
    days_before = max((reservation.date - local_time.date()).days, 0)
    percentage = _penalty_percentage(reservation, days_before=days_before)
    paid_amount = _validated_amount_for_reservation(reservation)
    penalty_amount = (
        paid_amount * percentage / Decimal("100.0")
    ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    refundable_amount = paid_amount - penalty_amount
    return CancellationQuoteItem(
        reservation=reservation,
        days_before=days_before,
        penalty_percentage=percentage,
        paid_amount=paid_amount,
        penalty_amount=penalty_amount,
        refundable_amount=refundable_amount,
    )


def _penalty_percentage(reservation: Reservation, *, days_before: int) -> Decimal:
    batch = reservation.batch
    penalties = (
        batch.cancellation_policy_snapshot.get("penalties", []) if batch else []
    )
    normalized = sorted(
        (
            (int(item["days_before"]), Decimal(str(item["percentage"])))
            for item in penalties
        ),
        key=lambda item: item[0],
    )
    for threshold, percentage in normalized:
        if days_before <= threshold:
            return percentage
    return Decimal("0.0")


def _validated_amount_for_reservation(reservation: Reservation) -> Decimal:
    allocated = PaymentAllocation.objects.filter(
        reservation=reservation,
        payment__status=PaymentStatus.VALIDATED,
        payment__is_deleted=False,
        is_deleted=False,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    legacy = Payment.objects.filter(
        reservation=reservation,
        status=PaymentStatus.VALIDATED,
        is_deleted=False,
        allocations__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return allocated + legacy


def _reject_pending_group_payment(
    batch: ReservationBatch,
    *,
    actor: Model | None,
) -> None:
    pending = Payment.objects.select_for_update().filter(
        batch=batch,
        reservation__isnull=True,
        status=PaymentStatus.REGISTERED,
        is_deleted=False,
    )
    for payment in pending:
        reject_payment(
            payment=payment,
            reason="El grupo fue modificado por una cancelación.",
            actor=actor,
        )


def _cancel_open_settlements(
    reservation: Reservation,
    *,
    actor: Model | None,
) -> None:
    settlements = Settlement.objects.select_for_update().filter(
        reservation=reservation,
        status__in=(SettlementStatus.PENDING, SettlementStatus.CALCULATED),
        is_deleted=False,
    )
    for settlement in settlements:
        cancel_settlement(settlement=settlement, actor=actor)


def _validate_pending_case(case: CancellationCase) -> None:
    if case.status != CancellationCaseStatus.PENDING:
        raise ValidationError({"status": "La cancelación ya fue resuelta."})
    if case.refundable_amount <= Decimal("0.00"):
        raise ValidationError({"status": "La cancelación no tiene saldo a devolver."})


def _require_admin(actor: Model | None) -> None:
    if actor is None:
        raise ValidationError({"actor": "Se requiere un administrador."})
    role_getter = getattr(actor, "get_role_values", None)
    roles = (
        set(role_getter())
        if callable(role_getter)
        else {getattr(actor, "role", "")}
    )
    if not roles.intersection({UserRole.SUPERADMIN, UserRole.ADMIN}):
        raise ValidationError(
            {"actor": "Sólo un administrador puede resolver devoluciones."}
        )


def _set_audit_users(
    instance: Any,
    actor: Model | None,
    *,
    created: bool = False,
) -> None:
    if actor is None:
        return
    if created:
        instance.created_by = cast(Any, actor)
    instance.updated_by = cast(Any, actor)


def _notify_admins(cancellation_case: CancellationCase) -> None:
    if cancellation_case.batch is None:
        return
    user_model = get_user_model()
    clinic = cancellation_case.batch.room.clinic
    recipients = list(
        user_model.objects.filter(is_active=True)
        .filter(
            Q(role__in={UserRole.SUPERADMIN, UserRole.ADMIN})
            | Q(
                role_assignments__role__in={UserRole.SUPERADMIN, UserRole.ADMIN},
                role_assignments__is_active=True,
                role_assignments__is_deleted=False,
            )
        )
        .filter(Q(assigned_clinics=clinic) | Q(assigned_clinics__isnull=True))
        .values_list("email", flat=True)
        .distinct()
    )
    if not recipients:
        return
    send_mail(
        subject=f"Cancelación pendiente, grupo {cancellation_case.batch.reference}",
        message=(
            f"Se cancelaron {cancellation_case.items.count()} reservaciones de "
            f"{cancellation_case.tenant_doctor}. Importe a resolver: "
            f"{cancellation_case.refundable_amount} {cancellation_case.currency}."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        fail_silently=True,
    )


def _notify_tenant_resolution(cancellation_case: CancellationCase) -> None:
    recipient = cancellation_case.tenant_doctor.user.email
    if not recipient:
        return
    send_mail(
        subject="Cancelación resuelta",
        message=(
            f"Su cancelación fue resuelta mediante "
            f"{cancellation_case.get_resolution_method_display()}. Importe: "
            f"{cancellation_case.refundable_amount} {cancellation_case.currency}."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=True,
    )


def _case_payload(
    cancellation_case: CancellationCase,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": cancellation_case._meta.label,
        "id": str(cancellation_case.pk),
        "batch_id": str(cancellation_case.batch_id or ""),
        "tenant_doctor_id": str(cancellation_case.tenant_doctor_id),
        "status": cancellation_case.status,
        "resolution_method": cancellation_case.resolution_method,
        "total_paid": str(cancellation_case.total_paid),
        "penalty_amount": str(cancellation_case.penalty_amount),
        "refundable_amount": str(cancellation_case.refundable_amount),
        "currency": cancellation_case.currency,
        "level": "legal_financiero",
        "actor_id": str(actor.pk) if actor is not None else "",
    }


def _item_payload(
    item: CancellationItem,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": item._meta.label,
        "id": str(item.pk),
        "cancellation_case_id": str(item.cancellation_case_id),
        "reservation_id": str(item.reservation_id),
        "days_before": str(item.days_before),
        "penalty_percentage": str(item.penalty_percentage),
        "paid_amount": str(item.paid_amount),
        "penalty_amount": str(item.penalty_amount),
        "refundable_amount": str(item.refundable_amount),
        "level": "legal_financiero",
        "actor_id": str(actor.pk) if actor is not None else "",
    }
