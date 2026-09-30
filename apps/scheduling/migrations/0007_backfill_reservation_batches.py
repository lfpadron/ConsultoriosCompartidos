import uuid

from django.db import migrations


def backfill_reservation_batches(apps, schema_editor):
    Reservation = apps.get_model("scheduling", "Reservation")
    ReservationBatch = apps.get_model("scheduling", "ReservationBatch")
    Statement = apps.get_model("finance", "Statement")

    for reservation in Reservation.objects.filter(batch__isnull=True).iterator():
        statement = (
            Statement.objects.filter(reservation_id=reservation.pk, status="vigente")
            .order_by("-version")
            .first()
        )
        if reservation.status == "cancelada":
            batch_status = "cancelled"
        elif reservation.status in {"confirmada", "finalizada"}:
            batch_status = "confirmed"
        else:
            batch_status = "requested"

        batch = ReservationBatch.objects.create(
            reference=uuid.uuid4().hex[:12].upper(),
            room_id=reservation.room_id,
            tenant_doctor_id=reservation.tenant_doctor_id,
            batch_type="single",
            status=batch_status,
            recurrence_rule={
                "legacy": True,
                "reservation_id": str(reservation.pk),
            },
            occurrence_count=1,
            currency=statement.currency if statement is not None else "MXN",
            tariff_total=reservation.tariff_total,
            tariff_final=reservation.tariff_final,
            requested_at=reservation.requested_at,
            notes=reservation.notes,
            created_by_id=reservation.created_by_id,
            updated_by_id=reservation.updated_by_id,
            is_active=reservation.is_active,
            is_deleted=reservation.is_deleted,
        )
        Reservation.objects.filter(pk=reservation.pk).update(batch_id=batch.pk)


def reverse_backfill_reservation_batches(apps, schema_editor):
    Reservation = apps.get_model("scheduling", "Reservation")
    ReservationBatch = apps.get_model("scheduling", "ReservationBatch")
    legacy_batches = ReservationBatch.objects.filter(recurrence_rule__legacy=True)
    Reservation.objects.filter(batch__in=legacy_batches).update(batch=None)
    legacy_batches.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0007_statement_room_discount_percentage_and_more"),
        ("scheduling", "0006_reservationbatch_reservation_batch_and_more"),
    ]

    operations = [
        migrations.RunPython(
            backfill_reservation_batches,
            reverse_backfill_reservation_batches,
        ),
    ]
