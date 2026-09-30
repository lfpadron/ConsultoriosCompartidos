from django.db import migrations


SCREENS = (
    (
        "owner_subscriptions",
        "Suscripciones Propietarios",
        "owner_subscriptions",
        "bi-person-check",
    ),
    (
        "tenant_subscriptions",
        "Suscripciones Arrendatarios",
        "tenant_subscriptions",
        "bi-person-vcard-fill",
    ),
    (
        "owner_terms",
        "Comisiones y Pagos",
        "owner_terms",
        "bi-cash-coin",
    ),
    (
        "room_fees",
        "Cuotas Consultorios",
        "room_fees",
        "bi-building-gear",
    ),
    (
        "cancellation_policies",
        "Políticas Cancelación",
        "cancellation_policies",
        "bi-calendar-x",
    ),
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


def default_access(role):
    if role in {"superadmin", "admin"}:
        return "edit"
    if role == "auditor":
        return "read"
    return "none"


def seed_billing_screens(apps, schema_editor):
    ApplicationScreen = apps.get_model("identity", "ApplicationScreen")
    RoleScreenPermission = apps.get_model("identity", "RoleScreenPermission")

    for order, (key, label, url_name, icon) in enumerate(SCREENS, start=25):
        screen, _ = ApplicationScreen.objects.update_or_create(
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
        for role in ROLES:
            RoleScreenPermission.objects.update_or_create(
                screen=screen,
                role=role,
                defaults={
                    "access_level": default_access(role),
                    "is_active": True,
                    "is_deleted": False,
                },
            )


def reverse_billing_screens(apps, schema_editor):
    ApplicationScreen = apps.get_model("identity", "ApplicationScreen")
    RoleScreenPermission = apps.get_model("identity", "RoleScreenPermission")
    screen_keys = [screen[0] for screen in SCREENS]
    RoleScreenPermission.objects.filter(screen__key__in=screen_keys).delete()
    ApplicationScreen.objects.filter(key__in=screen_keys).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0001_initial"),
        ("identity", "0004_seed_roles_and_screen_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_billing_screens, reverse_billing_screens),
    ]
