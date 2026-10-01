from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.astrotrace.models import TraceEvent
from apps.billing.models import (
    FeePaymentMode,
    OwnerCommissionRule,
    OwnerPayoutSchedule,
    OwnerSubscription,
    PayoutFrequency,
    RoomFeeType,
    RoomFixedFeeRule,
    RoomMonthlyFee,
    SubscriptionCycle,
    TenantSubscription,
)
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.finance.models import (
    AccountLineType,
    AccountPaymentCategory,
    AccountStatement,
    OwnerPayout,
    PaymentMethod,
    PriceType,
    RateRule,
)
from apps.finance.services.account_statement_service import (
    due_owner_period,
    generate_due_account_statements,
    generate_owner_account_statement,
    generate_tenant_account_statement,
    register_account_payment,
    register_owner_payout,
    validate_account_payment,
)
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.finance.services.settlement_service import generate_settlement_for_reservation
from apps.identity.models import UserRole
from apps.scheduling.models import AvailabilityRule, Weekday
from apps.scheduling.services.reservation_service import (
    confirm_reservation,
    create_reservation,
)


def create_user(email: str, role: str) -> Any:
    return get_user_model().objects.create_user(
        email=email,
        password="Segura-12345",
        first_name="Estado",
        last_name="Cuenta",
        role=role,
    )


def create_commercial_context(prefix: str) -> tuple[
    Clinic,
    OwnerProfile,
    TenantDoctorProfile,
    ConsultingRoom,
]:
    email_token = "".join(
        character
        for character in prefix.lower()
        if character.isascii() and character.isalnum()
    )
    clinic = Clinic.objects.create(name=f"Clínica {prefix}")
    owner = OwnerProfile.objects.create(
        user=create_user(f"owner-{email_token}@example.com", UserRole.OWNER),
        display_name=f"Propietario {prefix}",
    )
    tenant = TenantDoctorProfile.objects.create(
        user=create_user(
            f"tenant-{email_token}@example.com",
            UserRole.TENANT_DOCTOR,
        ),
        display_name=f"Arrendatario {prefix}",
        status=TenantDoctorStatus.AUTHORIZED,
    )
    room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name=f"Consultorio {prefix}",
        number="101",
    )
    tenant.assigned_rooms.add(room)
    return clinic, owner, tenant, room


def create_confirmed_reservation(
    room: ConsultingRoom,
    tenant: TenantDoctorProfile,
) -> Any:
    AvailabilityRule.objects.create(
        room=room,
        name="Disponibilidad junio",
        weekday=Weekday.MONDAY,
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=date(2026, 1, 1),
    )
    RateRule.objects.create(
        room=room,
        name="Tarifa junio",
        weekdays=[Weekday.MONDAY],
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=date(2026, 1, 1),
        price_type=PriceType.HOURLY,
        amount=Decimal("75.00"),
        currency="MXN",
        priority=1,
    )
    reservation = create_reservation(
        room=room,
        tenant_doctor=tenant,
        reservation_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
    )
    return confirm_reservation(reservation=reservation)


def create_owner_statement(prefix: str = "Propietario") -> tuple[Any, Any, Any]:
    _, owner, tenant, room = create_commercial_context(prefix)
    OwnerPayoutSchedule.objects.create(
        owner=owner,
        frequency=PayoutFrequency.MONTHLY,
        start_date=date(2026, 1, 1),
    )
    OwnerCommissionRule.objects.create(
        owner=owner,
        percentage=Decimal("10.0"),
        start_date=date(2026, 1, 1),
    )
    OwnerSubscription.objects.create(
        owner=owner,
        billing_cycle=SubscriptionCycle.MONTHLY,
        amount=Decimal("100.00"),
        start_date=date(2026, 6, 1),
    )
    RoomFixedFeeRule.objects.create(
        room=room,
        fee_type=RoomFeeType.MAINTENANCE,
        amount=Decimal("50.00"),
        payment_mode=FeePaymentMode.DIRECT,
        start_date=date(2026, 6, 10),
    )
    RoomFixedFeeRule.objects.create(
        room=room,
        fee_type=RoomFeeType.ELECTRICITY,
        amount=Decimal("10.00"),
        payment_mode=FeePaymentMode.DIRECT,
        start_date=date(2026, 1, 1),
    )
    RoomMonthlyFee.objects.create(
        room=room,
        fee_type=RoomFeeType.ELECTRICITY,
        billing_month=date(2026, 6, 1),
        amount=Decimal("25.00"),
        payment_mode=FeePaymentMode.AUTO_DEDUCT,
    )
    reservation = create_confirmed_reservation(room, tenant)
    settlement = generate_settlement_for_reservation(reservation=reservation)
    statement = generate_owner_account_statement(
        owner=owner,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        as_of=date(2026, 7, 1),
    )
    return statement, settlement, room


