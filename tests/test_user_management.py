from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.catalog.models import Clinic, ConsultingRoom, OwnerProfile
from apps.identity.models import UserRole


def create_user(
    email: str,
    role: str = UserRole.SUPERADMIN,
    password: str = "Temporal-12345",
) -> Any:
    return get_user_model().objects.create_user(
        email=email,
        password=password,
        first_name="Usuaria",
        last_name="Prueba",
        role=role,
        is_staff=role == UserRole.SUPERADMIN,
        is_superuser=role == UserRole.SUPERADMIN,
    )


@pytest.mark.django_db
def test_user_list_requires_manager_role(client: Any) -> None:
    tenant = create_user("tenant-list@example.com", UserRole.TENANT_DOCTOR)
    client.force_login(tenant)

    response = client.get(reverse("users"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_superadmin_can_create_business_admin_with_invitation(
    client: Any,
    settings: Any,
    mailoutbox: list[Any],
) -> None:
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    superadmin = create_user("root-users@example.com")
    clinic = Clinic.objects.create(name="Clínica Usuarios")
    client.force_login(superadmin)

    response = client.post(
        reverse("user_create"),
        {
            "email": "negocio@example.com",
            "first_name": "Nadia",
            "last_name": "Negocio",
            "phone": "555-0101",
            "secondary_email": "nadia.alt@example.com",
            "secondary_phone": "555-0102",
            "role": UserRole.ADMIN,
            "assigned_clinics": [str(clinic.pk)],
            "assigned_owners": [],
            "temporary_password": "Temporal-12345",
            "must_change_password": "on",
            "send_invitation": "on",
            "is_active": "on",
        },
    )

    user = get_user_model().objects.get(email="negocio@example.com")
    assert response.status_code == 302, (
        [template.name for template in response.templates],
        list(response.context["form"].fields),
        response.context["form"].errors.as_json(),
    )
    assert user.role == UserRole.ADMIN
    assert user.phone == "555-0101"
    assert user.assigned_clinics.filter(pk=clinic.pk).exists()
    assert user.must_change_password is True
    assert user.check_password("Temporal-12345")
    assert user.invitation_sent_at is not None
    assert len(mailoutbox) == 1
    assert "negocio@example.com" in mailoutbox[0].to


@pytest.mark.django_db
def test_system_admin_limit_allows_only_three_active_users(client: Any) -> None:
    superadmin = create_user("root-limit@example.com")
    create_user("root-limit-2@example.com")
    create_user("root-limit-3@example.com")
    client.force_login(superadmin)

    response = client.post(
        reverse("user_create"),
        {
            "email": "root-limit-4@example.com",
            "first_name": "Cuarta",
            "last_name": "Admin",
            "role": UserRole.SUPERADMIN,
            "temporary_password": "Temporal-12345",
            "must_change_password": "on",
            "send_invitation": "",
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    assert (
        not get_user_model().objects.filter(email="root-limit-4@example.com").exists()
    )
    assert "Solo puede haber tres administradores" in response.content.decode()


@pytest.mark.django_db
def test_forced_password_change_flow(client: Any, settings: Any) -> None:
    settings.AUTH_PASSWORD_VALIDATORS = []
    user = create_user("force-change@example.com", UserRole.ADMIN)
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 302, (
        [template.name for template in response.templates],
        response.context["form"].errors.as_json(),
    )
    assert response.url == reverse("password_change_required")

    response = client.post(
        reverse("password_change_required"),
        {
            "new_password1": "Tr3s-Colinas-Azules-2026!",
            "new_password2": "Tr3s-Colinas-Azules-2026!",
        },
    )

    user.refresh_from_db()
    assert response.status_code == 302, (
        [template.name for template in response.templates],
        response.context["form"].errors.as_json(),
    )
    assert response.url == reverse("dashboard")
    assert user.must_change_password is False
    assert user.check_password("Tr3s-Colinas-Azules-2026!")


@pytest.mark.django_db
def test_owner_can_create_assistant_assigned_to_self(client: Any) -> None:
    owner_user = create_user("owner-manager@example.com", UserRole.OWNER)
    owner = OwnerProfile.objects.create(user=owner_user)
    client.force_login(owner_user)

    response = client.post(
        reverse("user_create"),
        {
            "email": "assistant@example.com",
            "first_name": "Ana",
            "last_name": "Asistente",
            "role": UserRole.ASSISTANT,
            "assigned_owners": [str(owner.pk)],
            "temporary_password": "Temporal-12345",
            "must_change_password": "on",
            "send_invitation": "",
            "is_active": "on",
        },
    )

    assistant = get_user_model().objects.get(email="assistant@example.com")
    assert response.status_code == 302
    assert assistant.role == UserRole.ASSISTANT
    assert assistant.assigned_owners.filter(pk=owner.pk).exists()


@pytest.mark.django_db
def test_tenant_doctor_profile_accepts_assigned_rooms(client: Any) -> None:
    admin = create_user("tenant-rooms-admin@example.com")
    owner = OwnerProfile.objects.create(
        user=create_user("tenant-rooms-owner@example.com", UserRole.OWNER)
    )
    clinic = Clinic.objects.create(name="Clínica Asignada")
    room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name="Consultorio A",
    )
    tenant_user = create_user("tenant-rooms@example.com", UserRole.TENANT_DOCTOR)
    client.force_login(admin)

    response = client.post(
        reverse("tenant_doctor_create"),
        {
            "user": str(tenant_user.pk),
            "display_name": "Doctora Asignada",
            "specialties": [],
            "assigned_rooms": [str(room.pk)],
            "professional_license": "",
            "tax_id": "",
            "phone": "",
            "status": "authorized",
            "notes": "",
            "is_active": "on",
        },
    )

    tenant_profile = tenant_user.tenant_doctor_profile
    assert response.status_code == 302
    assert tenant_profile.assigned_rooms.filter(pk=room.pk).exists()
