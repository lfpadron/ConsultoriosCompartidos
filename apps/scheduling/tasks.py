"""Periodic scheduling tasks."""

from celery import shared_task

from apps.scheduling.services.deadline_service import (
    expire_overdue_reservation_batches,
)


@shared_task(name="scheduling.expire_overdue_reservation_batches")
def expire_overdue_reservation_batches_task() -> int:
    """Cancel pending reservation groups whose frozen payment deadline elapsed."""

    return expire_overdue_reservation_batches()
