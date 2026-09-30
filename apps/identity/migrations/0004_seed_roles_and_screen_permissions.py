from django.db import migrations


SCREENS = (
    ("dashboard", "Dashboard", "dashboard", "bi-speedometer2"),
    ("clinics", "Clínicas", "clinics", "bi-hospital"),
    ("rooms", "Consultorios", "rooms", "bi-door-open"),
    ("specialties", "Especialidades", "specialties", "bi-clipboard2-pulse"),
    ("equipment", "Equipamiento", "equipment", "bi-tools"),
    ("owners", "Propietarios", "owners", "bi-person-badge"),
    (
        "tenant_doctors",
        "Médicos Arrendatarios",
        "tenant_doctors",
        "bi-person-vcard",
    ),
    (
        "availability",
        "Disponibilidad y tarifas",
        "availability",
        "bi-calendar-week",
    ),
    ("calendar_week", "Calendario", "calendar_week", "bi-calendar3"),
    (
        "calendar_quick",
        "Vista rápida",
        "calendar_quick",
        "bi-grid-3x3-gap",
    ),
    ("reservations", "Reservaciones", "reservations", "bi-calendar-check"),
    ("statements", "Estados de Cuenta", "statements", "bi-receipt"),
    (
        "room_rate_discounts",
        "Descuentos Consultorio",
        "room_rate_discounts",
        "bi-percent",
    ),
    (
        "tenant_doctor_discounts",
        "Descuentos Arrendatario",
        "tenant_doctor_discounts",
        "bi-tags",
    ),
    ("payments", "Pagos", "payments", "bi-credit-card"),
    ("settlements", "Liquidaciones", "settlements", "bi-bank"),
    ("documents", "Documentos", "documents", "bi-file-earmark-pdf"),
    ("access_credentials", "Accesos", "access_credentials", "bi-key"),
    ("timeline", "Timeline", "timeline", "bi-diagram-3"),
    ("reports", "Reportes", "reports", "bi-bar-chart"),
    ("users", "Usuarios", "users", "bi-people"),
    ("profile", "Perfil", "profile", "bi-person-circle"),
    ("permissions", "Permisos", "permissions", "bi-shield-lock"),
    ("administration", "Administración", "administration", "bi-gear"),
)

ROLES = (
    "superadmin",
    "admin",
    "operator",
    "receptionist",
    "owner",
    "tenant_doctor",
    "assistant",
    "auditor",
)


def default_access(role, screen_key):
    if screen_key == "permissions":
        return "edit" if role == "superadmin" else "none"
    if screen_key == "users" and role not in {"superadmin", "admin", "owner"}:
        return "none"
    if role == "auditor":
        return "read"
    if role == "receptionist" and screen_key in {
        "room_rate_discounts",
        "tenant_doctor_discounts",
        "payments",
        "settlements",
    }:
        return "none"
    return "edit"


def seed_access_control(apps, schema_editor):
    ApplicationScreen = apps.get_model("identity", "ApplicationScreen")
    CustomUser = apps.get_model("identity", "CustomUser")
    RoleScreenPermission = apps.get_model("identity", "RoleScreenPermission")
    UserRoleAssignment = apps.get_model("identity", "UserRoleAssignment")
    OwnerProfile = apps.get_model("catalog", "OwnerProfile")
    TenantDoctorProfile = apps.get_model("catalog", "TenantDoctorProfile")

    for order, (key, label, url_name, icon) in enumerate(SCREENS, start=1):
        ApplicationScreen.objects.update_or_create(
            key=key,
            defaults={
                "label": label,
                "url_name": url_name,
                "icon": icon,
                "sort_order": order,
                "is_active": True,
                "is_deleted": False,
            },
        )

    screens = {screen.key: screen for screen in ApplicationScreen.objects.all()}
    permissions = [
        RoleScreenPermission(
            screen=screens[screen_key],
            role=role,
            access_level=default_access(role, screen_key),
        )
        for screen_key in screens
        for role in ROLES
    ]
    RoleScreenPermission.objects.bulk_create(permissions, ignore_conflicts=True)

    role_pairs = {(user.pk, user.role) for user in CustomUser.objects.all()}
    role_pairs.update(
        (user_id, "owner")
        for user_id in OwnerProfile.objects.values_list("user_id", flat=True)
    )
    role_pairs.update(
        (user_id, "tenant_doctor")
        for user_id in TenantDoctorProfile.objects.values_list("user_id", flat=True)
    )
    UserRoleAssignment.objects.bulk_create(
        [
            UserRoleAssignment(user_id=user_id, role=role)
            for user_id, role in role_pairs
        ],
        ignore_conflicts=True,
    )


def reverse_seed(apps, schema_editor):
    apps.get_model("identity", "RoleScreenPermission").objects.all().delete()
    apps.get_model("identity", "ApplicationScreen").objects.all().delete()
    apps.get_model("identity", "UserRoleAssignment").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0003_applicationscreen_rolescreenpermission_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_access_control, reverse_seed),
    ]
