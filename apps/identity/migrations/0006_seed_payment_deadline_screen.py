from django.db import migrations


SCREEN = (
    "payment_deadlines",
    "Plazos de Pago",
    "payment_deadlines",
    "bi-hourglass-split",
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


def seed_payment_deadline_screen(apps, schema_editor):
    ApplicationScreen = apps.get_model("identity", "ApplicationScreen")
    RoleScreenPermission = apps.get_model("identity", "RoleScreenPermission")
    key, label, url_name, icon = SCREEN
    screen, _ = ApplicationScreen.objects.update_or_create(
        key=key,
        defaults={
            "label": label,
            "url_name": url_name,
            "icon": icon,
            "sort_order": 30,
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


def reverse_payment_deadline_screen(apps, schema_editor):
    ApplicationScreen = apps.get_model("identity", "ApplicationScreen")
    RoleScreenPermission = apps.get_model("identity", "RoleScreenPermission")
    RoleScreenPermission.objects.filter(screen__key=SCREEN[0]).delete()
    ApplicationScreen.objects.filter(key=SCREEN[0]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0005_seed_billing_screen_permissions"),
        ("scheduling", "0008_alter_reservationbatch_deadline_policy_and_more"),
    ]

    operations = [
        migrations.RunPython(
            seed_payment_deadline_screen,
            reverse_payment_deadline_screen,
        ),
    ]
