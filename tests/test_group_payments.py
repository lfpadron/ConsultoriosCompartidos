from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.finance.models import (
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentStatus,
    Settlement,
)
from apps.finance.services.payment_service import (
    reject_payment,
    submit_batch_payment,
    validate_payment,
)
from apps.identity.models import UserRole
from apps.scheduling.models import ReservationBatchStatus, ReservationStatus
from apps.scheduling.services.reservation_service import create_reservation_batch
from tests.test_reservations import (
    create_availability,
    create_rate,
    create_room,
    create_tenant_doctor,
    create_user,
)


def create_batch(prefix: str = "Pago agrupado") -> Any:
    room = create_room(prefix)
    tenant = create_tenant_doctor(
        f"{prefix.lower().replace(' ', '-')}@tenant.example.com"
    )
    tenant.assigned_rooms.add(room)
    create_availability(room)
    create_rate(room)
    return create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
        recurrence_end_date=date(2026, 7, 13),
        actor=tenant.user,
    )


def receipt(name: str = "comprobante.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"pago agrupado", content_type="application/pdf")


def submit_payment(batch: Any, *, actor: Any | None = None) -> Payment:
    return submit_batch_payment(
        batch=batch,
        amount=batch.tariff_final,
        currency=batch.currency,
        method=PaymentMethod.TRANSFER,
        reference="SPEI-GRUPO-001",
        receipt=receipt(),
        actor=actor or batch.tenant_doctor.user,
    )


@pytest.mark.django_db
def test_submit_one_receipt_allocates_all_occurrences(
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_batch("Grupo Asignaciones")

    payment = submit_payment(batch)

    batch.refresh_from_db()
    allocations = PaymentAllocation.objects.filter(payment=payment)
    assert payment.batch == batch
    assert payment.reservation is None
    assert allocations.count() == 3
    assert sum((item.amount for item in allocations), Decimal("0.00")) == Decimal(
        "1125.00"
    )
    assert batch.payment_proof_submitted_at is not None
    assert set(batch.reservations.values_list("status", flat=True)) == {
        ReservationStatus.PENDING_PAYMENT
    }
    assert TraceEvent.objects.filter(event_type="payment.submitted").exists()
    assert (
        TraceEvent.objects.filter(
            event_type="reservation.payment_proof_submitted"
        ).count()
        == 3
    )


@pytest.mark.django_db
def test_submit_rejects_amount_below_group_total(tmp_path: Any, settings: Any) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_batch("Grupo Incompleto")

    with pytest.raises(ValidationError, match="cubrir al menos"):
        submit_batch_payment(
            batch=batch,
            amount=Decimal("1000.00"),
            currency="MXN",
            method=PaymentMethod.TRANSFER,
            reference="SPEI-INCOMPLETO",
            receipt=receipt(),
            actor=batch.tenant_doctor.user,
        )

    assert not Payment.objects.filter(batch=batch).exists()


@pytest.mark.django_db
def test_submit_rejects_receipt_after_deadline(tmp_path: Any, settings: Any) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_batch("Grupo Vencido")
    batch.payment_deadline_at = timezone.now() - timedelta(minutes=1)
    batch.save(update_fields=["payment_deadline_at", "updated_at"])

    with pytest.raises(ValidationError, match="fecha límite"):
        submit_payment(batch)


@pytest.mark.django_db
def test_rejected_receipt_can_be_replaced(tmp_path: Any, settings: Any) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_batch("Grupo Reemplazo")
    first = submit_payment(batch)

    reject_payment(
        payment=first,
        reason="El importe del banco no es legible.",
        actor=create_user("admin-rechazo@example.com"),
    )
    batch.refresh_from_db()
    assert batch.payment_proof_submitted_at is None
    assert set(batch.reservations.values_list("status", flat=True)) == {
        ReservationStatus.REQUESTED
    }

    replacement = submit_payment(batch)

    assert replacement.pk != first.pk
    assert replacement.status == PaymentStatus.REGISTERED
    assert TraceEvent.objects.filter(
        event_type="payment.replacement_submitted"
    ).exists()


@pytest.mark.django_db
def test_validate_group_confirms_reservations_and_creates_settlements(
    tmp_path: Any,
    settings: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []
    batch = create_batch("Grupo Validación")
    mail.outbox = []
    payment = submit_payment(batch)
    admin = create_user("admin-validacion@example.com")
    admin.role = UserRole.ADMIN
    admin.save(update_fields=["role"])

    with django_capture_on_commit_callbacks(execute=True):
        validate_payment(payment=payment, actor=admin)

    payment.refresh_from_db()
    batch.refresh_from_db()
    assert payment.status == PaymentStatus.VALIDATED
    assert batch.status == ReservationBatchStatus.CONFIRMED
    assert set(batch.reservations.values_list("status", flat=True)) == {
        ReservationStatus.CONFIRMED
    }
    assert Settlement.objects.filter(reservation__batch=batch).count() == 3
    assert TraceEvent.objects.filter(
        event_type="reservation_batch.payment_validated"
    ).exists()
    assert len(mail.outbox) == 1
    assert "reservaciones confirmadas" in mail.outbox[0].subject
    assert set(mail.outbox[0].to) == {
        batch.tenant_doctor.user.email,
        batch.room.owner.user.email,
    }


@pytest.mark.django_db
def test_validation_is_atomic_when_allocation_is_incomplete(
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_batch("Grupo Atómico")
    payment = submit_payment(batch)
    allocation = PaymentAllocation.objects.filter(payment=payment).first()
    assert allocation is not None
    allocation.delete()

    with pytest.raises(ValidationError, match="no cubren exactamente"):
        validate_payment(payment=payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REGISTERED
    assert set(batch.reservations.values_list("status", flat=True)) == {
        ReservationStatus.PENDING_PAYMENT
    }
    assert not Settlement.objects.filter(reservation__batch=batch).exists()


@pytest.mark.django_db
def test_tenant_can_upload_but_cannot_validate_from_ui(
    client: Any,
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_batch("Grupo UI Arrendatario")
    client.force_login(batch.tenant_doctor.user)

    response = client.post(
        f"/reservaciones/grupos/{batch.pk}/comprobante/",
        {
            "amount": str(batch.tariff_final),
            "currency": batch.currency,
            "method": PaymentMethod.TRANSFER,
            "reference": "SPEI-UI-GRUPO",
            "payment_date": "2026-06-29",
            "receipt": receipt("ui.pdf"),
            "notes": "Pago de las tres fechas",
        },
    )

    payment = Payment.objects.get(batch=batch)
    assert response.status_code == 302
    assert client.post(f"/pagos/{payment.pk}/validar/").status_code == 403


@pytest.mark.django_db
def test_business_admin_can_validate_group_from_ui(
    client: Any,
    tmp_path: Any,
    settings: Any,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    batch = create_batch("Grupo UI Admin")
    payment = submit_payment(batch)
    admin = create_user("admin-ui-grupo@example.com")
    admin.role = UserRole.ADMIN
    admin.save(update_fields=["role"])
    client.force_login(admin)

    response = client.post(f"/pagos/{payment.pk}/validar/")

    payment.refresh_from_db()
    assert response.status_code == 302
    assert payment.status == PaymentStatus.VALIDATED
