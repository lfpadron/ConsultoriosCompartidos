"""Navigation metadata for the base layout."""

from apps.identity.screen_registry import SCREEN_DEFINITIONS

NAVIGATION_ITEMS = [
    {
        "screen_key": definition.key,
        "label": definition.label,
        "url_name": definition.url_name,
        "icon": definition.icon,
    }
    for definition in SCREEN_DEFINITIONS
]

PAGE_TITLES = {
    "clinics": "Clínicas",
    "rooms": "Consultorios",
    "specialties": "Especialidades",
    "equipment": "Equipamiento",
    "owners": "Propietarios",
    "tenant_doctors": "Médicos Arrendatarios",
    "availability": "Disponibilidad y tarifas",
    "calendar_week": "Calendario",
    "calendar_quick": "Vista rápida",
    "reservations": "Reservaciones",
    "statements": "Estados de Cuenta",
    "room_rate_discounts": "Descuentos por consultorio",
    "tenant_doctor_discounts": "Descuentos por médico arrendatario",
    "payments": "Pagos",
    "settlements": "Liquidaciones",
    "documents": "Documentos",
    "access_credentials": "Accesos",
    "timeline": "Timeline",
    "reports": "Reportes",
    "users": "Usuarios",
    "profile": "Perfil",
    "permissions": "Permisos",
    "administration": "Administración",
}
