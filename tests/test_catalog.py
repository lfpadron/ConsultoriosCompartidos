from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.catalog.forms import ConsultingRoomForm
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    Equipment,
    OwnerProfile,
    Specialty,
    TenantDoctorProfile,
)
from apps.identity.models import UserRole


def create_user(email: str, role: str = UserRole.OPERATOR) -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=email,
        password="segura-123",
        first_name="Nombre",
        last_name="Apellido",
        role=role,
    )


@pytest.mark.django_db
def test_create_clinic() -> None:
    clinic = Clinic.objects.create(
        name="Clínica Norte",
        address="Av. Siempre Viva 123",
        phone="555-0000",
        email="norte@example.com",
        schedule_text="Lunes a viernes de 8:00 a 18:00",
    )

    assert clinic.name == "Clínica Norte"
    assert clinic.is_active is True


@pytest.mark.django_db
def test_create_specialty() -> None:
    specialty = Specialty.objects.create(
        name="Cardiología",
        description="Atención cardiovascular",
    )

    assert str(specialty) == "Cardiología"


@pytest.mark.django_db
def test_create_equipment() -> None:
    equipment = Equipment.objects.create(
        name="Ultrasonido",
        description="Equipo de diagnóstico",
    )

    assert equipment.is_active is True


@pytest.mark.django_db
def test_create_owner_profile() -> None:
    user = create_user("dueno@example.com", UserRole.OWNER)

    owner = OwnerProfile.objects.create(
        user=user,
        professional_license="123456",
        tax_id="XAXX010101000",
        phone="555-1111",
        notes="Propietario inicial",
    )

    assert owner.display_name == "Nombre Apellido"
    assert owner.user == user


@pytest.mark.django_db
def test_create_tenant_doctor_profile() -> None:
    user = create_user("medico@example.com", UserRole.TENANT_DOCTOR)
    specialty = Specialty.objects.create(name="Dermatología")

    doctor = TenantDoctorProfile.objects.create(
        user=user,
        professional_license="654321",
        tax_id="XEXX010101000",
        phone="555-2222",
    )
    doctor.specialties.add(specialty)

    assert doctor.display_name == "Nombre Apellido"
    assert list(doctor.specialties.all()) == [specialty]


@pytest.mark.django_db
def test_create_consulting_room() -> None:
    clinic = Clinic.objects.create(name="Clínica Centro")
    owner = OwnerProfile.objects.create(user=create_user("owner-room@example.com"))
    specialty = Specialty.objects.create(name="Pediatría")
    equipment = Equipment.objects.create(name="Camilla")

    room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name="Consultorio 1",
        floor="PB",
        capacity=2,
    )
    room.allowed_specialties.add(specialty)
    room.equipment.add(equipment)

    assert room.clinic == clinic
    assert room.owner == owner
    assert room.capacity == 2


@pytest.mark.django_db
def test_consulting_room_specialties_must_not_overlap() -> None:
    clinic = Clinic.objects.create(name="Clínica Sur")
    owner = OwnerProfile.objects.create(user=create_user("owner-overlap@example.com"))
    specialty = Specialty.objects.create(name="Neurología")

    form = ConsultingRoomForm(
        data={
            "clinic": str(clinic.pk),
            "owner": str(owner.pk),
            "name": "Consultorio 2",
            "capacity": "1",
            "allowed_specialties": [str(specialty.pk)],
            "excluded_specialties": [str(specialty.pk)],
            "equipment": [],
            "status": "available",
            "is_active": "on",
        }
    )

    assert form.is_valid() is False
    assert "excluded_specialties" in form.errors


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/clinicas/",
        "/clinicas/nueva/",
        "/especialidades/",
        "/especialidades/nueva/",
        "/equipamiento/",
        "/equipamiento/nuevo/",
        "/propietarios/",
        "/propietarios/nuevo/",
        "/medicos-arrendatarios/",
        "/medicos-arrendatarios/nuevo/",
        "/consultorios/",
        "/consultorios/nuevo/",
    ],
)
def test_catalog_views_return_200_for_authenticated_user(
    client: Any, path: str
) -> None:
    user = create_user("viewer@example.com", UserRole.ADMIN)
    client.force_login(user)

    response = client.get(path)

    assert response.status_code == 200
