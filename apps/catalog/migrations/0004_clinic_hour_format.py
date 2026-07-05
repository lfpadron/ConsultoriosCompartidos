from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_alter_equipment_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="clinic",
            name="hour_format",
            field=models.CharField(
                choices=(("24h", _("24 horas")), ("12h", _("AM/PM"))),
                default="24h",
                max_length=8,
                verbose_name=_("formato de hora"),
            ),
        ),
    ]
