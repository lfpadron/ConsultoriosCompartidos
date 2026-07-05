from datetime import date, time
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import ModelChoiceField

from apps.astrotrace.models import TraceEvent
from apps.catalog.models import Clinic, ConsultingRoom, OwnerProfile
from apps.scheduling.forms import WeeklyCalendarFilterForm
from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityExceptionType,
    AvailabilityRule,
    Weekday,
)
from apps.scheduling.services import (
    BLOCK_STATUS_EXCEPTION,
    BLOCK_STATUS_FREE,
    generate_availability_blocks,
)


def create_user(email: str = "agenda@example.com") -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=email,
        password="segura-123",
        first_name="Agenda",
        last_name="Operador",
    )


def create_room(name: str = "Consultorio Agenda") -> ConsultingRoom:
    user = create_user(f"{name.lower().replace(' ', '-')}@example.com")
    clinic = Clinic.objects.create(name=f"Clínica {name}")
    owner = OwnerProfile.objects.create(user=user)
    return ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name=name,
        capacity=1,
    )


def create_rule(
    room: ConsultingRoom,
    weekday: int = Weekday.MONDAY,
    start_time: time = time(9, 0),
    end_time: time = time(12, 0),
    start_date: date = date(2026, 6, 29),
) -> AvailabilityRule:
    return AvailabilityRule.objects.create(
        room=room,
        name="Turno matutino",
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        start_date=start_date,
    )


@pytest.mark.django_db
def test_valid_availability_rule() -> None:
    room = create_room()

    rule = create_rule(room)

    assert rule.is_active is True
    assert rule.weekday == Weekday.MONDAY


@pytest.mark.django_db
def test_availability_rule_rejects_invalid_time() -> None:
    room = create_room()

    with pytest.raises(ValidationError):
        create_rule(room, start_time=time(12, 0), end_time=time(9, 0))


@pytest.mark.django_db
def test_availability_rule_rejects_invalid_end_date() -> None:
    room = create_room()

    with pytest.raises(ValidationError):
        AvailabilityRule.objects.create(
            room=room,
            name="Fecha inválida",
            weekday=Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 6, 30),
        )


@pytest.mark.django_db
def test_availability_rule_rejects_overlap() -> None:
    room = create_room()
    create_rule(room, start_time=time(9, 0), end_time=time(11, 0))

    with pytest.raises(ValidationError):
        create_rule(room, start_time=time(10, 0), end_time=time(12, 0))


@pytest.mark.django_db
def test_full_day_exception_is_valid() -> None:
    room = create_room()

    exception = AvailabilityException.objects.create(
        room=room,
        date=date(2026, 6, 29),
        exception_type=AvailabilityExceptionType.HOLIDAY,
        reason="Día festivo",
    )

    assert exception.is_full_day is True


@pytest.mark.django_db
def test_timed_exception_is_valid() -> None:
    room = create_room()

    exception = AvailabilityException.objects.create(
        room=room,
        date=date(2026, 6, 29),
        start_time=time(10, 0),
        end_time=time(11, 0),
        reason="Mantenimiento menor",
    )

    assert exception.is_full_day is False


@pytest.mark.django_db
def test_timed_exception_rejects_invalid_time() -> None:
    room = create_room()

    with pytest.raises(ValidationError):
        AvailabilityException.objects.create(
            room=room,
            date=date(2026, 6, 29),
            start_time=time(11, 0),
            end_time=time(10, 0),
            reason="Horario inválido",
        )


@pytest.mark.django_db
def test_generate_week_blocks() -> None:
    room = create_room()
    create_rule(room)

    blocks = generate_availability_blocks(room, date(2026, 6, 29), date(2026, 7, 5))

    assert len(blocks) == 1
    assert blocks[0].date == date(2026, 6, 29)
    assert blocks[0].status == BLOCK_STATUS_FREE


@pytest.mark.django_db
def test_generate_blocks_from_multi_day_availability_rule() -> None:
    room = create_room("Consultorio Multi Día")

    AvailabilityRule.objects.create(
        room=room,
        name="Lunes a jueves",
        weekdays=[
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
        ],
        start_time=time(9, 0),
        end_time=time(10, 0),
        start_date=date(2026, 6, 29),
    )

    blocks = generate_availability_blocks(room, date(2026, 6, 29), date(2026, 7, 5))

    assert [block.date for block in blocks] == [
        date(2026, 6, 29),
        date(2026, 6, 30),
        date(2026, 7, 1),
        date(2026, 7, 2),
    ]


@pytest.mark.django_db
def test_generate_blocks_applies_full_day_exception() -> None:
    room = create_room()
    create_rule(room)
    AvailabilityException.objects.create(
        room=room,
        date=date(2026, 6, 29),
        reason="Vacaciones",
        exception_type=AvailabilityExceptionType.VACATION,
    )

    blocks = generate_availability_blocks(room, date(2026, 6, 29), date(2026, 6, 29))

    assert len(blocks) == 1
    assert blocks[0].status == BLOCK_STATUS_EXCEPTION
    assert blocks[0].label == "Vacaciones"


