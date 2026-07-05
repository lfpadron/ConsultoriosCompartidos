from typing import Any

from django.db import migrations, models


def forwards(apps: Any, schema_editor: Any) -> None:
    availability_rule = apps.get_model("scheduling", "AvailabilityRule")
    for rule in availability_rule.objects.all().only("pk", "weekday", "weekdays"):
        if not rule.weekdays:
            rule.weekdays = [rule.weekday]
            rule.save(update_fields=["weekdays"])


def backwards(apps: Any, schema_editor: Any) -> None:
    availability_rule = apps.get_model("scheduling", "AvailabilityRule")
    for rule in availability_rule.objects.all().only("pk", "weekday", "weekdays"):
        if rule.weekdays:
            rule.weekday = rule.weekdays[0]
            rule.save(update_fields=["weekday"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "scheduling",
            "0003_alter_reservation_options_remove_reservation_end_at_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="availabilityrule",
            name="weekdays",
            field=models.JSONField(
                blank=True,
                default=list,
                verbose_name="días de semana",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
