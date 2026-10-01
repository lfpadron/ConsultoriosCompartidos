from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.billing.models import CancellationPenaltyRule, CancellationPolicy
from apps.finance.models import (
    CancellationCaseStatus,
    CancellationResolutionMethod,
    PaymentMethod,
    PaymentStatus,
    SettlementStatus,
    TenantCreditApplicationStatus,
    TenantCreditStatus,
)
from apps.finance.services.cancellation_service import (
    calculate_cancellation_quote,
    create_cancellation_case,
    resolve_with_future_credit,
    resolve_with_manual_refund,
)
from apps.finance.services.payment_service import (
    reject_payment,
    submit_batch_payment,
    validate_payment,
)
from apps.identity.models import UserRole
from apps.scheduling.models import ReservationBatchStatus, ReservationStatus
from apps.scheduling.services.reservation_service import create_reservation_batch
from tests.test_group_payments import receipt
from tests.test_reservations import (
    create_availability,
    create_rate,
    create_room,
    create_tenant_doctor,
    create_user,
    future_monday,
)


def create_cancellable_batch(prefix: str = "Cancelación") -> Any:
    room = create_room(prefix)
    tenant = create_tenant_doctor(
        f"{prefix.lower().replace(' ', '-')}@tenant-cancel.example.com"
    )
    tenant.assigned_rooms.add(room)
    create_availability(room)
    create_rate(room)
    policy = CancellationPolicy.objects.create(
        clinic=room.clinic,
        room=room,
        name=f"Política {prefix}",
        start_date=timezone.localdate() - timedelta(days=30),
    )
    CancellationPenaltyRule.objects.create(
        policy=policy,
        days_before=0,
        percentage=Decimal("100.0"),
    )
    CancellationPenaltyRule.objects.create(
        policy=policy,
        days_before=3,
        percentage=Decimal("25.0"),
    )
    start = future_monday()
    return create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=start,
        start_time=time(8, 0),
        end_time=time(13, 0),
        recurrence_end_date=start + timedelta(days=14),
        actor=tenant.user,
    )


def pay_and_validate(batch: Any, admin: Any) -> Any:
    payment = submit_batch_payment(
        batch=batch,
        amount=batch.tariff_final,
        currency=batch.currency,
        method=PaymentMethod.TRANSFER,
        reference="SPEI-CANCEL-001",
        receipt=receipt("cancelacion.pdf"),
        actor=batch.tenant_doctor.user,
    )
    return validate_payment(payment=payment, actor=admin)


def cancellation_time(reservation: Any, days_before: int) -> datetime:
    return datetime.combine(
        reservation.date - timedelta(days=days_before),
        time(12, 0),
        tzinfo=ZoneInfo(reservation.room.clinic.timezone),
    )


@pytest.mark.django_db
def test_quote_uses_snapshot_penalty_threshold() -> None:
    batch = create_cancellable_batch("Umbral")
    reservation = batch.reservations.order_by("date").first()
    assert reservation is not None

    quote = calculate_cancellation_quote(
        reservations=[reservation],
        as_of=cancellation_time(reservation, 2),
    )

    assert quote.items[0].penalty_percentage == Decimal("25.0")
    assert quote.total_paid == Decimal("0.00")


