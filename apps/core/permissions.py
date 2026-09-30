"""Role, screen, method and object-level access rules."""

from collections.abc import Callable
from typing import Any, cast

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, resolve_url
from django.urls import Resolver404, resolve

from apps.identity.models import RoleScreenPermission, ScreenAccessLevel, UserRole
from apps.identity.screen_registry import (
    SCREEN_DEFINITIONS,
    default_access_for_role,
    route_requires_edit,
    screen_key_for_route,
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATH_PREFIXES = (
    "/login/",
    "/logout/",
    "/admin/login/",
    "/static/",
    "/media/",
)
FORCED_PASSWORD_ALLOWED_PREFIXES = (
    "/cambiar-contrasena/",
    "/logout/",
    "/static/",
    "/media/",
)


class MvpAccessMiddleware:
    """Enforce screen permissions while preserving forced password changes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if _is_public_path(request.path):
            return self.get_response(request)

        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(), resolve_url(settings.LOGIN_URL)
            )

        if getattr(user, "must_change_password", False) and not any(
            request.path.startswith(prefix)
            for prefix in FORCED_PASSWORD_ALLOWED_PREFIXES
        ):
            return redirect("password_change_required")

        if any(
            request.path.startswith(prefix)
            for prefix in FORCED_PASSWORD_ALLOWED_PREFIXES
        ):
            return self.get_response(request)

        try:
            match = resolve(request.path_info)
        except Resolver404:
            return self.get_response(request)

        url_name = match.url_name
        screen_key = screen_key_for_route(url_name)
        if screen_key is None:
            return self.get_response(request)

        access_level = get_screen_access(user, screen_key)
        cast_request = cast(Any, request)
        cast_request.current_screen_key = screen_key
        cast_request.current_screen_access = access_level
        required_level = (
            ScreenAccessLevel.EDIT
            if route_requires_edit(url_name or "", request.method or "")
            else ScreenAccessLevel.READ
        )
        if not access_level_allows(access_level, required_level):
            return HttpResponseForbidden(
                "No tienes permiso para acceder o modificar esta pantalla."
            )

        return self.get_response(request)


def _is_public_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


ACCESS_LEVEL_RANK: dict[str, int] = {
    ScreenAccessLevel.NONE: 0,
    ScreenAccessLevel.READ: 1,
    ScreenAccessLevel.EDIT: 2,
}


def get_user_roles(user: Any) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()
    role_getter = getattr(user, "get_role_values", None)
    if callable(role_getter):
        return set(role_getter())
    role = getattr(user, "role", "")
    return {role} if role else set()


def user_has_role(user: Any, role: str) -> bool:
    return role in get_user_roles(user)


def access_level_allows(current: str, required: str) -> bool:
    return ACCESS_LEVEL_RANK.get(current, 0) >= ACCESS_LEVEL_RANK.get(required, 0)


def get_user_screen_access_map(user: Any) -> dict[str, str]:
    roles = get_user_roles(user)
    screen_keys = [definition.key for definition in SCREEN_DEFINITIONS]
    result: dict[str, str] = {key: ScreenAccessLevel.NONE for key in screen_keys}
    if not roles:
        return result

    explicit = {
        (permission.role, permission.screen.key): permission.access_level
        for permission in RoleScreenPermission.objects.filter(
            role__in=roles,
            screen__key__in=screen_keys,
            screen__is_active=True,
            screen__is_deleted=False,
            is_active=True,
            is_deleted=False,
        ).select_related("screen")
    }
    for screen_key in screen_keys:
        levels = [
            explicit.get(
                (role, screen_key),
                default_access_for_role(role, screen_key),
            )
            for role in roles
        ]
        result[screen_key] = max(
            levels,
            key=lambda level: ACCESS_LEVEL_RANK.get(level, 0),
        )

    if UserRole.SUPERADMIN in roles:
        result["permissions"] = ScreenAccessLevel.EDIT
    return result


def get_screen_access(user: Any, screen_key: str) -> str:
    return get_user_screen_access_map(user).get(screen_key, ScreenAccessLevel.NONE)


def can_edit_screen(user: Any, screen_key: str) -> bool:
    return access_level_allows(
        get_screen_access(user, screen_key),
        ScreenAccessLevel.EDIT,
    )


def scope_queryset_for_user(queryset: QuerySet[Any], user: Any) -> QuerySet[Any]:
    """Apply the union of every data scope granted by the user's active roles."""

    roles = get_user_roles(user)
    if roles.intersection(
        {
            UserRole.SUPERADMIN,
            UserRole.OPERATOR,
            UserRole.RECEPTIONIST,
            UserRole.AUDITOR,
        }
    ):
        return queryset
    if UserRole.ADMIN in roles:
        return _scope_business_admin_queryset(queryset, user)

    scoped = queryset.none()
    has_scoped_role = False
    if UserRole.OWNER in roles:
        has_scoped_role = True
        owner = getattr(user, "owner_profile", None)
        if owner is not None:
            scoped = scoped | _scope_owner_queryset(queryset, owner)
        elif roles == {UserRole.OWNER}:
            return queryset
    if UserRole.TENANT_DOCTOR in roles:
        has_scoped_role = True
        tenant_doctor = getattr(user, "tenant_doctor_profile", None)
        if tenant_doctor is not None:
            scoped = scoped | _scope_tenant_doctor_queryset(queryset, tenant_doctor)
        elif roles == {UserRole.TENANT_DOCTOR}:
            return queryset
    if UserRole.ASSISTANT in roles:
        has_scoped_role = True
        scoped = scoped | _scope_assistant_queryset(queryset, user)
    if has_scoped_role:
        return scoped.distinct()
    return queryset.none()


def _scope_business_admin_queryset(queryset: QuerySet[Any], user: Any) -> QuerySet[Any]:
    clinics = user.assigned_clinics.filter(is_deleted=False)
    if not clinics.exists():
        return queryset

    model_label = queryset.model._meta.label
    if model_label == "catalog.Clinic":
        return queryset.filter(pk__in=clinics.values("pk"))
    if model_label == "catalog.OwnerProfile":
        return queryset.filter(consulting_rooms__clinic__in=clinics).distinct()
    if model_label == "catalog.TenantDoctorProfile":
        return queryset.filter(
            Q(assigned_rooms__clinic__in=clinics)
            | Q(reservations__room__clinic__in=clinics)
        ).distinct()
    if model_label == "catalog.ConsultingRoom":
        return queryset.filter(clinic__in=clinics)
    if model_label in {
        "scheduling.AvailabilityRule",
        "scheduling.AvailabilityException",
        "finance.RateRule",
        "finance.RoomRateDiscount",
    }:
        return queryset.filter(room__clinic__in=clinics)
    if model_label == "finance.TenantDoctorDiscount":
        return queryset.filter(
            Q(tenant_doctor__assigned_rooms__clinic__in=clinics)
            | Q(tenant_doctor__reservations__room__clinic__in=clinics)
        ).distinct()
    if model_label in {
        "billing.OwnerSubscription",
        "billing.OwnerCommissionRule",
        "billing.OwnerPayoutSchedule",
    }:
        return queryset.filter(owner__consulting_rooms__clinic__in=clinics).distinct()
    if model_label == "billing.TenantSubscription":
        return queryset.filter(
            Q(tenant_doctor__assigned_rooms__clinic__in=clinics)
            | Q(tenant_doctor__reservations__room__clinic__in=clinics)
        ).distinct()
    if model_label in {
        "billing.RoomFixedFeeRule",
        "billing.RoomMonthlyFee",
    }:
        return queryset.filter(room__clinic__in=clinics)
    if model_label == "billing.CancellationPolicy":
        return queryset.filter(clinic__in=clinics)
    if model_label == "billing.CancellationPenaltyRule":
        return queryset.filter(policy__clinic__in=clinics)
    if model_label in {"scheduling.Reservation", "scheduling.ReservationBatch"}:
        return queryset.filter(room__clinic__in=clinics)
    if model_label == "finance.Statement":
        return queryset.filter(reservation__room__clinic__in=clinics)
    if model_label == "finance.Payment":
        return queryset.filter(reservation__room__clinic__in=clinics)
    if model_label == "finance.Settlement":
        return queryset.filter(room__clinic__in=clinics)
    if model_label == "vault.DocumentAsset":
        return queryset.filter(
            Q(clinic__in=clinics)
            | Q(room__clinic__in=clinics)
            | Q(owner__consulting_rooms__clinic__in=clinics)
            | Q(tenant_doctor__assigned_rooms__clinic__in=clinics)
            | Q(reservation__room__clinic__in=clinics)
            | Q(payment__reservation__room__clinic__in=clinics)
            | Q(settlement__room__clinic__in=clinics)
        ).distinct()
    if model_label == "integration.AccessCredential":
        return queryset.filter(reservation__room__clinic__in=clinics)
    return queryset


def _scope_owner_queryset(queryset: QuerySet[Any], owner: Any) -> QuerySet[Any]:
    model_label = queryset.model._meta.label
    if model_label == "catalog.OwnerProfile":
        return queryset.filter(pk=owner.pk)
    if model_label == "catalog.ConsultingRoom":
        return queryset.filter(owner=owner)
    if model_label in {"scheduling.Reservation", "scheduling.ReservationBatch"}:
        return queryset.filter(room__owner=owner)
    if model_label == "finance.Statement":
        return queryset.filter(reservation__room__owner=owner)
    if model_label == "finance.RoomRateDiscount":
        return queryset.filter(room__owner=owner)
    if model_label == "finance.TenantDoctorDiscount":
        return queryset.filter(
            Q(tenant_doctor__assigned_rooms__owner=owner)
            | Q(tenant_doctor__reservations__room__owner=owner)
        ).distinct()
    if model_label in {
        "billing.OwnerSubscription",
        "billing.OwnerCommissionRule",
        "billing.OwnerPayoutSchedule",
    }:
        return queryset.filter(owner=owner)
    if model_label in {
        "billing.RoomFixedFeeRule",
        "billing.RoomMonthlyFee",
    }:
        return queryset.filter(room__owner=owner)
    if model_label == "billing.CancellationPolicy":
        return queryset.filter(
            Q(room__owner=owner)
            | Q(room__isnull=True, clinic__consulting_rooms__owner=owner)
        ).distinct()
    if model_label == "billing.CancellationPenaltyRule":
        return queryset.filter(
            Q(policy__room__owner=owner)
            | Q(
                policy__room__isnull=True,
                policy__clinic__consulting_rooms__owner=owner,
            )
        ).distinct()
    if model_label == "finance.Settlement":
        return queryset.filter(owner=owner)
    if model_label == "vault.DocumentAsset":
        return queryset.filter(
            Q(owner=owner)
            | Q(room__owner=owner)
            | Q(reservation__room__owner=owner)
            | Q(payment__reservation__room__owner=owner)
            | Q(settlement__owner=owner)
        )
    return queryset


def _scope_tenant_doctor_queryset(
    queryset: QuerySet[Any],
    tenant_doctor: Any,
) -> QuerySet[Any]:
    model_label = queryset.model._meta.label
    assigned_rooms = tenant_doctor.assigned_rooms.filter(is_deleted=False)
    if model_label == "catalog.TenantDoctorProfile":
        return queryset.filter(pk=tenant_doctor.pk)
    if model_label == "catalog.ConsultingRoom" and assigned_rooms.exists():
        return queryset.filter(pk__in=assigned_rooms.values("pk"))
    if model_label in {"scheduling.Reservation", "scheduling.ReservationBatch"}:
        return queryset.filter(tenant_doctor=tenant_doctor)
    if model_label == "finance.Statement":
        return queryset.filter(reservation__tenant_doctor=tenant_doctor)
    if model_label == "finance.RoomRateDiscount" and assigned_rooms.exists():
        return queryset.filter(room__in=assigned_rooms)
    if model_label == "finance.TenantDoctorDiscount":
        return queryset.filter(tenant_doctor=tenant_doctor)
    if model_label == "billing.TenantSubscription":
        return queryset.filter(tenant_doctor=tenant_doctor)
    if (
        model_label
        in {
            "billing.RoomFixedFeeRule",
            "billing.RoomMonthlyFee",
        }
        and assigned_rooms.exists()
    ):
        return queryset.filter(room__in=assigned_rooms)
    if model_label == "billing.CancellationPolicy" and assigned_rooms.exists():
        return queryset.filter(
            Q(room__in=assigned_rooms)
            | Q(room__isnull=True, clinic__consulting_rooms__in=assigned_rooms)
        ).distinct()
    if model_label == "billing.CancellationPenaltyRule" and assigned_rooms.exists():
        return queryset.filter(
            Q(policy__room__in=assigned_rooms)
            | Q(
                policy__room__isnull=True,
                policy__clinic__consulting_rooms__in=assigned_rooms,
            )
        ).distinct()
    if model_label == "finance.Payment":
        return queryset.filter(tenant_doctor=tenant_doctor)
    if model_label == "vault.DocumentAsset":
        return queryset.filter(
            Q(tenant_doctor=tenant_doctor)
            | Q(reservation__tenant_doctor=tenant_doctor)
            | Q(payment__tenant_doctor=tenant_doctor)
        )
    if model_label == "integration.AccessCredential":
        return queryset.filter(tenant_doctor=tenant_doctor)
    return queryset


def _scope_assistant_queryset(queryset: QuerySet[Any], user: Any) -> QuerySet[Any]:
    owners = user.assigned_owners.filter(is_deleted=False)
    if not owners.exists():
        return queryset.none()

    model_label = queryset.model._meta.label
    if model_label == "catalog.OwnerProfile":
        return queryset.filter(pk__in=owners.values("pk"))
    if model_label == "catalog.ConsultingRoom":
        return queryset.filter(owner__in=owners)
    if model_label in {"scheduling.Reservation", "scheduling.ReservationBatch"}:
        return queryset.filter(room__owner__in=owners)
    if model_label == "finance.Statement":
        return queryset.filter(reservation__room__owner__in=owners)
    if model_label == "finance.RoomRateDiscount":
        return queryset.filter(room__owner__in=owners)
    if model_label == "finance.TenantDoctorDiscount":
        return queryset.filter(
            Q(tenant_doctor__assigned_rooms__owner__in=owners)
            | Q(tenant_doctor__reservations__room__owner__in=owners)
        ).distinct()
    if model_label in {
        "billing.OwnerSubscription",
        "billing.OwnerCommissionRule",
        "billing.OwnerPayoutSchedule",
    }:
        return queryset.filter(owner__in=owners)
    if model_label in {
        "billing.RoomFixedFeeRule",
        "billing.RoomMonthlyFee",
    }:
        return queryset.filter(room__owner__in=owners)
    if model_label == "billing.CancellationPolicy":
        return queryset.filter(
            Q(room__owner__in=owners)
            | Q(room__isnull=True, clinic__consulting_rooms__owner__in=owners)
        ).distinct()
    if model_label == "billing.CancellationPenaltyRule":
        return queryset.filter(
            Q(policy__room__owner__in=owners)
            | Q(
                policy__room__isnull=True,
                policy__clinic__consulting_rooms__owner__in=owners,
            )
        ).distinct()
    if model_label == "finance.Payment":
        return queryset.filter(reservation__room__owner__in=owners)
    if model_label == "finance.Settlement":
        return queryset.filter(owner__in=owners)
    if model_label == "vault.DocumentAsset":
        return queryset.filter(
            Q(owner__in=owners)
            | Q(room__owner__in=owners)
            | Q(reservation__room__owner__in=owners)
            | Q(payment__reservation__room__owner__in=owners)
            | Q(settlement__owner__in=owners)
        ).distinct()
    if model_label == "integration.AccessCredential":
        return queryset.filter(reservation__room__owner__in=owners)
    return queryset
