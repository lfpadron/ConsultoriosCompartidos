"""Stable screen and route registry for role-based access control."""

from dataclasses import dataclass

from apps.identity.models import ScreenAccessLevel, UserRole


@dataclass(frozen=True)
class ScreenDefinition:
    key: str
    label: str
    url_name: str
    icon: str


SCREEN_DEFINITIONS = (
    ScreenDefinition("dashboard", "Dashboard", "dashboard", "bi-speedometer2"),
    ScreenDefinition("clinics", "Clínicas", "clinics", "bi-hospital"),
    ScreenDefinition("rooms", "Consultorios", "rooms", "bi-door-open"),
    ScreenDefinition(
        "specialties", "Especialidades", "specialties", "bi-clipboard2-pulse"
    ),
    ScreenDefinition("equipment", "Equipamiento", "equipment", "bi-tools"),
    ScreenDefinition("owners", "Propietarios", "owners", "bi-person-badge"),
    ScreenDefinition(
        "tenant_doctors",
        "Médicos Arrendatarios",
        "tenant_doctors",
        "bi-person-vcard",
    ),
    ScreenDefinition(
        "availability",
        "Disponibilidad y tarifas",
        "availability",
        "bi-calendar-week",
    ),
    ScreenDefinition("calendar_week", "Calendario", "calendar_week", "bi-calendar3"),
    ScreenDefinition(
        "calendar_quick",
        "Vista rápida",
        "calendar_quick",
        "bi-grid-3x3-gap",
    ),
    ScreenDefinition(
        "reservations", "Reservaciones", "reservations", "bi-calendar-check"
    ),
    ScreenDefinition("statements", "Estados de Cuenta", "statements", "bi-receipt"),
    ScreenDefinition(
        "room_rate_discounts",
        "Descuentos Consultorio",
        "room_rate_discounts",
        "bi-percent",
    ),
    ScreenDefinition(
        "tenant_doctor_discounts",
        "Descuentos Arrendatario",
        "tenant_doctor_discounts",
        "bi-tags",
    ),
    ScreenDefinition(
        "owner_subscriptions",
        "Suscripciones Propietarios",
        "owner_subscriptions",
        "bi-person-check",
    ),
    ScreenDefinition(
        "tenant_subscriptions",
        "Suscripciones Arrendatarios",
        "tenant_subscriptions",
        "bi-person-vcard-fill",
    ),
    ScreenDefinition(
        "owner_terms",
        "Comisiones y Pagos",
        "owner_terms",
        "bi-cash-coin",
    ),
    ScreenDefinition(
        "room_fees",
        "Cuotas Consultorios",
        "room_fees",
        "bi-building-gear",
    ),
    ScreenDefinition(
        "cancellation_policies",
        "Políticas Cancelación",
        "cancellation_policies",
        "bi-calendar-x",
    ),
    ScreenDefinition(
        "payment_deadlines",
        "Plazos de Pago",
        "payment_deadlines",
        "bi-hourglass-split",
    ),
    ScreenDefinition("payments", "Pagos", "payments", "bi-credit-card"),
    ScreenDefinition("settlements", "Liquidaciones", "settlements", "bi-bank"),
    ScreenDefinition("documents", "Documentos", "documents", "bi-file-earmark-pdf"),
    ScreenDefinition("access_credentials", "Accesos", "access_credentials", "bi-key"),
    ScreenDefinition("timeline", "Timeline", "timeline", "bi-diagram-3"),
    ScreenDefinition("reports", "Reportes", "reports", "bi-bar-chart"),
    ScreenDefinition("users", "Usuarios", "users", "bi-people"),
    ScreenDefinition("profile", "Perfil", "profile", "bi-person-circle"),
    ScreenDefinition("permissions", "Permisos", "permissions", "bi-shield-lock"),
    ScreenDefinition("administration", "Administración", "administration", "bi-gear"),
)

SCREEN_KEYS = {definition.key for definition in SCREEN_DEFINITIONS}

EXACT_ROUTE_SCREENS = {
    "dashboard": "dashboard",
    "dashboard_page": "dashboard",
    "clinics": "clinics",
    "rooms": "rooms",
    "specialties": "specialties",
    "equipment": "equipment",
    "owners": "owners",
    "tenant_doctors": "tenant_doctors",
    "availability": "availability",
    "availability_exceptions": "availability",
    "rates": "availability",
    "calendar_week": "calendar_week",
    "calendar_quick": "calendar_quick",
    "reservations": "reservations",
    "statements": "statements",
    "room_rate_discounts": "room_rate_discounts",
    "tenant_doctor_discounts": "tenant_doctor_discounts",
    "owner_subscriptions": "owner_subscriptions",
    "tenant_subscriptions": "tenant_subscriptions",
    "owner_terms": "owner_terms",
    "room_fees": "room_fees",
    "cancellation_policies": "cancellation_policies",
    "payment_deadlines": "payment_deadlines",
    "payments": "payments",
    "settlements": "settlements",
    "documents": "documents",
    "access_credentials": "access_credentials",
    "timeline": "timeline",
    "reports": "reports",
    "users": "users",
    "profile": "profile",
    "permissions": "permissions",
    "administration": "administration",
}