@pytest.mark.django_db
def test_partial_recurring_cancellation_creates_financial_case(
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_cancellable_batch("Parcial")
    admin = create_user("admin-cancel-partial@example.com")
    admin.role = UserRole.ADMIN
    admin.save(update_fields=["role"])
    pay_and_validate(batch, admin)
    reservation = batch.reservations.order_by("date").first()
    assert reservation is not None

    case = create_cancellation_case(
        reservations=[reservation],
        reason="Cambio de agenda",
        actor=batch.tenant_doctor.user,
        as_of=cancellation_time(reservation, 2),
    )

    reservation.refresh_from_db()
    batch.refresh_from_db()
    item = case.items.get()
    assert reservation.status == ReservationStatus.CANCELLED
    assert batch.status == ReservationBatchStatus.PARTIALLY_CANCELLED
    assert case.status == CancellationCaseStatus.PENDING
    assert item.paid_amount == Decimal("375.00")
    assert item.penalty_amount == Decimal("93.75")
    assert item.refundable_amount == Decimal("281.25")
    assert reservation.settlements.get().status == SettlementStatus.CANCELLED
    assert TraceEvent.objects.filter(event_type="cancellation.case_created").exists()


@pytest.mark.django_db
def test_manual_refund_requires_proof_and_closes_case(
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_cancellable_batch("Devolución")
    admin = create_user("admin-refund@example.com")
    admin.role = UserRole.ADMIN
    admin.save(update_fields=["role"])
    pay_and_validate(batch, admin)
    reservation = batch.reservations.order_by("date").first()
    assert reservation is not None
    case = create_cancellation_case(
        reservations=[reservation],
        reason="Devolución manual",
        actor=admin,
        as_of=cancellation_time(reservation, 4),
    )

    with pytest.raises(ValidationError, match="comprobante"):
        resolve_with_manual_refund(
            cancellation_case=case,
            reference="DEV-001",
            refund_date=reservation.date,
            receipt=None,
            actor=admin,
        )

    result = resolve_with_manual_refund(
        cancellation_case=case,
        reference="DEV-001",
        refund_date=reservation.date,
        receipt=SimpleUploadedFile("devolucion.pdf", b"transferencia"),
        actor=admin,
    )

    assert result.status == CancellationCaseStatus.RESOLVED
    assert result.resolution_method == CancellationResolutionMethod.MANUAL_REFUND
    assert result.refund_receipt.name
    assert TraceEvent.objects.filter(
        event_type="cancellation.refund_completed"
    ).exists()


@pytest.mark.django_db
def test_credit_is_reserved_released_and_applied(
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_cancellable_batch("Saldo")
    admin = create_user("admin-credit@example.com")
    admin.role = UserRole.ADMIN
    admin.save(update_fields=["role"])
    pay_and_validate(batch, admin)
    reservation = batch.reservations.order_by("date").first()
    assert reservation is not None
    case = create_cancellation_case(
        reservations=[reservation],
        reason="Aplicar en fecha futura",
        actor=admin,
        as_of=cancellation_time(reservation, 4),
    )
    _case, credit = resolve_with_future_credit(
        cancellation_case=case,
        actor=admin,
    )

    new_start = batch.reservations.order_by("-date").first().date + timedelta(days=7)
    next_batch = create_reservation_batch(
        room=batch.room,
        tenant_doctor=batch.tenant_doctor,
        start_date=new_start,
        start_time=time(8, 0),
        end_time=time(13, 0),
        actor=batch.tenant_doctor.user,
    )
    payment = submit_batch_payment(
        batch=next_batch,
        amount=next_batch.tariff_final,
        currency=next_batch.currency,
        method=PaymentMethod.TRANSFER,
        reference="",
        receipt=None,
        credit_amount=next_batch.tariff_final,
        actor=batch.tenant_doctor.user,
    )
    credit.refresh_from_db()
    application = payment.credit_applications.get()
    assert credit.status == TenantCreditStatus.EXHAUSTED
    assert application.status == TenantCreditApplicationStatus.RESERVED
    assert payment.method == PaymentMethod.CREDIT

    reject_payment(payment=payment, reason="Revisión administrativa", actor=admin)
    credit.refresh_from_db()
    application.refresh_from_db()
    assert credit.remaining_amount == credit.original_amount
    assert application.status == TenantCreditApplicationStatus.RELEASED

    replacement = submit_batch_payment(
        batch=next_batch,
        amount=next_batch.tariff_final,
        currency=next_batch.currency,
        method=PaymentMethod.CREDIT,
        reference="",
        receipt=None,
        credit_amount=next_batch.tariff_final,
        actor=batch.tenant_doctor.user,
    )
    validate_payment(payment=replacement, actor=admin)
    credit.refresh_from_db()
    replacement.refresh_from_db()
    assert replacement.status == PaymentStatus.VALIDATED
    assert replacement.credit_applications.get().status == (
        TenantCreditApplicationStatus.APPLIED
    )
    assert credit.remaining_amount == Decimal("0.00")
    assert next_batch.reservations.get().status == ReservationStatus.CONFIRMED


@pytest.mark.django_db
def test_single_reservation_cannot_be_resolved_as_credit(
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    recurring_batch = create_cancellable_batch("Única")
    start = recurring_batch.reservations.order_by("-date").first().date + timedelta(
        days=14
    )
    batch = create_reservation_batch(
        room=recurring_batch.room,
        tenant_doctor=recurring_batch.tenant_doctor,
        start_date=start,
        start_time=time(8, 0),
        end_time=time(13, 0),
        actor=recurring_batch.tenant_doctor.user,
    )
    admin = create_user("admin-single-credit@example.com")
    admin.role = UserRole.ADMIN
    admin.save(update_fields=["role"])
    pay_and_validate(batch, admin)
    reservation = batch.reservations.get()
    case = create_cancellation_case(
        reservations=[reservation],
        reason="Cancelar reserva única",
        actor=admin,
        as_of=cancellation_time(reservation, 4),
    )

    with pytest.raises(ValidationError, match="repetitivas"):
        resolve_with_future_credit(cancellation_case=case, actor=admin)


@pytest.mark.django_db
def test_tenant_can_cancel_selected_date_but_cannot_resolve_refund(
    client: Any,
) -> None:
    batch = create_cancellable_batch("Pantalla")
    reservation = batch.reservations.order_by("date").first()
    assert reservation is not None
    client.force_login(batch.tenant_doctor.user)

    form_url = reverse("reservation_batch_cancel", kwargs={"pk": batch.pk})
    assert client.get(form_url).status_code == 200
    response = client.post(
        form_url,
        {
            "reservations": [str(reservation.pk)],
            "reason": "Cambio desde la pantalla",
        },
    )

    case = batch.cancellation_cases.get()
    assert response.status_code == 302
    assert client.get(
        reverse("reservation_cancellation_detail", kwargs={"pk": case.pk})
    ).status_code == 200
    assert client.post(
        reverse("reservation_cancellation_refund", kwargs={"pk": case.pk})
    ).status_code == 403
