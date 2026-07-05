"""Availability block generation and selectors."""

from collections import defaultdict
from datetime import date, timedelta

from django.db.models import QuerySet

from apps.catalog.models import Clinic, ConsultingRoom
from apps.scheduling.models import (
    ACTIVE_RESERVATION_STATUSES,
    AvailabilityException,
    AvailabilityRule,
    Reservation,
    ReservationBlock,
    rule_weekdays,
)

BLOCK_STATUS_FREE = "libre"
BLOCK_STATUS_EXCEPTION = "bloqueado_por_excepcion"
BLOCK_STATUS_RESERVED_FUTURE = "reservado_futuro"

BLOCK_ORIGIN_RULE = "regla"
BLOCK_ORIGIN_EXCEPTION = "excepción"
BLOCK_ORIGIN_RESERVATION = "reservación"


def generate_availability_blocks(
    consulting_room: ConsultingRoom,
    start_date: date,
    end_date: date,
) -> list[ReservationBlock]:
    rules = _active_rules_for_range(consulting_room, start_date, end_date)
    exceptions = _active_exceptions_for_range(consulting_room, start_date, end_date)
    reservations = _active_reservations_for_range(consulting_room, start_date, end_date)
    exceptions_by_date = _group_exceptions_by_date(exceptions)
    reservations_by_date = _group_reservations_by_date(reservations)
    blocks: list[ReservationBlock] = []

    for current_date in _date_range(start_date, end_date):
        for rule in rules:
            if not _rule_applies_on(rule, current_date):
                continue

            rule_block = ReservationBlock(
                room=consulting_room,
                date=current_date,
                start_time=rule.start_time,
                end_time=rule.end_time,
                status=BLOCK_STATUS_FREE,
                origin=BLOCK_ORIGIN_RULE,
                label=rule.name,
            )
            blocks.extend(
                _apply_reservations(
                    _apply_exceptions(
                        rule_block,
                        exceptions_by_date.get(current_date, []),
                    ),
                    reservations_by_date.get(current_date, []),
                )
            )

    return sorted(
        blocks,
        key=lambda block: (block.date, block.start_time, block.end_time),
    )


def get_week_start(selected_date: date) -> date:
    return selected_date - timedelta(days=selected_date.weekday())


def get_week_dates(week_start: date) -> list[date]:
    return [week_start + timedelta(days=offset) for offset in range(7)]


def get_weekly_availability_for_room(
    consulting_room: ConsultingRoom,
    week_start: date,
) -> dict[date, list[ReservationBlock]]:
    week_dates = get_week_dates(week_start)
    blocks = generate_availability_blocks(
        consulting_room,
        week_dates[0],
        week_dates[-1],
    )
    grouped: dict[date, list[ReservationBlock]] = {day: [] for day in week_dates}
    for block in blocks:
        grouped[block.date].append(block)
    return grouped


def get_weekly_availability_for_clinic(
    clinic: Clinic,
    week_start: date,
) -> dict[ConsultingRoom, dict[date, list[ReservationBlock]]]:
    rooms = clinic.consulting_rooms.filter(is_active=True, is_deleted=False).order_by(
        "name"
    )
    return {room: get_weekly_availability_for_room(room, week_start) for room in rooms}


def _active_rules_for_range(
    consulting_room: ConsultingRoom,
    start_date: date,
    end_date: date,
) -> QuerySet[AvailabilityRule]:
    return AvailabilityRule.objects.filter(
        room=consulting_room,
        is_active=True,
        is_deleted=False,
        start_date__lte=end_date,
    ).filter(end_date__isnull=True) | AvailabilityRule.objects.filter(
        room=consulting_room,
        is_active=True,
        is_deleted=False,
        start_date__lte=end_date,
        end_date__gte=start_date,
    )


def _active_exceptions_for_range(
    consulting_room: ConsultingRoom,
    start_date: date,
    end_date: date,
) -> QuerySet[AvailabilityException]:
    return AvailabilityException.objects.filter(
        room=consulting_room,
        is_active=True,
        is_deleted=False,
        date__gte=start_date,
        date__lte=end_date,
    ).order_by("date", "start_time")


def _active_reservations_for_range(
    consulting_room: ConsultingRoom,
    start_date: date,
    end_date: date,
) -> QuerySet[Reservation]:
    return Reservation.objects.filter(
        room=consulting_room,
        status__in=ACTIVE_RESERVATION_STATUSES,
        is_deleted=False,
        date__gte=start_date,
        date__lte=end_date,
    ).order_by("date", "start_time")


def _group_exceptions_by_date(
    exceptions: QuerySet[AvailabilityException],
) -> dict[date, list[AvailabilityException]]:
    grouped: dict[date, list[AvailabilityException]] = defaultdict(list)
    for exception in exceptions:
        grouped[exception.date].append(exception)
    return grouped


