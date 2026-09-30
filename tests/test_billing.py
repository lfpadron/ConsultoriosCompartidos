from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.astrotrace.models import TraceEvent
from apps.billing.models import (
    CancellationPenaltyRule,
    CancellationPolicy,
    FeePaymentMode,
    OwnerCommissionRule,
    OwnerSubscription,
    RoomFeeType,
    RoomMonthlyFee,
    SubscriptionCycle,
)
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
)
from apps.identity.models import UserRole


def create_user(email: str, role: str = UserRole.ADMIN) -> Any:
    return get_user_model().objects.create_user(
        email=email,
        password="Segura-12345",
        first_name="Configuración",
        last_name="Comercial",
        role=role,
    )


def create_catalog(prefix: str = "Comercial") -> tuple[
    Clinic,
    OwnerProfile,
    TenantDoctorProfile,
    ConsultingRoom,
]:
    clinic = Clinic.objects.create(name=f"Clínica {prefix}")
    owner = OwnerProfile.objects.create(
        user=create_user(f"owner-{prefix.lower()}@example.com", UserRole.OWNER),
        display_name=f"Propietario {prefix}",
    )
    tenant = TenantDoctorProfile.objects.create(
        user=create_user(
            f"tenant-{prefix.lower()}@example.com", UserRole.TENANT_DOCTOR
        ),
        display_name=f"Arrendatario {prefix}",
    )
    room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name=f"Consultorio {prefix}",
        number="101",
    )
    tenant.assigned_rooms.add(room)
    return clinic, owner, tenant, room


@pytest.mark.django_db
def test_owner_subscription_is_versioned_immutable_and_allows_zero() -> None:
    _, owner, _, _ = create_catalog("Suscripción")
    first = OwnerSubscription.objects.create(
        owner=owner,
        billing_cycle=SubscriptionCycle.MONTHLY,
        amount=Decimal("0.00"),
        start_date=date(2026, 1, 1),
    )

    assert first.version == 1
    first.is_active = False
    first.save()
    second = OwnerSubscription.objects.create(
        owner=owner,
        billing_cycle=SubscriptionCycle.ANNUAL,
        amount=Decimal("1200.00"),
        start_date=date(2026, 2, 1),
    )

    assert second.version == 2
    first.amount = Decimal("1.00")
    with pytest.raises(ValidationError):
        first.save()


