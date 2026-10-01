"""Payment submission, allocation and validation services."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Model, Q, QuerySet, Sum
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.finance.models import (
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentStatus,
    Statement,
    StatementStatus,
    TenantCredit,
    TenantCreditApplication,
    TenantCreditApplicationStatus,
    TenantCreditStatus,
)
from apps.finance.services.settlement_service import generate_settlement_for_reservation
from apps.scheduling.models import (
    Reservation,
    ReservationBatch,
    ReservationBatchStatus,
    ReservationStatus,
)
from apps.scheduling.services.reservation_service import confirm_reservation


@dataclass(frozen=True)
class PaymentSummary:
    reservation: Reservation
    total_to_pay: Decimal
    total_validated: Decimal
    pending_balance: Decimal
    currency: str
    payments: QuerySet[Payment]


@dataclass(frozen=True)
class BatchPaymentSummary:
    batch: ReservationBatch
    total_to_pay: Decimal
    total_submitted: Decimal
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
    """Preserve the legacy one-reservation registration flow."""

    if method == PaymentMethod.CREDIT:
        raise ValidationError(
            {"method": "El saldo a favor sólo puede aplicarse a un grupo."}
        )

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
    _set_audit_users(payment, actor, created=True)
    payment.save()
    _create_allocation(
        payment=payment,
        reservation=reservation,
        statement=statement,
        amount=amount,
        actor=actor,
    )

    record_event(
        event_type="payment.registered",
        object_label=str(payment),
        actor=actor,
        payload=_payment_payload(payment, actor=actor),
    )
    return payment


@transaction.atomic
def submit_batch_payment(
    *,
    batch: ReservationBatch,
    amount: Decimal,
    method: str,
    reference: str,
    receipt: Any,
    credit_amount: Decimal = Decimal("0.00"),
    payment_date: date | None = None,
    currency: str = "",
    notes: str = "",
    actor: Model | None = None,
) -> Payment:
    """Submit one receipt and allocate it over every payable occurrence."""

    credit_amount = credit_amount or Decimal("0.00")

    locked_batch = (
        ReservationBatch.objects.select_for_update()
        .select_related("room", "room__owner", "tenant_doctor", "tenant_doctor__user")
        .get(pk=batch.pk, is_deleted=False)
    )
    if locked_batch.status not in {
        ReservationBatchStatus.REQUESTED,
        ReservationBatchStatus.PARTIALLY_CANCELLED,
    }:
        raise ValidationError({"batch": "El grupo ya no admite comprobantes de pago."})
    now = timezone.now()
    if (
        locked_batch.payment_deadline_at is not None
        and now > locked_batch.payment_deadline_at
    ):
        raise ValidationError(
            {"batch": "La fecha límite para enviar el comprobante ya venció."}
        )
    if Payment.objects.filter(
        batch=locked_batch,
        status__in=(PaymentStatus.REGISTERED, PaymentStatus.VALIDATED),
        is_deleted=False,
    ).exists():
        raise ValidationError(
            {"batch": "El grupo ya tiene un comprobante pendiente o validado."}
        )

    payable = _payable_reservations(locked_batch, lock=True)
    if not payable:
        raise ValidationError(
            {"batch": "El grupo no tiene reservaciones pendientes de pago."}
        )
    statements = {
        statement.reservation_id: statement
        for statement in Statement.objects.select_for_update().filter(
            reservation__in=payable,
            status=StatementStatus.CURRENT,
            is_deleted=False,
        )
    }
    if len(statements) != len(payable):
        raise ValidationError(
            {"statement": "Todas las reservaciones deben tener un estado vigente."}
        )
    required_total = sum(
        (statements[item.pk].total_doctor for item in payable),
        Decimal("0.00"),
    )
    if required_total <= Decimal("0.00"):
        raise ValidationError(
            {"amount": "El grupo no tiene un saldo positivo por comprobar."}
        )
    if amount < required_total:
        raise ValidationError(
            {
                "amount": (
                    f"El comprobante debe cubrir al menos {required_total:.2f} "
                    f"{locked_batch.currency}."
                )
            }
        )
    maximum_credit = min(amount, required_total)
    if credit_amount < Decimal("0.00") or credit_amount > maximum_credit:
        raise ValidationError(
            {"credit_amount": "El saldo aplicado no es válido para este pago."}
        )
    cash_amount = amount - credit_amount
    if cash_amount > Decimal("0.00") and not receipt:
        raise ValidationError({"receipt": "El comprobante es obligatorio."})
    if cash_amount == Decimal("0.00"):
        method = PaymentMethod.CREDIT
        reference = reference.strip() or f"SALDO-{locked_batch.reference}"
    elif method == PaymentMethod.CREDIT:
        raise ValidationError(
            {"method": "Selecciona el método usado para pagar el importe restante."}
        )
    requested_currency = currency or locked_batch.currency
    if requested_currency != locked_batch.currency:
        raise ValidationError({"currency": "La moneda debe coincidir con el grupo."})

    previous_submission = (
        Payment.objects.filter(
            batch=locked_batch,
            status__in=(PaymentStatus.REJECTED, PaymentStatus.CANCELLED),
            is_deleted=False,
        )
        .order_by("-created_at")
        .first()
    )
    payment = Payment(
        batch=locked_batch,
        tenant_doctor=locked_batch.tenant_doctor,
        amount=amount,
        currency=requested_currency,
        method=method,
        reference=reference,
        payment_date=payment_date or timezone.localdate(),
        status=PaymentStatus.REGISTERED,
        notes=notes,
    )
    if receipt:
        payment.receipt = receipt
    _set_audit_users(payment, actor, created=True)
    payment.save()
    _reserve_tenant_credit(
        payment=payment,
        amount=credit_amount,
        actor=actor,
    )

    for reservation in payable:
        statement = statements[reservation.pk]
        allocation = _create_allocation(
            payment=payment,
            reservation=reservation,
            statement=statement,
            amount=statement.total_doctor,
            actor=actor,
        )
        if reservation.status == ReservationStatus.REQUESTED:
            reservation.status = ReservationStatus.PENDING_PAYMENT
            _set_audit_users(reservation, actor)
            reservation.save(update_fields=["status", "updated_by", "updated_at"])
        record_event(
            event_type="reservation.payment_proof_submitted",
            object_label=str(reservation),
            actor=actor,
            payload={
                **_allocation_payload(allocation, actor=actor),
                "batch_id": str(locked_batch.pk),
            },
        )

    locked_batch.payment_proof_submitted_at = now
    _set_audit_users(locked_batch, actor)
    locked_batch.save(
        update_fields=["payment_proof_submitted_at", "updated_by", "updated_at"]
    )
    event_type = (
        "payment.replacement_submitted"
        if previous_submission is not None
        else "payment.submitted"
    )
    payload = _payment_payload(payment, actor=actor)
    if previous_submission is not None:
        payload["replaces_payment_id"] = str(previous_submission.pk)
    record_event(
        event_type=event_type,
        object_label=str(payment),
        actor=actor,
        payload=payload,
    )
    record_event(
        event_type="reservation_batch.payment_proof_submitted",
        object_label=str(locked_batch),
        actor=actor,
        payload={
            **payload,
            "batch_id": str(locked_batch.pk),
            "required_total": str(required_total),
            "allocation_count": str(len(payable)),
        },
    )
    return payment


@transaction.atomic
def validate_payment(
    *,
    payment: Payment,
    actor: Model | None = None,
) -> Payment:
    locked_payment = (
        Payment.objects.select_for_update()
        .select_related(
            "batch",
            "batch__room",
            "batch__room__owner",
            "batch__tenant_doctor",
            "batch__tenant_doctor__user",
            "reservation",
            "statement",
        )
        .get(pk=payment.pk, is_deleted=False)
    )
    if locked_payment.status != PaymentStatus.REGISTERED:
        raise ValidationError({"status": "Sólo se puede validar un pago registrado."})

    if locked_payment.batch_id and locked_payment.reservation_id is None:
        _validate_batch_payment(locked_payment, actor=actor)
    else:
        locked_payment.status = PaymentStatus.VALIDATED
        locked_payment.validated_at = timezone.now()
        if actor is not None:
            locked_payment.validated_by = cast(Any, actor)
        _set_audit_users(locked_payment, actor)
        locked_payment.save()
        record_event(
            event_type="payment.validated",
            object_label=str(locked_payment),
            actor=actor,
            payload=_payment_payload(locked_payment, actor=actor),
        )
        if locked_payment.reservation is None:
            raise ValidationError(
                {"reservation": "El pago no tiene una reservación asociada."}
            )
        _mark_reservation_paid_if_covered(locked_payment.reservation, actor=actor)

    _copy_payment_state(payment, locked_payment)
    return locked_payment


def _validate_batch_payment(payment: Payment, *, actor: Model | None) -> None:
    batch = payment.batch
    if batch is None:
        raise ValidationError({"batch": "El pago no tiene un grupo asociado."})
    if batch.status not in {
        ReservationBatchStatus.REQUESTED,
        ReservationBatchStatus.PARTIALLY_CANCELLED,
    }:
        raise ValidationError({"batch": "El grupo ya no puede confirmarse."})

    payable = _payable_reservations(batch, lock=True)
    allocations = list(
        PaymentAllocation.objects.select_for_update()
        .filter(payment=payment, is_deleted=False)
        .select_related("reservation", "statement")
        .order_by("reservation__date", "reservation__start_time")
    )
    allocation_by_reservation = {item.reservation_id: item for item in allocations}
    if set(allocation_by_reservation) != {item.pk for item in payable}:
        raise ValidationError(
            {
                "allocations": (
                    "Las asignaciones no cubren exactamente las reservaciones "
                    "pendientes del grupo."
                )
            }
        )

    allocation_total = Decimal("0.00")
    for reservation in payable:
        allocation = allocation_by_reservation[reservation.pk]
        statement = _current_statement_for_reservation(reservation)
        if allocation.statement_id != statement.pk:
            raise ValidationError(
                {"allocations": "Una asignación usa un estado de cuenta no vigente."}
            )
        if allocation.amount != statement.total_doctor:
            raise ValidationError(
                {
                    "allocations": (
                        "Cada asignación debe cubrir el total vigente de su "
                        "reservación."
                    )
                }
            )
        allocation_total += allocation.amount
    if payment.amount < allocation_total:
        raise ValidationError(
            {"amount": "El importe del pago no cubre todas las asignaciones."}
        )

    payment.status = PaymentStatus.VALIDATED
    payment.validated_at = timezone.now()
    if actor is not None:
        payment.validated_by = cast(Any, actor)
    _set_audit_users(payment, actor)
    payment.save()
    _apply_reserved_credit(payment, actor=actor)
    record_event(
        event_type="payment.validated",
        object_label=str(payment),
        actor=actor,
        payload={
            **_payment_payload(payment, actor=actor),
            "allocation_total": str(allocation_total),
            "allocation_count": str(len(allocations)),
        },
    )

    for reservation in payable:
        confirm_reservation(reservation=reservation, actor=actor)
        generate_settlement_for_reservation(reservation=reservation, actor=actor)

    batch.refresh_from_db()
    record_event(
        event_type="reservation_batch.payment_validated",
        object_label=str(batch),
        actor=actor,
        payload={
            **_payment_payload(payment, actor=actor),
            "batch_id": str(batch.pk),
            "batch_status": batch.status,
        },
    )
    transaction.on_commit(lambda: _send_batch_confirmation_email(batch, payable))


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
    locked_payment = (
        Payment.objects.select_for_update()
        .select_related("batch")
        .get(
            pk=payment.pk,
            is_deleted=False,
        )
    )
    if locked_payment.status == PaymentStatus.VALIDATED:
        raise ValidationError({"status": "No se puede rechazar un pago validado."})
    if locked_payment.status == PaymentStatus.CANCELLED:
        raise ValidationError({"status": "No se puede rechazar un pago cancelado."})

    locked_payment.status = PaymentStatus.REJECTED
    locked_payment.rejected_reason = reason
    _set_audit_users(locked_payment, actor)
    locked_payment.save()
    _release_reserved_credit(locked_payment, actor=actor)
    _reset_batch_after_unsuccessful_submission(locked_payment, actor=actor)

    payload = {**_payment_payload(locked_payment, actor=actor), "reason": reason}
    record_event(
        event_type="payment.rejected",
        object_label=str(locked_payment),
        actor=actor,
        payload=payload,
    )
    if locked_payment.batch is not None and locked_payment.reservation_id is None:
        record_event(
            event_type="reservation_batch.payment_rejected",
            object_label=str(locked_payment.batch),
            actor=actor,
            payload=payload,
        )
    _copy_payment_state(payment, locked_payment)
    return locked_payment


@transaction.atomic
def cancel_payment(
    *,
    payment: Payment,
    actor: Model | None = None,
) -> Payment:
    locked_payment = (
        Payment.objects.select_for_update()
        .select_related("batch")
        .get(
            pk=payment.pk,
            is_deleted=False,
        )
    )
    if locked_payment.status == PaymentStatus.VALIDATED:
        raise ValidationError({"status": "No se puede cancelar un pago validado."})

    locked_payment.status = PaymentStatus.CANCELLED
    _set_audit_users(locked_payment, actor)
    locked_payment.save()
    _release_reserved_credit(locked_payment, actor=actor)
    _reset_batch_after_unsuccessful_submission(locked_payment, actor=actor)

    record_event(
        event_type="payment.cancelled",
        object_label=str(locked_payment),
        actor=actor,
        payload=_payment_payload(locked_payment, actor=actor),
    )
    _copy_payment_state(payment, locked_payment)
    return locked_payment


def get_payment_summary_for_reservation(reservation: Reservation) -> PaymentSummary:
    statement = _current_statement_for_reservation(reservation)
    payments = (
        Payment.objects.filter(
            Q(reservation=reservation) | Q(allocations__reservation=reservation),
            is_deleted=False,
        )
        .select_related("batch", "statement", "tenant_doctor", "validated_by")
        .distinct()
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


def get_payment_summary_for_batch(batch: ReservationBatch) -> BatchPaymentSummary:
    payments = (
        Payment.objects.filter(batch=batch, is_deleted=False)
        .select_related("tenant_doctor", "validated_by")
        .prefetch_related("allocations")
        .order_by("-created_at")
    )
    total_submitted = payments.filter(
        status__in=(PaymentStatus.REGISTERED, PaymentStatus.VALIDATED)
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total_validated = payments.filter(status=PaymentStatus.VALIDATED).aggregate(
        total=Sum("allocations__amount")
    )["total"] or Decimal("0.00")
    total_to_pay = _batch_current_total(batch)
    return BatchPaymentSummary(
        batch=batch,
        total_to_pay=total_to_pay,
        total_submitted=total_submitted,
        total_validated=total_validated,
        pending_balance=max(total_to_pay - total_validated, Decimal("0.00")),
        currency=batch.currency,
        payments=payments,
    )


def _reset_batch_after_unsuccessful_submission(
    payment: Payment,
    *,
    actor: Model | None,
) -> None:
    batch = payment.batch
    if batch is None or payment.reservation_id is not None:
        return
    if (
        Payment.objects.filter(
            batch=batch,
            status__in=(PaymentStatus.REGISTERED, PaymentStatus.VALIDATED),
            is_deleted=False,
        )
        .exclude(pk=payment.pk)
        .exists()
    ):
        return

    batch.payment_proof_submitted_at = None
    _set_audit_users(batch, actor)
    batch.save(update_fields=["payment_proof_submitted_at", "updated_by", "updated_at"])
    reservations = batch.reservations.filter(
        status=ReservationStatus.PENDING_PAYMENT,
        is_deleted=False,
    )
    for reservation in reservations:
        reservation.status = ReservationStatus.REQUESTED
        _set_audit_users(reservation, actor)
        reservation.save(update_fields=["status", "updated_by", "updated_at"])
        record_event(
            event_type="reservation.payment_proof_released",
            object_label=str(reservation),
            actor=actor,
            payload={
                "model": reservation._meta.label,
                "id": str(reservation.pk),
                "batch_id": str(batch.pk),
                "payment_id": str(payment.pk),
                "level": "financiero",
            },
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
    _set_audit_users(reservation, actor)
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


def _payable_reservations(
    batch: ReservationBatch,
    *,
    lock: bool,
) -> list[Reservation]:
    queryset = batch.reservations.filter(
        status__in=(ReservationStatus.REQUESTED, ReservationStatus.PENDING_PAYMENT),
        is_deleted=False,
    ).select_related("room", "room__owner", "tenant_doctor", "tenant_doctor__user")
    if lock:
        queryset = queryset.select_for_update()
    return list(queryset.order_by("date", "start_time"))


def _current_statement_for_reservation(reservation: Reservation) -> Statement:
    statement = (
        reservation.statements.filter(
            status=StatementStatus.CURRENT,
            is_deleted=False,
        )
        .order_by("-version")
        .first()
    )
    if statement is None:
        raise ValidationError(
            {"statement": "La reservación no tiene estado de cuenta vigente."}
        )
    return statement


def _batch_current_total(batch: ReservationBatch) -> Decimal:
    return Statement.objects.filter(
        reservation__batch=batch,
        reservation__status__in=(
            ReservationStatus.REQUESTED,
            ReservationStatus.PENDING_PAYMENT,
            ReservationStatus.PAID,
            ReservationStatus.CONFIRMED,
        ),
        status=StatementStatus.CURRENT,
        is_deleted=False,
    ).aggregate(total=Sum("total_doctor"))["total"] or Decimal("0.00")


def _validated_total_for_statement(*, statement_id: Any) -> Decimal:
    allocation_total = PaymentAllocation.objects.filter(
        statement_id=statement_id,
        payment__status=PaymentStatus.VALIDATED,
        payment__is_deleted=False,
        is_deleted=False,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    legacy_total = Payment.objects.filter(
        statement_id=statement_id,
        status=PaymentStatus.VALIDATED,
        is_deleted=False,
        allocations__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return allocation_total + legacy_total


def _create_allocation(
    *,
    payment: Payment,
    reservation: Reservation,
    statement: Statement,
    amount: Decimal,
    actor: Model | None,
) -> PaymentAllocation:
    allocation = PaymentAllocation(
        payment=payment,
        reservation=reservation,
        statement=statement,
        amount=amount,
    )
    _set_audit_users(allocation, actor, created=True)
    allocation.save()
    return allocation


def _reserve_tenant_credit(
    *,
    payment: Payment,
    amount: Decimal,
    actor: Model | None,
) -> None:
    remaining = amount
    if remaining <= Decimal("0.00"):
        return
    credits = TenantCredit.objects.select_for_update().filter(
        tenant_doctor=payment.tenant_doctor,
        currency=payment.currency,
        status=TenantCreditStatus.ACTIVE,
        remaining_amount__gt=0,
        is_deleted=False,
    ).order_by("created_at", "pk")
    for credit in credits:
        applied_amount = min(credit.remaining_amount, remaining)
        credit.remaining_amount -= applied_amount
        credit.status = (
            TenantCreditStatus.EXHAUSTED
            if credit.remaining_amount == Decimal("0.00")
            else TenantCreditStatus.ACTIVE
        )
        _set_audit_users(credit, actor)
        credit.save()
        application = TenantCreditApplication(
            credit=credit,
            payment=payment,
            amount=applied_amount,
            status=TenantCreditApplicationStatus.RESERVED,
        )
        _set_audit_users(application, actor, created=True)
        application.save()
        record_event(
            event_type="tenant_credit.reserved",
            object_label=str(application),
            actor=actor,
            payload=_credit_application_payload(application, actor=actor),
        )
        remaining -= applied_amount
        if remaining == Decimal("0.00"):
            break
    if remaining > Decimal("0.00"):
        raise ValidationError(
            {"credit_amount": "El saldo a favor disponible es insuficiente."}
        )


def _apply_reserved_credit(payment: Payment, *, actor: Model | None) -> None:
    applications = TenantCreditApplication.objects.select_for_update().filter(
        payment=payment,
        status=TenantCreditApplicationStatus.RESERVED,
        is_deleted=False,
    )
    now = timezone.now()
    for application in applications:
        application.status = TenantCreditApplicationStatus.APPLIED
        application.applied_at = now
        _set_audit_users(application, actor)
        application.save()
        record_event(
            event_type="tenant_credit.applied",
            object_label=str(application),
            actor=actor,
            payload=_credit_application_payload(application, actor=actor),
        )


def _release_reserved_credit(payment: Payment, *, actor: Model | None) -> None:
    applications = (
        TenantCreditApplication.objects.select_for_update()
        .filter(
            payment=payment,
            status=TenantCreditApplicationStatus.RESERVED,
            is_deleted=False,
        )
        .select_related("credit")
    )
    now = timezone.now()
    for application in applications:
        credit = TenantCredit.objects.select_for_update().get(pk=application.credit_id)
        credit.remaining_amount += application.amount
        credit.status = TenantCreditStatus.ACTIVE
        _set_audit_users(credit, actor)
        credit.save()
        application.status = TenantCreditApplicationStatus.RELEASED
        application.released_at = now
        _set_audit_users(application, actor)
        application.save()
        record_event(
            event_type="tenant_credit.released",
            object_label=str(application),
            actor=actor,
            payload=_credit_application_payload(application, actor=actor),
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


def _copy_payment_state(target: Payment, source: Payment) -> None:
    target.status = source.status
    target.validated_at = source.validated_at
    target.validated_by = source.validated_by
    target.rejected_reason = source.rejected_reason


def _send_batch_confirmation_email(
    batch: ReservationBatch,
    reservations: list[Reservation],
) -> None:
    owner = batch.room.owner
    recipients = [
        batch.tenant_doctor.user.email,
        owner.user.email if owner else "",
    ]
    recipient_list = list(dict.fromkeys(email for email in recipients if email))
    if not recipient_list or not reservations:
        return
    first = reservations[0]
    last = reservations[-1]
    room_label = batch.room.number.strip() or batch.room.name
    if len(reservations) == 1:
        message = (
            f"Estimado Dr. {batch.tenant_doctor}, su reservación ha quedado "
            f"confirmada para el día {first.date:%d/%m/%Y} de las "
            f"{first.start_time:%H:%M} hasta las {first.end_time:%H:%M}."
        )
        subject = f"Consultorio {room_label}, reservación confirmada"
    else:
        message = (
            f"Estimado Dr. {batch.tenant_doctor}, sus {len(reservations)} "
            f"reservaciones han quedado confirmadas del {first.date:%d/%m/%Y} "
            f"al {last.date:%d/%m/%Y}, de las {first.start_time:%H:%M} hasta "
            f"las {first.end_time:%H:%M}. Referencia: {batch.reference}."
        )
        subject = f"Consultorio {room_label}, reservaciones confirmadas"
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
    )


def _payment_payload(payment: Payment, *, actor: Model | None) -> dict[str, str]:
    credit_amount = payment.credit_applications.filter(
        status__in=(
            TenantCreditApplicationStatus.RESERVED,
            TenantCreditApplicationStatus.APPLIED,
        ),
        is_deleted=False,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    payload = {
        "model": payment._meta.label,
        "id": str(payment.pk),
        "level": "financiero",
        "batch_id": str(payment.batch_id or ""),
        "reservation_id": str(payment.reservation_id or ""),
        "statement_id": str(payment.statement_id or ""),
        "tenant_doctor_id": str(payment.tenant_doctor_id),
        "amount": str(payment.amount),
        "currency": payment.currency,
        "method": payment.method,
        "reference": payment.reference,
        "status": payment.status,
        "credit_amount": str(credit_amount),
        "actor_id": str(actor.pk) if actor is not None else "",
    }
    if payment.reservation is not None:
        payload["reservation"] = str(payment.reservation)
    if payment.method == PaymentMethod.CASH and not payment.reference:
        payload["reference"] = ""
    return payload


def _allocation_payload(
    allocation: PaymentAllocation,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": allocation._meta.label,
        "id": str(allocation.pk),
        "payment_id": str(allocation.payment_id),
        "reservation_id": str(allocation.reservation_id),
        "statement_id": str(allocation.statement_id),
        "amount": str(allocation.amount),
        "level": "financiero",
        "actor_id": str(actor.pk) if actor is not None else "",
    }


def _credit_application_payload(
    application: TenantCreditApplication,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": application._meta.label,
        "id": str(application.pk),
        "credit_id": str(application.credit_id),
        "payment_id": str(application.payment_id),
        "amount": str(application.amount),
        "status": application.status,
        "level": "financiero",
        "actor_id": str(actor.pk) if actor is not None else "",
    }