def _group_reservations_by_date(
    reservations: QuerySet[Reservation],
) -> dict[date, list[Reservation]]:
    grouped: dict[date, list[Reservation]] = defaultdict(list)
    for reservation in reservations:
        grouped[reservation.date].append(reservation)
    return grouped


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _rule_applies_on(rule: AvailabilityRule, current_date: date) -> bool:
    if current_date.weekday() not in rule_weekdays(rule):
        return False
    if current_date < rule.start_date:
        return False
    return not rule.end_date or current_date <= rule.end_date


def _apply_exceptions(
    block: ReservationBlock,
    exceptions: list[AvailabilityException],
) -> list[ReservationBlock]:
    segments = [block]

    for exception in exceptions:
        if exception.is_full_day:
            return [
                ReservationBlock(
                    room=block.room,
                    date=block.date,
                    start_time=block.start_time,
                    end_time=block.end_time,
                    status=BLOCK_STATUS_EXCEPTION,
                    origin=BLOCK_ORIGIN_EXCEPTION,
                    label=exception.reason,
                )
            ]

        segments = _apply_partial_exception(segments, exception)

    return segments


def _apply_reservations(
    blocks: list[ReservationBlock],
    reservations: list[Reservation],
) -> list[ReservationBlock]:
    segments = blocks
    for reservation in reservations:
        segments = _apply_partial_reservation(segments, reservation)
    return segments


def _apply_partial_reservation(
    segments: list[ReservationBlock],
    reservation: Reservation,
) -> list[ReservationBlock]:
    updated_segments: list[ReservationBlock] = []

    for segment in segments:
        if segment.status != BLOCK_STATUS_FREE:
            updated_segments.append(segment)
            continue

        if (
            reservation.start_time >= segment.end_time
            or reservation.end_time <= segment.start_time
        ):
            updated_segments.append(segment)
            continue

        reserved_start = max(segment.start_time, reservation.start_time)
        reserved_end = min(segment.end_time, reservation.end_time)

        if segment.start_time < reserved_start:
            updated_segments.append(
                ReservationBlock(
                    room=segment.room,
                    date=segment.date,
                    start_time=segment.start_time,
                    end_time=reserved_start,
                    status=BLOCK_STATUS_FREE,
                    origin=BLOCK_ORIGIN_RULE,
                    label=segment.label,
                )
            )

        updated_segments.append(
            ReservationBlock(
                room=segment.room,
                date=segment.date,
                start_time=reserved_start,
                end_time=reserved_end,
                status=BLOCK_STATUS_RESERVED_FUTURE,
                origin=BLOCK_ORIGIN_RESERVATION,
                label="Reservación activa",
                reservation=reservation,
            )
        )

        if reserved_end < segment.end_time:
            updated_segments.append(
                ReservationBlock(
                    room=segment.room,
                    date=segment.date,
                    start_time=reserved_end,
                    end_time=segment.end_time,
                    status=BLOCK_STATUS_FREE,
                    origin=BLOCK_ORIGIN_RULE,
                    label=segment.label,
                )
            )

    return updated_segments


def _apply_partial_exception(
    segments: list[ReservationBlock],
    exception: AvailabilityException,
) -> list[ReservationBlock]:
    updated_segments: list[ReservationBlock] = []

    for segment in segments:
        if segment.status != BLOCK_STATUS_FREE:
            updated_segments.append(segment)
            continue

        exception_start = exception.start_time
        exception_end = exception.end_time
        if exception_start is None or exception_end is None:
            updated_segments.append(segment)
            continue

        if exception_start >= segment.end_time or exception_end <= segment.start_time:
            updated_segments.append(segment)
            continue

        blocked_start = max(segment.start_time, exception_start)
        blocked_end = min(segment.end_time, exception_end)

        if segment.start_time < blocked_start:
            updated_segments.append(
                ReservationBlock(
                    room=segment.room,
                    date=segment.date,
                    start_time=segment.start_time,
                    end_time=blocked_start,
                    status=BLOCK_STATUS_FREE,
                    origin=BLOCK_ORIGIN_RULE,
                    label=segment.label,
                )
            )

        updated_segments.append(
            ReservationBlock(
                room=segment.room,
                date=segment.date,
                start_time=blocked_start,
                end_time=blocked_end,
                status=BLOCK_STATUS_EXCEPTION,
                origin=BLOCK_ORIGIN_EXCEPTION,
                label=exception.reason,
            )
        )

        if blocked_end < segment.end_time:
            updated_segments.append(
                ReservationBlock(
                    room=segment.room,
                    date=segment.date,
                    start_time=blocked_end,
                    end_time=segment.end_time,
                    status=BLOCK_STATUS_FREE,
                    origin=BLOCK_ORIGIN_RULE,
                    label=segment.label,
                )
            )

    return updated_segments
