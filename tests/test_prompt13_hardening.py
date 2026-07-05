from datetime import time
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.astrotrace.services.timeline_service import get_timeline_for_reservation
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    Specialty,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.finance.models import (
    Payment,
    PaymentMethod,
    PriceType,
    RateRule,
    Settlement,
    SettlementStatus,
    Statement,
)
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.finance.services.settlement_service import (
    generate_settlement_for_reservation,
    mark_settlement_as_paid,
)
from apps.identity.models import UserRole
from apps.integration.models import AccessCredentialStatus
from apps.integration.services.access_simulator import (
    provision_access_for_reservation,
    simulate_access_use,
)
from apps.scheduling.models import AvailabilityRule, Reservation, ReservationStatus
from apps.scheduling.services.reservation_service import (
    confirm_reservation,
    create_reservation,
)
from apps.vault.models import DocumentAsset, DocumentType
from apps.vault.services.document_service import upload_document

TEST_MEDIA_ROOT = Path(__file__).resolve().parents[1] / "tmp" / "prompt13-media"


def create_user(email: str, role: str = UserRole.ADMIN) -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=email,
        password="segura-123",
        first_name="Prompt",
        last_name="Trece",
        role=role,
        is_staff=role in {UserRole.ADMIN, UserRole.SUPERADMIN},
    )


@pytest.mark.django_db
def test_smoke_main_mvp_routes_for_authenticated_admin(client: Any) -> None:
    user = create_user("prompt13-smoke@example.com", UserRole.ADMIN)
    client.force_login(user)

    urls = (
        "/",
        "/dashboard/",
        "/clinicas/",
        "/consultorios/",
        "/disponibilidad/",
        "/calendario/",
        "/calendario/vista-rapida/",
        "/tarifas/",
        "/reservaciones/",
        "/pagos/",
        "/liquidaciones/",
        "/documentos/",
        "/timeline/",
        "/integraciones/accesos/",
        "/reportes/",
    )

    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url


@pytest.mark.django_db
def test_mvp_permissions_require_login_and_apply_basic_role_rules(client: Any) -> None:
    response = client.get("/clinicas/")
    assert response.status_code == 302
    assert "/login/" in response["Location"]

    auditor = create_user("prompt13-auditor@example.com", UserRole.AUDITOR)
    client.force_login(auditor)
    response = client.post("/clinicas/nueva/", {"name": "No debe crear"})
    assert response.status_code == 403

    receptionist = create_user(
        "prompt13-recepcion@example.com",
        UserRole.RECEPTIONIST,
    )
    client.force_login(receptionist)
    assert client.get("/calendario/").status_code == 200
    assert client.get("/pagos/").status_code == 403


@pytest.mark.django_db
def test_room_list_search_pagination_and_breadcrumb(client: Any) -> None:
    user = create_user("prompt13-search@example.com", UserRole.ADMIN)
    clinic = Clinic.objects.create(name="Clínica Búsqueda")
    owner = OwnerProfile.objects.create(user=create_user("prompt13-owner@example.com"))
    for index in range(30):
        ConsultingRoom.objects.create(
            clinic=clinic,
            owner=owner,
            name=f"Sala QA {index:02d}",
        )
    client.force_login(user)

    response = client.get("/consultorios/?q=Sala+QA&page=2")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Dashboard" in content
    assert "Sala QA 29" in content
    assert "2 / 2" in content


@pytest.mark.django_db
def test_owner_role_only_sees_own_rooms(client: Any) -> None:
    owner_user = create_user("prompt13-own-owner@example.com", UserRole.OWNER)
    other_user = create_user("prompt13-other-owner@example.com", UserRole.OWNER)
    owner = OwnerProfile.objects.create(user=owner_user, display_name="Dueño Propio")
    other_owner = OwnerProfile.objects.create(
        user=other_user,
        display_name="Dueño Otro",
    )
    clinic = Clinic.objects.create(name="Clínica Scope Owner")
    own_room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name="Scope Owner Propio",
    )
    other_room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=other_owner,
        name="Scope Owner Ajeno",
    )
    client.force_login(owner_user)

    response = client.get("/consultorios/?q=Scope+Owner")

    content = response.content.decode()
    assert response.status_code == 200
    assert own_room.name in content
    assert other_room.name not in content


@pytest.mark.django_db
def test_tenant_doctor_role_only_sees_own_reservations(client: Any) -> None:
    owner = OwnerProfile.objects.create(
        user=create_user("prompt13-scope-owner@example.com", UserRole.OWNER)
    )
    clinic = Clinic.objects.create(name="Clínica Scope Médico")
    room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name="Scope Médico Consultorio",
    )
    user = create_user("prompt13-own-doctor@example.com", UserRole.TENANT_DOCTOR)
    other_user = create_user(
        "prompt13-other-doctor@example.com",
        UserRole.TENANT_DOCTOR,
    )
    doctor = TenantDoctorProfile.objects.create(
        user=user,
        display_name="Scope Médico Propio",
        status=TenantDoctorStatus.AUTHORIZED,
    )
    other_doctor = TenantDoctorProfile.objects.create(
        user=other_user,
        display_name="Scope Médico Ajeno",
        status=TenantDoctorStatus.AUTHORIZED,
    )
    own_reservation = Reservation.objects.create(
        room=room,
        tenant_doctor=doctor,
        notes="Scope Médico Propio",
    )
    other_reservation = Reservation.objects.create(
        room=room,
        tenant_doctor=other_doctor,
        start_time=time(9, 0),
        end_time=time(10, 0),
        notes="Scope Médico Ajeno",
    )
    client.force_login(user)

    response = client.get("/reservaciones/?q=Scope+M%C3%A9dico")

    content = response.content.decode()
    assert response.status_code == 200
    assert str(own_reservation.tenant_doctor) in content
    assert str(other_reservation.tenant_doctor) not in content


