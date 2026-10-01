from django.db import migrations


def set_administration_permissions(apps, schema_editor):
    RoleScreenPermission = apps.get_model("identity", "RoleScreenPermission")
    levels = {
        "superadmin": "edit",
        "admin": "edit",
        "auditor": "read",
    }
    permissions = RoleScreenPermission.objects.filter(
        screen__key="administration",
        is_deleted=False,
    )
    for permission in permissions:
        permission.access_level = levels.get(permission.role, "none")
        permission.save(update_fields=["access_level", "updated_at"])


def restore_administration_permissions(apps, schema_editor):
    RoleScreenPermission = apps.get_model("identity", "RoleScreenPermission")
    RoleScreenPermission.objects.filter(
        screen__key="administration",
        is_deleted=False,
    ).update(access_level="edit")


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0006_seed_payment_deadline_screen"),
    ]

    operations = [
        migrations.RunPython(
            set_administration_permissions,
            restore_administration_permissions,
        ),
    ]
