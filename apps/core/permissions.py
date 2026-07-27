"""Simple MVP access rules.

This is intentionally path-level and coarse while the project does not have
full object-level authorization yet.
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, resolve_url

from apps.identity.models import UserRole

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATH_PREFIXES = (
    "/login/",
    "/logout/",
    "/admin/login/",
    "/static/",
    "/media/",
)
RECEPTIONIST_BLOCKED_PREFIXES = (
    "/tarifas/",
    "/pagos/",
    "/liquidaciones/",
    "/reportes/ingresos",
    "/reportes/pagos",
    "/reportes/liquidaciones",
)
FORCED_PASSWORD_ALLOWED_PREFIXES = (
    "/cambiar-contrasena/",
    "/logout/",
    "/static/",
    "/media/",
)


class MvpAccessMiddleware:
    """Enforce the broad role rules needed for user-test hardening."""

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

        role = getattr(user, "role", "")
        if getattr(user, "must_change_password", False) and not any(
            request.path.startswith(prefix)
            for prefix in FORCED_PASSWORD_ALLOWED_PREFIXES
        ):
            return redirect("password_change_required")
        if role == UserRole.AUDITOR and request.method not in SAFE_METHODS:
            return HttpResponseForbidden("El rol Auditor es de solo lectura.")
        if role == UserRole.RECEPTIONIST and _is_blocked_for_receptionist(request.path):
            return HttpResponseForbidden(
                "Recepción no tiene acceso a finanzas completas."
            )

        return self.get_response(request)


def _is_public_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


def _is_blocked_for_receptionist(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in RECEPTIONIST_BLOCKED_PREFIXES)


def scope_queryset_for_user(queryset: QuerySet[Any], user: Any) -> QuerySet[Any]:
    """Apply coarse owner/tenant scoping when the user has a linked profile."""

    role = getattr(user, "role", "")
    if role == UserRole.ADMIN:
        return _scope_business_admin_queryset(queryset, user)
    if role == UserRole.OWNER:
        owner = getattr(user, "owner_profile", None)
        if owner is None:
            return queryset
        return _scope_owner_queryset(queryset, owner)
    if role == UserRole.TENANT_DOCTOR:
        tenant_doctor = getattr(user, "tenant_doctor_profile", None)
        if tenant_doctor is None:
            return queryset
        return _scope_tenant_doctor_queryset(queryset, tenant_doctor)
    if role == UserRole.ASSISTANT:
        return _scope_assistant_queryset(queryset, user)
    return queryset


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
    }:
        return queryset.filter(room__clinic__in=clinics)
    if model_label == "scheduling.Reservation":
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
    if model_label == "scheduling.Reservation":
        return queryset.filter(room__owner=owner)
    if model_label == "finance.Statement":
        return queryset.filter(reservation__room__owner=owner)
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
    if model_label == "scheduling.Reservation":
        return queryset.filter(tenant_doctor=tenant_doctor)
    if model_label == "finance.Statement":
        return queryset.filter(reservation__tenant_doctor=tenant_doctor)
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
    if model_label == "scheduling.Reservation":
        return queryset.filter(room__owner__in=owners)
    if model_label == "finance.Statement":
        return queryset.filter(reservation__room__owner__in=owners)
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