ROUTE_PREFIX_SCREENS = (
    ("owner_subscription_", "owner_subscriptions"),
    ("tenant_subscription_", "tenant_subscriptions"),
    ("owner_commission_", "owner_terms"),
    ("owner_payout_", "owner_terms"),
    ("room_fixed_fee_", "room_fees"),
    ("room_monthly_fee_", "room_fees"),
    ("cancellation_policy_", "cancellation_policies"),
    ("reservation_payment_policy_", "payment_deadlines"),
    ("payment_deadline_exception_", "payment_deadlines"),
    ("tenant_doctor_discount_", "tenant_doctor_discounts"),
    ("room_rate_discount_", "room_rate_discounts"),
    ("tenant_doctor_", "tenant_doctors"),
    ("availability_", "availability"),
    ("access_credential_", "access_credentials"),
    ("access_", "access_credentials"),
    ("reservation_", "reservations"),
    ("settlement_", "settlements"),
    ("payment_", "payments"),
    ("document_", "documents"),
    ("report_", "reports"),
    ("timeline_", "timeline"),
    ("room_", "rooms"),
    ("clinic_", "clinics"),
    ("specialty_", "specialties"),
    ("equipment_", "equipment"),
    ("owner_", "owners"),
    ("rate_", "availability"),
    ("user_", "users"),
)

EDIT_ROUTE_NAMES = {
    "clinic_create",
    "clinic_update",
    "clinic_deactivate",
    "room_create",
    "room_update",
    "room_deactivate",
    "specialty_create",
    "specialty_update",
    "specialty_deactivate",
    "equipment_create",
    "equipment_update",
    "equipment_deactivate",
    "owner_create",
    "owner_update",
    "owner_deactivate",
    "tenant_doctor_create",
    "tenant_doctor_update",
    "tenant_doctor_deactivate",
    "availability_rule_create",
    "availability_rule_update",
    "availability_rule_deactivate",
    "availability_exception_create",
    "availability_exception_update",
    "availability_exception_deactivate",
    "rate_create",
    "rate_update",
    "rate_deactivate",
    "reservation_request",
    "reservation_cancel",
    "reservation_confirm",
    "room_rate_discount_create",
    "room_rate_discount_toggle",
    "tenant_doctor_discount_create",
    "tenant_doctor_discount_toggle",
    "owner_subscription_create",
    "owner_subscription_toggle",
    "tenant_subscription_create",
    "tenant_subscription_toggle",
    "owner_commission_create",
    "owner_commission_toggle",
    "owner_payout_create",
    "owner_payout_toggle",
    "room_fixed_fee_create",
    "room_fixed_fee_toggle",
    "room_monthly_fee_create",
    "room_monthly_fee_toggle",
    "cancellation_policy_create",
    "cancellation_policy_toggle",
    "reservation_payment_policy_create",
    "reservation_payment_policy_toggle",
    "payment_deadline_exception_create",
    "payment_register",
    "payment_batch_submit",
    "payment_validate",
    "payment_reject",
    "payment_cancel",
    "settlement_generate",
    "settlement_paid",
    "settlement_cancel",
    "document_upload",
    "document_in_review",
    "document_approve",
    "document_reject",
    "document_cancel",
    "access_credentials_expire",
    "access_provision",
    "access_credential_use",
    "access_credential_revoke",
    "user_create",
    "user_update",
    "user_deactivate",
    "user_send_invitation",
}


def screen_key_for_route(url_name: str | None) -> str | None:
    if not url_name:
        return None
    exact = EXACT_ROUTE_SCREENS.get(url_name)
    if exact:
        return exact
    for prefix, screen_key in ROUTE_PREFIX_SCREENS:
        if url_name.startswith(prefix):
            return screen_key
    return None


def route_requires_edit(url_name: str | None, method: str) -> bool:
    return method not in {"GET", "HEAD", "OPTIONS"} or url_name in EDIT_ROUTE_NAMES


def default_access_for_role(role: str, screen_key: str) -> str:
    if screen_key == "permissions":
        return (
            ScreenAccessLevel.EDIT
            if role == UserRole.SUPERADMIN
            else ScreenAccessLevel.NONE
        )
    if screen_key == "users" and role not in {
        UserRole.SUPERADMIN,
        UserRole.ADMIN,
        UserRole.OWNER,
    }:
        return ScreenAccessLevel.NONE
    if screen_key in {
        "owner_subscriptions",
        "tenant_subscriptions",
        "owner_terms",
        "room_fees",
        "cancellation_policies",
        "payment_deadlines",
    }:
        if role in {UserRole.SUPERADMIN, UserRole.ADMIN}:
            return ScreenAccessLevel.EDIT
        if role == UserRole.AUDITOR:
            return ScreenAccessLevel.READ
        return ScreenAccessLevel.NONE
    if role == UserRole.AUDITOR:
        return ScreenAccessLevel.READ
    if role == UserRole.RECEPTIONIST and screen_key in {
        "room_rate_discounts",
        "tenant_doctor_discounts",
        "payments",
        "settlements",
    }:
        return ScreenAccessLevel.NONE
    if role in UserRole.values:
        return ScreenAccessLevel.EDIT
    return ScreenAccessLevel.NONE
