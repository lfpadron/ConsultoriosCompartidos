from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.astrotrace.models import TraceEvent
from apps.finance.models import Payment, PaymentMethod, PaymentStatus
from apps.finance.services.payment_service import (
    cancel_payment,
    get_payment_summary_for_reservation,
    register_payment,
    reject_payment,
    validate_payment,
)
from apps.scheduling.models import ReservationStatus
from apps.scheduling.services.reservation_service import cancel_reservation
from tests.test_reservations import create_user, create_valid_reservation


def current_statement_for(reservation: Any) -> Any:
    return reservation.statements.get()


def create_registered_payment(
    reservation: Any,
    *,
    amount: Decimal = Decimal("100.00"),
    reference: str = "SPEI-123",
) -> Payment:
    return register_payment(
        reservation=reservation,
        amount=amount,
        method=PaymentMethod.TRANSFER,
        reference=reference,
    )


@pytest.mark.django_db
def test_valid_transfer_payment_with_reference() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Transfer")
    statement = current_statement_for(reservation)

    payment = Payment.objects.create(
        reservation=reservation,
        statement=statement,
        tenant_doctor=reservation.tenant_doctor,
        amount=Decimal("100.00"),
        currency="MXN",
        method=PaymentMethod.TRANSFER,
        reference="SPEI-OK",
    )

    assert payment.status == PaymentStatus.REGISTERED


@pytest.mark.django_db
def test_cash_payment_without_reference_is_allowed() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Efectivo")
    statement = current_statement_for(reservation)

    payment = Payment.objects.create(
        reservation=reservation,
        statement=statement,
        tenant_doctor=reservation.tenant_doctor,
        amount=Decimal("100.00"),
        currency="MXN",
        method=PaymentMethod.CASH,
        reference="",
    )

    assert payment.method == PaymentMethod.CASH


@pytest.mark.django_db
def test_payment_rejects_zero_amount() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Cero")
    statement = current_statement_for(reservation)

    with pytest.raises(ValidationError):
        Payment.objects.create(
            reservation=reservation,
            statement=statement,
            tenant_doctor=reservation.tenant_doctor,
            amount=Decimal("0.00"),
            currency="MXN",
            method=PaymentMethod.TRANSFER,
            reference="SPEI-CERO",
        )


@pytest.mark.django_db
def test_payment_rejects_cancelled_reservation() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Cancelada")
    statement = current_statement_for(reservation)
    cancel_reservation(reservation=reservation, reason="Cancelada")
    reservation.refresh_from_db()

    with pytest.raises(ValidationError):
        Payment.objects.create(
            reservation=reservation,
            statement=statement,
            tenant_doctor=reservation.tenant_doctor,
            amount=Decimal("100.00"),
            currency="MXN",
            method=PaymentMethod.TRANSFER,
            reference="SPEI-CANCELADA",
        )


@pytest.mark.django_db
def test_register_payment() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Registrar Pago")

    payment = create_registered_payment(reservation)

    assert payment.status == PaymentStatus.REGISTERED
    assert payment.reservation == reservation
    assert TraceEvent.objects.filter(event_type="payment.registered").exists()


@pytest.mark.django_db
def test_validate_partial_payment() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Parcial")
    payment = create_registered_payment(reservation, amount=Decimal("100.00"))

    validate_payment(payment=payment)
    reservation.refresh_from_db()
    summary = get_payment_summary_for_reservation(reservation)

    assert payment.status == PaymentStatus.VALIDATED
    assert reservation.status == ReservationStatus.REQUESTED
    assert summary.total_validated == Decimal("100.00")
    assert summary.pending_balance == Decimal("275.00")


@pytest.mark.django_db
def test_validate_full_payment_marks_reservation_paid() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Completo")
    payment = create_registered_payment(reservation, amount=Decimal("375.00"))

    validate_payment(payment=payment)
    reservation.refresh_from_db()

    assert reservation.status == ReservationStatus.PAID
    assert TraceEvent.objects.filter(event_type="payment.validated").exists()
    assert TraceEvent.objects.filter(event_type="reservation.marked_paid").exists()


@pytest.mark.django_db
def test_validated_payments_cannot_exceed_total() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Excedente")
    first_payment = create_registered_payment(
        reservation,
        amount=Decimal("300.00"),
        reference="SPEI-300",
    )
    second_payment = create_registered_payment(
        reservation,
        amount=Decimal("100.00"),
        reference="SPEI-100",
    )
    validate_payment(payment=first_payment)

    with pytest.raises(ValidationError):
        validate_payment(payment=second_payment)


@pytest.mark.django_db
def test_reject_payment_requires_reason() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Rechazo Motivo")
    payment = create_registered_payment(reservation)

    with pytest.raises(ValidationError):
        reject_payment(payment=payment, reason="")


@pytest.mark.django_db
def test_cancel_registered_payment() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Cancelar")
    payment = create_registered_payment(reservation)

    cancel_payment(payment=payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CANCELLED
    assert TraceEvent.objects.filter(event_type="payment.cancelled").exists()


@pytest.mark.django_db
def test_cannot_cancel_validated_payment() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Validado")
    payment = create_registered_payment(reservation)
    validate_payment(payment=payment)

    with pytest.raises(ValidationError):
        cancel_payment(payment=payment)


@pytest.mark.django_db
def test_cannot_validate_rejected_payment() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Pago Rechazado")
    payment = create_registered_payment(reservation)
    reject_payment(payment=payment, reason="No coincide el comprobante")

    with pytest.raises(ValidationError):
        validate_payment(payment=payment)

    assert TraceEvent.objects.filter(event_type="payment.rejected").exists()


@pytest.mark.django_db
def test_reservation_detail_shows_payment_summary(client: Any) -> None:
    user = create_user("detalle-pagos@example.com")
    reservation = create_valid_reservation(room_name="Consultorio UI Pagos Detalle")
    create_registered_payment(reservation, amount=Decimal("100.00"))
    client.force_login(user)

    response = client.get(f"/reservaciones/{reservation.pk}/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Pagos" in content
    assert "Total a pagar" in content
    assert "Saldo pendiente" in content


@pytest.mark.django_db
def test_payment_list_responds_200(client: Any) -> None:
    user = create_user("listado-pagos@example.com")
    client.force_login(user)

    response = client.get("/pagos/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_register_payment_from_ui(client: Any) -> None:
    user = create_user("registrar-pago-ui@example.com")
    reservation = create_valid_reservation(room_name="Consultorio UI Registrar Pago")
    client.force_login(user)

    response = client.post(
        f"/reservaciones/{reservation.pk}/pagos/nuevo/",
        {
            "amount": "100.00",
            "currency": "MXN",
            "method": PaymentMethod.TRANSFER,
            "reference": "SPEI-UI",
            "payment_date": "2026-06-29",
            "notes": "Pago desde UI",
        },
    )

    assert response.status_code == 302
    assert Payment.objects.filter(reference="SPEI-UI").exists()


@pytest.mark.django_db
def test_validate_payment_from_ui(client: Any) -> None:
    user = create_user("validar-pago-ui@example.com")
    reservation = create_valid_reservation(room_name="Consultorio UI Validar Pago")
    payment = create_registered_payment(reservation, amount=Decimal("375.00"))
    client.force_login(user)

    response = client.post(f"/pagos/{payment.pk}/validar/")

    payment.refresh_from_db()
    reservation.refresh_from_db()
    assert response.status_code == 302
    assert payment.status == PaymentStatus.VALIDATED
    assert reservation.status == ReservationStatus.PAID
