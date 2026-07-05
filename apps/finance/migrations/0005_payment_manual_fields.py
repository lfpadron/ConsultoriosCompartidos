"""Expand manual payments with reservation context and validation status."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def populate_payment_context(apps, schema_editor):
    payment_model = apps.get_model("finance", "Payment")

    for payment in payment_model.objects.select_related(
        "statement",
        "statement__reservation",
    ):
        reservation = payment.statement.reservation
        payment.reservation_id = reservation.pk
        payment.tenant_doctor_id = reservation.tenant_doctor_id
        payment.currency = payment.statement.currency
        if payment.paid_at:
            payment.payment_date = payment.paid_at.date()
        if payment.status == "pending":
            payment.status = "registrado"
        elif payment.status == "confirmed":
            payment.status = "validado"
            payment.validated_at = payment.paid_at or django.utils.timezone.now()
        elif payment.status == "cancelled":
            payment.status = "cancelado"
        payment.save(
            update_fields=[
                "reservation",
                "tenant_doctor",
                "currency",
                "payment_date",
                "status",
                "validated_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0003_alter_equipment_options_and_more"),
        ("finance", "0004_alter_statement_options_remove_statement_folio_and_more"),
        ("scheduling", "0003_alter_reservation_options_remove_reservation_end_at_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="payment",
            options={
                "ordering": ("-payment_date", "-created_at"),
                "verbose_name": "pago",
                "verbose_name_plural": "pagos",
            },
        ),
        migrations.AddField(
            model_name="payment",
            name="reservation",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to="scheduling.reservation",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="tenant_doctor",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to="catalog.tenantdoctorprofile",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="currency",
            field=models.CharField(default="MXN", max_length=3, verbose_name="moneda"),
        ),
        migrations.AddField(
            model_name="payment",
            name="method",
            field=models.CharField(
                choices=[
                    ("transferencia", "Transferencia"),
                    ("efectivo", "Efectivo"),
                    ("tarjeta", "Tarjeta"),
                    ("depósito", "Depósito"),
                    ("otro", "Otro"),
                ],
                default="transferencia",
                max_length=24,
                verbose_name="método",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="payment_date",
            field=models.DateField(
                default=django.utils.timezone.localdate,
                verbose_name="fecha de pago",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="receipt",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to="payment-receipts/",
                verbose_name="comprobante",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="validated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="validated_payments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="validated_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="validado en",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="rejected_reason",
            field=models.TextField(
                blank=True,
                default="",
                verbose_name="motivo de rechazo",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="payment",
            name="notes",
            field=models.TextField(blank=True, default="", verbose_name="notas"),
            preserve_default=False,
        ),
        migrations.RunPython(populate_payment_context, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="payment",
            name="paid_at",
        ),
        migrations.AlterField(
            model_name="payment",
            name="status",
            field=models.CharField(
                choices=[
                    ("registrado", "Registrado"),
                    ("validado", "Validado"),
                    ("rechazado", "Rechazado"),
                    ("cancelado", "Cancelado"),
                ],
                default="registrado",
                max_length=24,
                verbose_name="estado",
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="reservation",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to="scheduling.reservation",
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="tenant_doctor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to="catalog.tenantdoctorprofile",
            ),
        ),
    ]
