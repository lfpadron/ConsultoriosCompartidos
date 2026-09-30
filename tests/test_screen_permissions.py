from datetime import time
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
)
from apps.core.permissions import get_screen_access, scope_queryset_for_user
from apps.identity.models import (
    ApplicationScreen,
    RoleScreenPermission,
    ScreenAccessLevel,
    UserRole,
    UserRoleAssignment,
)
from apps.identity.permission_service import update_permission_matrix
from apps.scheduling.models import Reservation


def create_user(email: str, role: str) -> Any:
    return get_user_model().objects.create_user(
        email=email,
        password="Segura-12345",
        first_name="Prueba",
        last_name="Permisos",
        role=role,
        is_staff=role == UserRole.SUPERADMIN,
        is_superuser=role == UserRole.SUPERADMIN,
    )


@pytest.mark.django_db
def test_user_manager_creates_primary_role_assignment() -> None:
    user = create_user("primary-role@example.com", UserRole.OWNER)

    assert user.role_assignments.filter(
        role=UserRole.OWNER,
        is_active=True,
        is_deleted=False,
    ).exists()
    assert user.get_role_values() == {UserRole.OWNER}


@pytest.mark.django_db
def test_user_form_saves_additional_roles(client: Any) -> None:
    superadmin = create_user("roles-root@example.com", UserRole.SUPERADMIN)
    client.force_login(superadmin)

    response = client.post(
        reverse("user_create"),
        {
            "email": "dual-profile@example.com",
            "first_name": "Doble",
            "last_name": "Perfil",
            "role": UserRole.OWNER,
            "additional_roles": [UserRole.TENANT_DOCTOR],
            "assigned_clinics": [],
            "assigned_owners": [],
            "temporary_password": "Temporal-12345",
            "must_change_password": "on",
            "send_invitation": "",
            "is_active": "on",
        },
    )

    user = get_user_model().objects.get(email="dual-profile@example.com")
    assert response.status_code == 302
    assert user.role == UserRole.OWNER
    assert user.get_role_values() == {
        UserRole.OWNER,
        UserRole.TENANT_DOCTOR,
    }
    assert TraceEvent.objects.filter(
        event_type="identity.user_roles.updated",
        payload__id=str(user.pk),
    ).exists()


@pytest.mark.django_db
def test_owner_and_tenant_scopes_are_combined() -> None:
    dual_user = create_user("dual-scope@example.com", UserRole.OWNER)
    UserRoleAssignment.objects.create(
        user=dual_user,
        role=UserRole.TENANT_DOCTOR,
    )
    owner = OwnerProfile.objects.create(user=dual_user, display_name="Doble Perfil")
    tenant = TenantDoctorProfile.objects.create(
        user=dual_user,
        display_name="Doble Perfil",
    )
    other_owner = OwnerProfile.objects.create(
        user=create_user("other-owner@example.com", UserRole.OWNER),
        display_name="Otro Propietario",
    )
    clinic = Clinic.objects.create(name="Clínica Roles")
    owned_room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name="Consultorio Propio",
        number="101",
    )
    rented_room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=other_owner,
        name="Consultorio Rentado",
        number="102",
    )
    tenant.assigned_rooms.add(rented_room)
    owner_reservation = Reservation.objects.create(
        room=owned_room,
        tenant_doctor=TenantDoctorProfile.objects.create(
            user=create_user("other-tenant@example.com", UserRole.TENANT_DOCTOR),
            display_name="Otro Arrendatario",
        ),
        date=timezone.localdate(),
        start_time=time(8),
        end_time=time(9),
    )
    tenant_reservation = Reservation.objects.create(
        room=rented_room,
        tenant_doctor=tenant,
        date=timezone.localdate(),
        start_time=time(9),
        end_time=time(10),
    )

    scoped = scope_queryset_for_user(Reservation.objects.all(), dual_user)

    assert set(scoped.values_list("pk", flat=True)) == {
        owner_reservation.pk,
        tenant_reservation.pk,
    }