@pytest.mark.django_db
def test_active_subscription_validities_cannot_overlap() -> None:
    _, owner, _, _ = create_catalog("Traslape")
    OwnerSubscription.objects.create(
        owner=owner,
        billing_cycle=SubscriptionCycle.MONTHLY,
        amount=Decimal("100.00"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )

    with pytest.raises(ValidationError):
        OwnerSubscription.objects.create(
            owner=owner,
            billing_cycle=SubscriptionCycle.MONTHLY,
            amount=Decimal("150.00"),
            start_date=date(2026, 6, 1),
        )


@pytest.mark.django_db
def test_commission_percentage_accepts_bounds_and_rejects_outside_range() -> None:
    _, owner, _, _ = create_catalog("Comisión")
    zero = OwnerCommissionRule.objects.create(
        owner=owner,
        percentage=Decimal("0.0"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )
    hundred = OwnerCommissionRule.objects.create(
        owner=owner,
        percentage=Decimal("100.0"),
        start_date=date(2026, 2, 1),
    )

    assert zero.percentage == Decimal("0.0")
    assert hundred.percentage == Decimal("100.0")
    with pytest.raises(ValidationError):
        OwnerCommissionRule.objects.create(
            owner=owner,
            percentage=Decimal("100.1"),
            start_date=date(2027, 1, 1),
        )


@pytest.mark.django_db
def test_monthly_fee_normalizes_its_full_month_and_rejects_negative_amount() -> None:
    _, _, _, room = create_catalog("Cuota")
    fee = RoomMonthlyFee.objects.create(
        room=room,
        fee_type=RoomFeeType.ELECTRICITY,
        billing_month=date(2026, 2, 1),
        amount=Decimal("850.40"),
        payment_mode=FeePaymentMode.AUTO_DEDUCT,
    )

    assert fee.start_date == date(2026, 2, 1)
    assert fee.end_date == date(2026, 2, 28)
    with pytest.raises(ValidationError):
        RoomMonthlyFee.objects.create(
            room=room,
            fee_type=RoomFeeType.INTERNET,
            billing_month=date(2026, 3, 1),
            amount=Decimal("-0.10"),
        )


@pytest.mark.django_db
def test_cancellation_policy_creation_records_rules_and_trace_events(
    client: Any,
) -> None:
    admin = create_user("billing-admin@example.com")
    clinic, _, _, room = create_catalog("Política")
    admin.assigned_clinics.add(clinic)
    client.force_login(admin)

    response = client.post(
        reverse("cancellation_policy_create"),
        {
            "clinic": str(clinic.pk),
            "room": str(room.pk),
            "name": "Cancelación estándar",
            "start_date": "2026-01-01",
            "end_date": "",
            "notes": "",
            "penalties-TOTAL_FORMS": "2",
            "penalties-INITIAL_FORMS": "0",
            "penalties-MIN_NUM_FORMS": "0",
            "penalties-MAX_NUM_FORMS": "1000",
            "penalties-0-days_before": "0",
            "penalties-0-percentage": "100.0",
            "penalties-1-days_before": "3",
            "penalties-1-percentage": "25.0",
        },
    )

    policy = CancellationPolicy.objects.get(name="Cancelación estándar")
    assert response.status_code == 302
    assert list(policy.penalty_rules.values_list("days_before", "percentage")) == [
        (0, Decimal("100.0")),
        (3, Decimal("25.0")),
    ]
    assert TraceEvent.objects.filter(
        event_type="billing.cancellation_policy.created",
        payload__id=str(policy.pk),
    ).exists()
    assert (
        TraceEvent.objects.filter(
            event_type="billing.cancellation_penalty_rule.created",
            payload__policy_id=str(policy.pk),
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_cancellation_policy_rejects_increasing_penalty_for_more_notice(
    client: Any,
) -> None:
    admin = create_user("billing-invalid-policy@example.com")
    clinic, _, _, _ = create_catalog("Política inválida")
    admin.assigned_clinics.add(clinic)
    client.force_login(admin)

    response = client.post(
        reverse("cancellation_policy_create"),
        {
            "clinic": str(clinic.pk),
            "room": "",
            "name": "Política inválida",
            "start_date": "2026-01-01",
            "end_date": "",
            "notes": "",
            "penalties-TOTAL_FORMS": "2",
            "penalties-INITIAL_FORMS": "0",
            "penalties-MIN_NUM_FORMS": "0",
            "penalties-MAX_NUM_FORMS": "1000",
            "penalties-0-days_before": "0",
            "penalties-0-percentage": "25.0",
            "penalties-1-days_before": "3",
            "penalties-1-percentage": "50.0",
        },
    )

    assert response.status_code == 200
    assert "no puede aumentar" in response.content.decode()
    assert not CancellationPolicy.objects.filter(name="Política inválida").exists()


@pytest.mark.django_db
def test_subscription_create_and_toggle_are_traced(client: Any) -> None:
    admin = create_user("billing-trace@example.com")
    clinic, owner, _, _ = create_catalog("Auditoría")
    admin.assigned_clinics.add(clinic)
    client.force_login(admin)

    create_response = client.post(
        reverse("owner_subscription_create"),
        {
            "owner": str(owner.pk),
            "billing_cycle": SubscriptionCycle.MONTHLY,
            "amount": "250.00",
            "currency": "MXN",
            "start_date": "2026-01-01",
            "end_date": "",
            "notes": "",
        },
    )
    subscription = OwnerSubscription.objects.get(owner=owner)
    toggle_response = client.post(
        reverse("owner_subscription_toggle", args=[subscription.pk])
    )

    assert create_response.status_code == 302
    assert toggle_response.status_code == 302
    subscription.refresh_from_db()
    assert subscription.is_active is False
    assert TraceEvent.objects.filter(
        event_type="billing.owner_subscription.created"
    ).exists()
    assert TraceEvent.objects.filter(
        event_type="billing.owner_subscription.deactivated"
    ).exists()


@pytest.mark.django_db
def test_billing_screens_are_available_to_admin_and_block_operator(client: Any) -> None:
    admin = create_user("billing-pages@example.com")
    client.force_login(admin)
    route_names = (
        "owner_subscriptions",
        "tenant_subscriptions",
        "owner_terms",
        "room_fees",
        "cancellation_policies",
    )

    for route_name in route_names:
        assert client.get(reverse(route_name)).status_code == 200

    operator = create_user("billing-operator@example.com", UserRole.OPERATOR)
    client.force_login(operator)
    assert client.get(reverse("owner_subscriptions")).status_code == 403


@pytest.mark.django_db
def test_penalty_rule_percentage_validation() -> None:
    clinic, _, _, _ = create_catalog("Penalización")
    policy = CancellationPolicy.objects.create(
        clinic=clinic,
        name="Validación",
        start_date=date(2026, 1, 1),
    )

    with pytest.raises(ValidationError):
        CancellationPenaltyRule.objects.create(
            policy=policy,
            days_before=0,
            percentage=Decimal("100.1"),
        )
