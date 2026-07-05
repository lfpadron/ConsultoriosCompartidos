"""Manual payment workflow services."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model, QuerySet, Sum
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.finance.models import Payment, PaymentMethod, PaymentStatus, StatementStatus
from apps.scheduling.models import Reservation, ReservationStatus


@dataclass(frozen=True)
class PaymentSummary:
    reservation: Reservation
    total_to_pay: Decimal
    total_validated: Decimal
    pending_balance: Decimal
    currency: str
    payments: QuerySet[Payment]


@transaction.atomic
def register_payment(
    *,
    reservation: Reservation,
    amount: Decimal,
    method: str,
    reference: str = "",
    payment_date: date | None = None,
    currency: str = "",
    receipt: Any = None,
    notes: str = "",
    actor: Model | None = None,
) -> Payment:
    statement = _current_statement_for_reservation(reservation)
    payment = Payment(
        reservation=reservation,
        statement=statement,
        tenant_doctor=reservation.tenant_doctor,
        amount=amount,
        currency=currency or statement.currency,
        method=method,
        reference=reference,
        payment_date=payment_date or timezone.localdate(),
        status=PaymentStatus.REGISTERED,
        notes=notes,
    )
    if receipt:
        payment.receipt = receipt
    if actor is not None:
        payment.created_by = cast(Any, actor)
        payment.updated_by = cast(Any, actor)
    payment.save()

    record_event(
        event_type="payment.registered",
        object_label=str(payment),
        actor=actor,
        payload=_payment_payload(payment, actor=actor),
    )
    return payment


@transaction.atomic
def validate_payment(
    *,
    payment: Payment,
    actor: Model | None = None,
) -> Payment:
    if payment.status in {PaymentStatus.REJECTED, PaymentStatus.CANCELLED}:
        raise ValidationError(
            {"status": "No se puede validar un pago rechazado o cancelado."}
        )

    payment.status = PaymentStatus.VALIDATED
    payment.validated_at = timezone.now()
    if actor is not None:
        payment.validated_by = cast(Any, actor)
        payment.updated_by = cast(Any, actor)
    payment.save()

    record_event(
        event_type="payment.validated",
        object_label=str(payment),
        actor=actor,
        payload=_payment_payload(payment, actor=actor),
    )
    _mark_reservation_paid_if_covered(payment.reservation, actor=actor)
    return payment


@transaction.atomic
def reject_payment(
    *,
    payment: Payment,
    reason: str,
    actor: Model | None = None,
) -> Payment:
    if not reason.strip():
        raise ValidationError(
            {"rejected_reason": "El motivo de rechazo es obligatorio."}
        )
    if payment.status == PaymentStatus.VALIDATED:
        raise ValidationError({"status": "No se puede rechazar un pago validado."})
    if payment.status == PaymentStatus.CANCELLED:
        raise ValidationError({"status": "No se puede rechazar un pago cancelado."})

    payment.status = PaymentStatus.REJECTED
    payment.rejected_reason = reason
    if actor is not None:
        payment.updated_by = cast(Any, actor)
    payment.save()

    record_event(
        event_type="payment.rejected",
        object_label=str(payment),
        actor=actor,
        payload={**_payment_payload(payment, actor=actor), "reason": reason},
    )
    return payment


@transaction.atomic
def cancel_payment(
    *,
    payment: Payment,
    actor: Model | None = None,
) -> Payment:
    if payment.status == PaymentStatus.VALIDATED:
        raise ValidationError({"status": "No se puede cancelar un pago validado."})

    payment.status = PaymentStatus.CANCELLED
    if actor is not None:
        payment.updated_by = cast(Any, actor)
    payment.save()

    record_event(
        event_type="payment.cancelled",
        object_label=str(payment),
        actor=actor,
        payload=_payment_payload(payment, actor=actor),
    )
    return payment


def get_payment_summary_for_reservation(reservation: Reservation) -> PaymentSummary:
    statement = _current_statement_for_reservation(reservation)
    payments = (
        Payment.objects.filter(reservation=reservation, is_deleted=False)
        .select_related("statement", "tenant_doctor", "validated_by")
        .order_by("-payment_date", "-created_at")
    )
    total_validated = _validated_total_for_statement(statement_id=statement.pk)
    total_to_pay = statement.total_doctor
    pending_balance = max(total_to_pay - total_validated, Decimal("0.00"))
    return PaymentSummary(
        reservation=reservation,
        total_to_pay=total_to_pay,
        total_validated=total_validated,
        pending_balance=pending_balance,
        currency=statement.currency,
        payments=payments,
    )


def _mark_reservation_paid_if_covered(
    reservation: Reservation,
    *,
    actor: Model | None,
) -> None:
    summary = get_payment_summary_for_reservation(reservation)
    if summary.total_validated < summary.total_to_pay:
        return
    if reservation.status in {
        ReservationStatus.PAID,
        ReservationStatus.CONFIRMED,
        ReservationStatus.CANCELLED,
        ReservationStatus.FINISHED,
    }:
        return

    reservation.status = ReservationStatus.PAID
    if actor is not None:
        reservation.updated_by = cast(Any, actor)
    reservation.save(update_fields=["status", "updated_by", "updated_at"])
    record_event(
        event_type="reservation.marked_paid",
        object_label=str(reservation),
        actor=actor,
        payload={
            "model": reservation._meta.label,
            "id": str(reservation.pk),
            "level": "financiero",
            "reservation": str(reservation),
            "total_validated": str(summary.total_validated),
            "total_to_pay": str(summary.total_to_pay),
            "currency": summary.currency,
            "actor_id": str(actor.pk) if actor is not None else "",
        },
    )


def _current_statement_for_reservation(reservation: Reservation) -> Any:
    statement = (
        reservation.statements.filter(status=StatementStatus.CURRENT)
        .order_by("-version")
        .first()
    )
    if statement is None:
        raise ValidationError(
            {"statement": "La reservación no tiene estado de cuenta vigente."}
        )
    return statement


def _validated_total_for_statement(*, statement_id: Any) -> Decimal:
    return Payment.objects.filter(
        statement_id=statement_id,
        status=PaymentStatus.VALIDATED,
        is_deleted=False,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def _payment_payload(payment: Payment, *, actor: Model | None) -> dict[str, str]:
    payload = {
        "model": payment._meta.label,
        "id": str(payment.pk),
        "level": "financiero",
        "reservation_id": str(payment.reservation_id),
        "reservation": str(payment.reservation),
        "statement_id": str(payment.statement_id),
        "tenant_doctor_id": str(payment.tenant_doctor_id),
        "amount": str(payment.amount),
        "currency": payment.currency,
        "method": payment.method,
        "reference": payment.reference,
        "status": payment.status,
        "actor_id": str(actor.pk) if actor is not None else "",
    }
    if payment.method == PaymentMethod.CASH and not payment.reference:
        payload["reference"] = ""
    return payload