@pytest.mark.parametrize(
    ("frequency", "as_of", "expected"),
    (
        (
            PayoutFrequency.WEEKLY,
            date(2026, 7, 6),
            (date(2026, 6, 29), date(2026, 7, 5)),
        ),
        (
            PayoutFrequency.BIWEEKLY,
            date(2026, 7, 16),
            (date(2026, 7, 1), date(2026, 7, 15)),
        ),
        (
            PayoutFrequency.BIWEEKLY,
            date(2026, 7, 1),
            (date(2026, 6, 16), date(2026, 6, 30)),
        ),
        (
            PayoutFrequency.MONTHLY,
            date(2026, 7, 1),
            (date(2026, 6, 1), date(2026, 6, 30)),
        ),
    ),
)
def test_due_owner_periods(
    frequency: str,
    as_of: date,
    expected: tuple[date, date],
) -> None:
    assert due_owner_period(frequency=frequency, as_of=as_of) == expected


@pytest.mark.django_db
def test_owner_statement_calculates_income_commission_fees_and_balances() -> None:
    statement, settlement, _ = create_owner_statement("Cálculo")

    assert statement.rental_income == settlement.reservation_subtotal
    assert statement.commissions == (
        settlement.platform_commission + settlement.commission_taxes
    )
    assert statement.owner_net == settlement.owner_net
    assert statement.subscription_charges == Decimal("100.00")
    assert statement.direct_fees == Decimal("50.00")
    assert statement.deducted_fees == Decimal("25.00")
    assert statement.balance_due == Decimal("150.00")
    assert statement.payout_due == settlement.owner_net - Decimal("25.00")

    electricity_lines = statement.lines.filter(
        source_snapshot__fee_type=RoomFeeType.ELECTRICITY,
        is_deleted=False,
    )
    assert electricity_lines.count() == 1
    assert electricity_lines.get().amount == Decimal("25.00")
    maintenance = statement.lines.get(
        source_snapshot__fee_type=RoomFeeType.MAINTENANCE,
        is_deleted=False,
    )
    assert maintenance.coverage_start == date(2026, 6, 1)
    assert maintenance.amount == Decimal("50.00")


@pytest.mark.django_db
def test_tenant_statement_includes_reservation_payment_and_subscription() -> None:
    _, _, tenant, room = create_commercial_context("Arrendatario")
    TenantSubscription.objects.create(
        tenant_doctor=tenant,
        billing_cycle=SubscriptionCycle.ANNUAL,
        amount=Decimal("1200.00"),
        start_date=date(2026, 6, 1),
    )
    reservation = create_confirmed_reservation(room, tenant)
    current_statement = reservation.statements.get()
    payment = register_payment(
        reservation=reservation,
        amount=current_statement.total_doctor,
        method=PaymentMethod.CASH,
        payment_date=date(2026, 6, 20),
    )
    validate_payment(payment=payment)

    statement = generate_tenant_account_statement(
        tenant_doctor=tenant,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        as_of=date(2026, 7, 1),
    )

    assert statement.reservation_charges == current_statement.total_doctor
    assert statement.reservation_payments == current_statement.total_doctor
    assert statement.subscription_charges == Decimal("1200.00")
    assert statement.balance_due == Decimal("1200.00")
    subscription_line = statement.lines.get(
        line_type=AccountLineType.TENANT_SUBSCRIPTION,
        is_deleted=False,
    )
    assert subscription_line.coverage_start == date(2026, 6, 1)
    assert subscription_line.coverage_end == date(2027, 5, 31)


