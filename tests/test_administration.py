from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.finance.models import CancellationCase, PaymentMethod
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.identity.models import UserRole
from apps.presentation.forms import AdministrationFilterForm
from apps.presentation.services.administration_service import (
    get_administration_overview,
)
from apps.scheduling.models import (
    ReservationDeadlinePolicy,
    ReservationStatus,
)
from tests.test_reservations import create_valid_reservation


def create_user(email: str, role: str) -> Any:
    return get_user_model().objects.create_user(
        email=email,
        password="Segura-12345",
        first_name="Centro",
        last_name="Administración",
        role=role,
    )


@pytest.mark.django_db
def test_administration_marks_reservations_by_deadline_rule() -> None:
    previous_day = create_valid_reservation(room_name="Administración Día")
    hours = create_valid_reservation(room_name="Administración Horas")
    cancelled = create_valid_reservation(room_name="Administración Cancelada")
    previous_day.batch.deadline_policy = ReservationDeadlinePolicy.PREVIOUS_DAY
    previous_day.batch.save(update_fields=["deadline_policy", "updated_at"])
    hours.batch.deadline_policy = ReservationDeadlinePolicy.HOURS_BEFORE
    hours.batch.save(update_fields=["deadline_policy", "updated_at"])
    cancelled.status = ReservationStatus.CANCELLED
    cancelled.save(update_fields=["status", "updated_at"])
    admin = create_user("admin-markers@example.com", UserRole.ADMIN)

    overview = get_administration_overview(filters={}, user=admin)
    items = {item.reservation.pk: item for item in overview.reservations}

    assert items[previous_day.pk].marker == "!"
    assert items[previous_day.pk].row_class == "administration-row-previous-day"
    assert items[hours.pk].marker == "!!"
    assert items[hours.pk].row_class == "administration-row-hours"
    assert items[cancelled.pk].marker == "X"
    assert items[cancelled.pk].row_class == "administration-row-cancelled"


@pytest.mark.django_db
def test_administration_consolidates_pending_actions_without_duplicates() -> None:
    missing_proof = create_valid_reservation(room_name="Administración Sin Pago")
    submitted = create_valid_reservation(room_name="Administración Con Pago")
    payment = register_payment(
        reservation=submitted,
        amount=Decimal("100.00"),
        method=PaymentMethod.CASH,
    )
    CancellationCase.objects.create(
        batch=missing_proof.batch,
        tenant_doctor=missing_proof.tenant_doctor,
        reason="Cancelación pendiente",
    )
    admin = create_user("admin-actions@example.com", UserRole.ADMIN)

    overview = get_administration_overview(filters={}, user=admin)

    assert payment.pk is not None
    assert overview.pending.payment_proofs == 1
    assert overview.pending.reservation_payments == 1
    assert overview.pending.cancellations == 1
    assert overview.pending.total == 3
    assert {action.title for action in overview.actions} == {
        "Comprobante pendiente",
        "Validar comprobante de reservación",
        "Resolver cancelación",
    }


@pytest.mark.django_db
def test_administration_financial_summary_uses_selected_reservations() -> None:
    reservation = create_valid_reservation(room_name="Administración Finanzas")
    other = create_valid_reservation(room_name="Administración Fuera")
    payment = register_payment(
        reservation=reservation,
        amount=Decimal("100.00"),
        method=PaymentMethod.CASH,
    )
    validate_payment(payment=payment)
    admin = create_user("admin-finance@example.com", UserRole.ADMIN)

    overview = get_administration_overview(
        filters={"room": reservation.room},
        user=admin,
    )

    assert other.pk is not None
    assert overview.total_reservations == 1
    assert overview.finance.expected_reservation_total == Decimal("375.00")
    assert overview.finance.validated_reservation_payments == Decimal("100.00")
    assert overview.finance.pending_reservation_balance == Decimal("275.00")
    assert overview.finance.calculated_commissions == Decimal("37.50")
    assert overview.finance.owner_net == Decimal("337.50")


@pytest.mark.django_db
def test_administration_respects_business_admin_clinic_scope() -> None:
    visible = create_valid_reservation(room_name="Administración Visible")
    hidden = create_valid_reservation(room_name="Administración Oculta")
    admin = create_user("admin-scope@example.com", UserRole.ADMIN)
    admin.assigned_clinics.add(visible.room.clinic)

    overview = get_administration_overview(filters={}, user=admin)
    reservation_ids = {item.reservation.pk for item in overview.reservations}

    assert visible.pk in reservation_ids
    assert hidden.pk not in reservation_ids


@pytest.mark.django_db
def test_administration_filter_limits_rooms_to_selected_clinic() -> None:
    visible = create_valid_reservation(room_name="Administración Filtro")
    hidden = create_valid_reservation(room_name="Administración Otro Filtro")
    admin = create_user("admin-filter@example.com", UserRole.ADMIN)

    form = AdministrationFilterForm(
        {"clinic": str(visible.room.clinic_id)},
        user=admin,
    )

    assert form.is_valid()
    room_ids = set(form.fields["room"].queryset.values_list("pk", flat=True))
    assert visible.room_id in room_ids
    assert hidden.room_id not in room_ids


@pytest.mark.django_db
def test_administration_screen_is_available_only_to_administrators(
    client: Any,
) -> None:
    reservation = create_valid_reservation(room_name="Administración Pantalla")
    admin = create_user("admin-screen@example.com", UserRole.ADMIN)
    admin.assigned_clinics.add(reservation.room.clinic)
    client.force_login(admin)

    response = client.get(reverse("administration"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Centro de administración" in content
    assert "Acciones pendientes" in content
    assert "Balance del periodo" in content
    assert "Regla de horas" in content

    tenant = create_user("tenant-screen@example.com", UserRole.TENANT_DOCTOR)
    client.force_login(tenant)
    forbidden = client.get(reverse("administration"))
    assert forbidden.status_code == 403