@pytest.mark.django_db
def test_no_access_is_grey_and_direct_url_is_forbidden(client: Any) -> None:
    receptionist = create_user("grey-menu@example.com", UserRole.RECEPTIONIST)
    client.force_login(receptionist)

    response = client.get(reverse("calendar_week"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "nav-link-unavailable" in content
    assert client.get(reverse("payments")).status_code == 403


@pytest.mark.django_db
def test_read_access_allows_list_but_blocks_edit_routes(client: Any) -> None:
    user = create_user("read-only@example.com", UserRole.OPERATOR)
    permission = RoleScreenPermission.objects.get(
        role=UserRole.OPERATOR,
        screen__key="clinics",
    )
    permission.access_level = ScreenAccessLevel.READ
    permission.save()
    client.force_login(user)

    assert client.get(reverse("clinics")).status_code == 200
    assert client.get(reverse("clinic_create")).status_code == 403
    assert (
        client.post(reverse("clinic_create"), {"name": "No autorizada"}).status_code
        == 403
    )
    assert not Clinic.objects.filter(name="No autorizada").exists()


@pytest.mark.django_db
def test_highest_permission_from_multiple_roles_wins(client: Any) -> None:
    user = create_user("role-union@example.com", UserRole.AUDITOR)
    UserRoleAssignment.objects.create(user=user, role=UserRole.OPERATOR)
    operator_permission = RoleScreenPermission.objects.get(
        role=UserRole.OPERATOR,
        screen__key="clinics",
    )
    operator_permission.access_level = ScreenAccessLevel.EDIT
    operator_permission.save()
    client.force_login(user)

    response = client.post(
        reverse("clinic_create"),
        {
            "name": "Clínica por permiso combinado",
            "timezone": "America/Mexico_City",
            "hour_format": "24h",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    assert Clinic.objects.filter(name="Clínica por permiso combinado").exists()


@pytest.mark.django_db
def test_permissions_screen_supports_read_only_access(client: Any) -> None:
    permission = RoleScreenPermission.objects.get(
        role=UserRole.ADMIN,
        screen__key="permissions",
    )
    permission.access_level = ScreenAccessLevel.READ
    permission.save()
    admin = create_user("permissions-reader@example.com", UserRole.ADMIN)
    client.force_login(admin)

    response = client.get(reverse("permissions"))

    assert response.status_code == 200
    assert "Gestión de permisos" in response.content.decode()
    assert "Guardar permisos" not in response.content.decode()
    assert client.post(reverse("permissions"), {}).status_code == 403


@pytest.mark.django_db
def test_superadmin_permissions_access_cannot_be_removed() -> None:
    superadmin = create_user("protected-root@example.com", UserRole.SUPERADMIN)
    permission = RoleScreenPermission.objects.get(
        role=UserRole.SUPERADMIN,
        screen__key="permissions",
    )
    permission.access_level = ScreenAccessLevel.NONE

    with pytest.raises(ValidationError):
        permission.save()

    RoleScreenPermission.objects.filter(pk=permission.pk).update(
        access_level=ScreenAccessLevel.NONE
    )
    assert (
        get_screen_access(superadmin, "permissions") == ScreenAccessLevel.EDIT
    )


@pytest.mark.django_db
def test_permission_changes_are_traced() -> None:
    actor = create_user("trace-root@example.com", UserRole.SUPERADMIN)
    screen = ApplicationScreen.objects.get(key="reports")

    updated = update_permission_matrix(
        levels={(UserRole.RECEPTIONIST, screen.key): ScreenAccessLevel.READ},
        actor=actor,
    )

    assert updated == 1
    assert TraceEvent.objects.filter(
        event_type="identity.screen_permission.updated",
        payload__role=UserRole.RECEPTIONIST,
        payload__screen_key="reports",
        payload__access_level=ScreenAccessLevel.READ,
    ).exists()