@pytest.mark.django_db
def test_generate_blocks_applies_partial_exception() -> None:
    room = create_room()
    create_rule(room, start_time=time(9, 0), end_time=time(12, 0))
    AvailabilityException.objects.create(
        room=room,
        date=date(2026, 6, 29),
        start_time=time(10, 0),
        end_time=time(11, 0),
        reason="Mantenimiento",
        exception_type=AvailabilityExceptionType.MAINTENANCE,
    )

    blocks = generate_availability_blocks(room, date(2026, 6, 29), date(2026, 6, 29))

    assert [block.status for block in blocks] == [
        BLOCK_STATUS_FREE,
        BLOCK_STATUS_EXCEPTION,
        BLOCK_STATUS_FREE,
    ]
    assert [(block.start_time, block.end_time) for block in blocks] == [
        (time(9, 0), time(10, 0)),
        (time(10, 0), time(11, 0)),
        (time(11, 0), time(12, 0)),
    ]


@pytest.mark.django_db
def test_generate_blocks_orders_by_date_and_time() -> None:
    room = create_room()
    create_rule(
        room, weekday=Weekday.TUESDAY, start_time=time(11, 0), end_time=time(12, 0)
    )
    create_rule(
        room, weekday=Weekday.MONDAY, start_time=time(9, 0), end_time=time(10, 0)
    )

    blocks = generate_availability_blocks(room, date(2026, 6, 29), date(2026, 7, 5))

    assert [(block.date, block.start_time) for block in blocks] == [
        (date(2026, 6, 29), time(9, 0)),
        (date(2026, 6, 30), time(11, 0)),
    ]


@pytest.mark.django_db
def test_availability_list_view_returns_200(client: Any) -> None:
    user = create_user("viewer-scheduling@example.com")
    client.force_login(user)

    response = client.get("/disponibilidad/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_weekly_calendar_view_returns_200(client: Any) -> None:
    user = create_user("calendar@example.com")
    client.force_login(user)

    response = client.get("/calendario/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_weekly_calendar_filter_cascades_rooms_by_owner() -> None:
    room = create_room("Consultorio Dueño Filtro")
    other_room = create_room("Consultorio Dueño Ajeno")

    form = WeeklyCalendarFilterForm(
        data={
            "owner": str(room.owner_id),
            "date_from": "2026-06-29",
            "date_to": "2026-07-05",
        }
    )

    room_field = form.fields["room"]
    assert isinstance(room_field, ModelChoiceField)
    room_queryset = room_field.queryset
    assert room_queryset is not None
    assert room in room_queryset
    assert other_room not in room_queryset


def test_weekly_calendar_date_input_starts_on_monday_hint() -> None:
    form = WeeklyCalendarFilterForm()

    assert form.fields["date_from"].widget.attrs["data-week-start"] == "1"
    assert form.fields["date_from"].widget.attrs["lang"] == "es-MX"


@pytest.mark.django_db
def test_create_availability_rule_view_creates_rule_and_trace_event(
    client: Any,
) -> None:
    user = create_user("rule-view@example.com")
    room = create_room("Consultorio Vista")
    client.force_login(user)

    response = client.post(
        "/disponibilidad/reglas/nueva/",
        {
            "room": str(room.pk),
            "name": "Viernes tarde",
            "weekday": str(Weekday.FRIDAY),
            "start_time": "15:00",
            "end_time": "18:00",
            "start_date": "2026-06-29",
            "notes": "",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    assert AvailabilityRule.objects.filter(name="Viernes tarde").exists()
    assert TraceEvent.objects.filter(event_type="availability_rule.created").exists()


@pytest.mark.django_db
def test_availability_rule_create_view_filters_rooms_by_clinic(client: Any) -> None:
    user = create_user("rule-clinic-filter@example.com")
    room = create_room("Consultorio Clínica Filtrada")
    other_room = create_room("Consultorio Otra Clínica")
    client.force_login(user)

    response = client.get(f"/disponibilidad/reglas/nueva/?clinic={room.clinic.pk}")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Clínica" in content
    assert "Consultorio" in content
    assert "Días de semana" in content
    assert f'value="{room.pk}"' in content
    assert f'value="{other_room.pk}"' not in content


@pytest.mark.django_db
def test_create_availability_rule_view_accepts_multiple_weekdays(
    client: Any,
) -> None:
    user = create_user("rule-multiple-days@example.com")
    room = create_room("Consultorio Regla Multi Día")
    client.force_login(user)

    response = client.post(
        "/disponibilidad/reglas/nueva/",
        {
            "clinic": str(room.clinic.pk),
            "room": str(room.pk),
            "name": "Lunes a jueves",
            "weekdays": [
                str(Weekday.MONDAY),
                str(Weekday.TUESDAY),
                str(Weekday.WEDNESDAY),
                str(Weekday.THURSDAY),
            ],
            "start_time": "09:00",
            "end_time": "10:00",
            "start_date": "2026-06-29",
            "notes": "",
            "is_active": "on",
        },
    )

    rule = AvailabilityRule.objects.get(name="Lunes a jueves")
    assert response.status_code == 302
    assert rule.weekdays == [
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
    ]


@pytest.mark.django_db
def test_create_availability_exception_view_creates_trace_event(client: Any) -> None:
    user = create_user("exception-view@example.com")
    room = create_room("Consultorio Excepción")
    client.force_login(user)

    response = client.post(
        "/disponibilidad/excepciones/nueva/",
        {
            "room": str(room.pk),
            "date": "2026-06-29",
            "start_time": "10:00",
            "end_time": "11:00",
            "exception_type": AvailabilityExceptionType.MAINTENANCE,
            "reason": "Mantenimiento preventivo",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    assert TraceEvent.objects.filter(
        event_type="availability_exception.created"
    ).exists()