@pytest.mark.django_db
def test_validated_owner_payment_reduces_only_owner_balance() -> None:
    statement, _, _ = create_owner_statement("Pago")
    payment = register_account_payment(
        account_statement=statement,
        category=AccountPaymentCategory.OWNER_SUBSCRIPTION,
        amount=Decimal("100.00"),
        method=PaymentMethod.CASH,
        reference="",
        payment_date=date(2026, 7, 2),
    )
    validate_account_payment(payment=payment)
    statement.refresh_from_db()

    assert statement.account_payments_total == Decimal("100.00")
    assert statement.balance_due == Decimal("50.00")
    assert statement.payout_due > Decimal("0.00")
    assert TraceEvent.objects.filter(
        event_type="account_payment.validated",
        object_label=str(payment),
    ).exists()


@pytest.mark.django_db
def test_account_payment_cannot_exceed_statement_balance() -> None:
    statement, _, _ = create_owner_statement("Sobrepago")

    with pytest.raises(ValidationError):
        register_account_payment(
            account_statement=statement,
            category=AccountPaymentCategory.OWNER_FEE,
            amount=statement.balance_due + Decimal("0.01"),
            method=PaymentMethod.CASH,
            reference="",
            payment_date=date(2026, 7, 2),
        )


@pytest.mark.django_db
def test_owner_payout_closes_payout_balance_and_records_receipt() -> None:
    statement, _, _ = create_owner_statement("Transferencia")
    amount = statement.payout_due
    payout = register_owner_payout(
        account_statement=statement,
        amount=amount,
        reference="SPEI-123",
        payment_date=date(2026, 7, 2),
        receipt=SimpleUploadedFile("pago.txt", b"comprobante"),
    )
    statement.refresh_from_db()

    assert payout.deducted_fees == Decimal("25.00")
    assert statement.payouts_made == amount
    assert statement.payout_due == Decimal("0.00")
    assert statement.lines.filter(
        line_type=AccountLineType.OWNER_PAYOUT,
        source_model=OwnerPayout._meta.label,
        source_id=str(payout.pk),
        is_deleted=False,
    ).exists()
    assert TraceEvent.objects.filter(
        event_type="account_statement.owner_payout_registered",
        object_label=str(payout),
    ).exists()


@pytest.mark.django_db
def test_automatic_generation_is_idempotent() -> None:
    _, owner, _, _ = create_commercial_context("Automático")
    OwnerPayoutSchedule.objects.create(
        owner=owner,
        frequency=PayoutFrequency.MONTHLY,
        start_date=date(2026, 1, 1),
    )

    first = generate_due_account_statements(as_of=date(2026, 7, 1))
    second = generate_due_account_statements(as_of=date(2026, 7, 1))

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].pk == second[0].pk
    assert AccountStatement.objects.count() == 1


@pytest.mark.django_db
def test_statement_views_scope_owners_and_protect_payout_action(client: Any) -> None:
    first, _, _ = create_owner_statement("Visible")
    second, _, _ = create_owner_statement("Oculto")
    owner_user = first.owner.user
    client.force_login(owner_user)

    listing = client.get(reverse("statements"))
    own_detail = client.get(reverse("account_statement_detail", args=[first.pk]))
    hidden_detail = client.get(reverse("account_statement_detail", args=[second.pk]))
    payout_action = client.get(
        reverse("account_statement_owner_payout_create", args=[first.pk])
    )

    assert listing.status_code == 200
    assert str(first.owner) in listing.content.decode()
    assert str(second.owner) not in listing.content.decode()
    assert own_detail.status_code == 200
    assert hidden_detail.status_code == 404
    assert payout_action.status_code == 403