@pytest.mark.django_db
def test_seed_demo_data_is_idempotent() -> None:
    with override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT / "seed"):
        stdout = StringIO()
        call_command("seed_demo_data", stdout=stdout)
        first_counts = _demo_counts()

        call_command("seed_demo_data", stdout=StringIO())
        second_counts = _demo_counts()

    assert "Datos demo listos" in stdout.getvalue()
    assert first_counts == second_counts
    assert Clinic.objects.filter(name__startswith="Demo Clínica").count() == 2
    assert ConsultingRoom.objects.filter(name__startswith="Consultorio").count() >= 4
    assert (
        Reservation.objects.filter(notes__icontains="[demo-user-testing]").count() == 4
    )


@pytest.mark.django_db
def test_happy_path_user_testing_flow_creates_traceable_records() -> None:
    with override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT / "happy"):
        actor = create_user("prompt13-happy-admin@example.com", UserRole.ADMIN)
        reservation = _create_happy_path_reservation(actor)
        statement = Statement.objects.get(reservation=reservation)

        payment = register_payment(
            reservation=reservation,
            amount=statement.total_doctor,
            currency=statement.currency,
            method=PaymentMethod.TRANSFER,
            reference="PROMPT13-PAGO",
            payment_date=timezone.localdate(),
            actor=actor,
        )
        validate_payment(payment=payment, actor=actor)
        reservation.refresh_from_db()
        assert reservation.status == ReservationStatus.PAID

        confirm_reservation(reservation=reservation, actor=actor)
        reservation.refresh_from_db()
        credential = provision_access_for_reservation(reservation, user=actor)
        simulate_access_use(credential=credential, user=actor)
        credential.refresh_from_db()
        assert credential.status == AccessCredentialStatus.USED

        settlement = generate_settlement_for_reservation(
            reservation=reservation,
            notes="Happy path Prompt 13",
            actor=actor,
        )
        mark_settlement_as_paid(
            settlement=settlement,
            reference="PROMPT13-LIQ",
            payment_date=timezone.localdate(),
            actor=actor,
        )
        settlement.refresh_from_db()
        assert settlement.status == SettlementStatus.PAID

        upload_document(
            title="Prompt 13 evidencia",
            document_type=DocumentType.OTHER,
            file=ContentFile(b"Evidencia Prompt 13", name="prompt13.txt"),
            reservation=reservation,
            actor=actor,
        )

    timeline_types = {
        item.event.event_type for item in get_timeline_for_reservation(reservation)
    }

    assert Payment.objects.filter(reservation=reservation).exists()
    assert Settlement.objects.filter(reservation=reservation).exists()
    assert DocumentAsset.objects.filter(reservation=reservation).exists()
    assert {
        "reservation.requested",
        "statement.generated",
        "payment.validated",
        "reservation.marked_paid",
        "reservation.confirmed",
        "access.used",
        "settlement.paid",
    }.issubset(timeline_types)
    assert "document.received" in timeline_types


def _demo_counts() -> tuple[int, int, int, int, int, int, int]:
    return (
        Clinic.objects.filter(name__startswith="Demo Clínica").count(),
        ConsultingRoom.objects.filter(name__startswith="Consultorio").count(),
        TenantDoctorProfile.objects.filter(
            user__email__startswith="demo.doctor"
        ).count(),
        Reservation.objects.filter(notes__icontains="[demo-user-testing]").count(),
        Payment.objects.filter(reference__startswith="DEMO-PAGO").count(),
        Settlement.objects.filter(payment_reference__startswith="DEMO-LIQ").count(),
        DocumentAsset.objects.filter(title__startswith="Demo ").count(),
    )


def _create_happy_path_reservation(actor: Any) -> Reservation:
    today = timezone.localdate()
    clinic = Clinic.objects.create(name="Prompt 13 Clínica")
    owner = OwnerProfile.objects.create(
        user=create_user("prompt13-owner-happy@example.com")
    )
    room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name="Prompt 13 Consultorio",
        capacity=1,
    )
    specialty = Specialty.objects.create(name="Prompt 13 Especialidad")
    doctor = TenantDoctorProfile.objects.create(
        user=create_user("prompt13-doctor@example.com", UserRole.TENANT_DOCTOR),
        status=TenantDoctorStatus.AUTHORIZED,
    )
    doctor.specialties.add(specialty)
    AvailabilityRule.objects.create(
        room=room,
        name="Prompt 13 disponibilidad total",
        weekday=today.weekday(),
        start_time=time(0, 0),
        end_time=time(23, 59),
        start_date=today,
    )
    RateRule.objects.create(
        room=room,
        name="Prompt 13 tarifa total",
        weekdays=[today.weekday()],
        start_time=time(0, 0),
        end_time=time(23, 59),
        start_date=today,
        price_type=PriceType.HOURLY,
        amount=Decimal("10.00"),
        currency="MXN",
        priority=1,
    )
    return create_reservation(
        room=room,
        tenant_doctor=doctor,
        reservation_date=today,
        start_time=time(0, 0),
        end_time=time(23, 59),
        notes="Prompt 13 happy path",
        actor=actor,
    )
