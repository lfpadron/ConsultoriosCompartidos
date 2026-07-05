from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.astrotrace.models import TraceEvent
from apps.catalog.models import Clinic, ConsultingRoom, OwnerProfile
from apps.finance.models import PriceType, RateRule
from apps.finance.services.pricing_engine import (
    PricingConfigurationError,
    calculate_block_price,
)
from apps.scheduling.models import AvailabilityRule, Weekday


def create_user(email: str = "finance@example.com") -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=email,
        password="segura-123",
        first_name="Finanzas",
        last_name="Operador",
    )


def create_room(name: str = "Consultorio Finanzas") -> ConsultingRoom:
    user = create_user(f"{name.lower().replace(' ', '-')}@example.com")
    clinic = Clinic.objects.create(name=f"Clínica {name}")
    owner = OwnerProfile.objects.create(user=user)
    return ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name=name,
        capacity=1,
    )


def create_rate_rule(
    room: ConsultingRoom,
    *,
    name: str = "Tarifa base",
    weekdays: list[int] | None = None,
    start_time: time = time(8, 0),
    end_time: time = time(13, 0),
    price_type: str = PriceType.HOURLY,
    amount: Decimal = Decimal("75.00"),
    priority: int = 1,
) -> RateRule:
    return RateRule.objects.create(
        room=room,
        name=name,
        weekdays=weekdays or [Weekday.MONDAY],
        start_time=start_time,
        end_time=end_time,
        start_date=date(2026, 6, 29),
        price_type=price_type,
        amount=amount,
        currency="MXN",
        priority=priority,
    )


@pytest.mark.django_db
def test_valid_hourly_rate_rule() -> None:
    room = create_room()

    rule = create_rate_rule(room)

    assert rule.price_type == PriceType.HOURLY
    assert rule.amount == Decimal("75.00")


@pytest.mark.django_db
def test_valid_block_rate_rule() -> None:
    room = create_room()

    rule = create_rate_rule(
        room,
        price_type=PriceType.BLOCK,
        amount=Decimal("150.00"),
    )

    assert rule.price_type == PriceType.BLOCK


@pytest.mark.django_db
def test_rate_rule_rejects_invalid_time() -> None:
    room = create_room()

    with pytest.raises(ValidationError):
        create_rate_rule(room, start_time=time(13, 0), end_time=time(8, 0))


@pytest.mark.django_db
def test_rate_rule_rejects_invalid_end_date() -> None:
    room = create_room()

    with pytest.raises(ValidationError):
        RateRule.objects.create(
            room=room,
            name="Fecha inválida",
            weekdays=[Weekday.MONDAY],
            start_time=time(8, 0),
            end_time=time(13, 0),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 6, 30),
            price_type=PriceType.HOURLY,
            amount=Decimal("75.00"),
            currency="MXN",
            priority=1,
        )


@pytest.mark.django_db
def test_rate_rule_rejects_negative_amount() -> None:
    room = create_room()

    with pytest.raises(ValidationError):
        create_rate_rule(room, amount=Decimal("-1.00"))


@pytest.mark.django_db
def test_rate_rule_rejects_exact_duplicate() -> None:
    room = create_room()
    create_rate_rule(room)

    with pytest.raises(ValidationError):
        create_rate_rule(room)


@pytest.mark.django_db
def test_rate_rule_allows_overlap_with_different_priority() -> None:
    room = create_room()
    create_rate_rule(room, start_time=time(8, 0), end_time=time(13, 0), priority=1)

    rule = create_rate_rule(
        room,
        name="Tarifa prioritaria",
        start_time=time(10, 0),
        end_time=time(12, 0),
        amount=Decimal("95.00"),
        priority=2,
    )

    assert rule.priority == 2


@pytest.mark.django_db
def test_rate_rule_rejects_overlap_with_same_priority() -> None:
    room = create_room()
    create_rate_rule(room, start_time=time(8, 0), end_time=time(13, 0), priority=1)

    with pytest.raises(ValidationError):
        create_rate_rule(
            room,
            name="Traslape",
            start_time=time(10, 0),
            end_time=time(12, 0),
            amount=Decimal("95.00"),
            priority=1,
        )


@pytest.mark.django_db
def test_calculate_hourly_block_price() -> None:
    room = create_room()
    rule = create_rate_rule(room, amount=Decimal("75.00"))

    result = calculate_block_price(room, date(2026, 6, 29), time(8, 0), time(13, 0))

    assert result.applied_rule == rule
    assert result.duration_hours == Decimal("5.00")
    assert result.subtotal == Decimal("375.00")


@pytest.mark.django_db
def test_calculate_block_price() -> None:
    room = create_room()
    create_rate_rule(
        room,
        price_type=PriceType.BLOCK,
        amount=Decimal("150.00"),
    )

    result = calculate_block_price(room, date(2026, 6, 29), time(8, 0), time(13, 0))

    assert result.subtotal == Decimal("150.00")
    assert result.price_type == PriceType.BLOCK


@pytest.mark.django_db
def test_pricing_engine_uses_highest_priority() -> None:
    room = create_room()
    create_rate_rule(room, amount=Decimal("75.00"), priority=1)
    high_priority = create_rate_rule(
        room,
        name="Tarifa alta prioridad",
        start_time=time(8, 0),
        end_time=time(13, 0),
        amount=Decimal("90.00"),
        priority=2,
    )

    result = calculate_block_price(room, date(2026, 6, 29), time(8, 0), time(13, 0))

    assert result.applied_rule == high_priority
    assert result.subtotal == Decimal("450.00")


@pytest.mark.django_db
def test_pricing_engine_raises_on_priority_tie() -> None:
    room = create_room()
    create_rate_rule(room, amount=Decimal("75.00"), priority=1)
    second_rule = create_rate_rule(
        room,
        name="Tarifa temporal",
        start_time=time(8, 0),
        end_time=time(13, 0),
        amount=Decimal("90.00"),
        priority=2,
    )
    RateRule.objects.filter(pk=second_rule.pk).update(priority=1)

    with pytest.raises(PricingConfigurationError):
        calculate_block_price(room, date(2026, 6, 29), time(8, 0), time(13, 0))


@pytest.mark.django_db
def test_pricing_engine_returns_controlled_result_without_rate() -> None:
    room = create_room()

    result = calculate_block_price(room, date(2026, 6, 29), time(8, 0), time(13, 0))

    assert result.applied_rule is None
    assert result.subtotal is None
    assert result.explanation == "Sin tarifa configurada"


@pytest.mark.django_db
def test_calendar_shows_price_for_free_block(client: Any) -> None:
    user = create_user("calendar-price@example.com")
    room = create_room("Consultorio Precio")
    AvailabilityRule.objects.create(
        room=room,
        name="Disponible lunes",
        weekday=Weekday.MONDAY,
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=date(2026, 6, 29),
    )
    create_rate_rule(room, amount=Decimal("75.00"))
    client.force_login(user)

    response = client.get(f"/calendario/?week=2026-06-29&room={room.pk}")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Subtotal estimado: 375.00 MXN" in content


@pytest.mark.django_db
def test_calendar_shows_missing_rate_message(client: Any) -> None:
    user = create_user("calendar-no-price@example.com")
    room = create_room("Consultorio Sin Precio")
    AvailabilityRule.objects.create(
        room=room,
        name="Disponible lunes",
        weekday=Weekday.MONDAY,
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=date(2026, 6, 29),
    )
    client.force_login(user)

    response = client.get(f"/calendario/?week=2026-06-29&room={room.pk}")

    assert response.status_code == 200
    assert "Sin tarifa configurada" in response.content.decode()


@pytest.mark.django_db
def test_rate_rule_crud_generates_trace_events(client: Any) -> None:
    user = create_user("rate-crud@example.com")
    room = create_room("Consultorio CRUD Tarifa")
    client.force_login(user)

    create_response = client.post(
        "/tarifas/nueva/",
        {
            "room": str(room.pk),
            "name": "Tarifa CRUD",
            "weekdays": [str(Weekday.MONDAY)],
            "start_time": "08:00",
            "end_time": "13:00",
            "start_date": "2026-06-29",
            "price_type": PriceType.HOURLY,
            "amount": "75.00",
            "currency": "MXN",
            "priority": "1",
            "notes": "",
            "is_active": "on",
        },
    )
    rule = RateRule.objects.get(name="Tarifa CRUD")

    update_response = client.post(
        f"/tarifas/{rule.pk}/editar/",
        {
            "room": str(room.pk),
            "name": "Tarifa CRUD actualizada",
            "weekdays": [str(Weekday.MONDAY)],
            "start_time": "08:00",
            "end_time": "13:00",
            "start_date": "2026-06-29",
            "price_type": PriceType.HOURLY,
            "amount": "80.00",
            "currency": "MXN",
            "priority": "1",
            "notes": "",
            "is_active": "on",
        },
    )
    deactivate_response = client.post(f"/tarifas/{rule.pk}/desactivar/")

    assert create_response.status_code == 302
    assert update_response.status_code == 302
    assert deactivate_response.status_code == 302
    assert TraceEvent.objects.filter(event_type="rate_rule.created").exists()
    assert TraceEvent.objects.filter(event_type="rate_rule.updated").exists()
    assert TraceEvent.objects.filter(event_type="rate_rule.deactivated").exists()


@pytest.mark.django_db
def test_rate_rule_create_view_filters_rooms_by_clinic(client: Any) -> None:
    user = create_user("rate-clinic-filter@example.com")
    room = create_room("Consultorio Tarifa Filtrada")
    other_room = create_room("Consultorio Tarifa Otra Clínica")
    client.force_login(user)

    response = client.get(f"/tarifas/nueva/?clinic={room.clinic.pk}")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Clínica" in content
    assert "Consultorio" in content
    assert "Días de semana" in content
    assert f'value="{room.pk}"' in content
    assert f'value="{other_room.pk}"' not in content


@pytest.mark.django_db
def test_rate_rule_create_view_accepts_multiple_weekdays(client: Any) -> None:
    user = create_user("rate-multiple-days@example.com")
    room = create_room("Consultorio Tarifa Multi Día")
    client.force_login(user)

    response = client.post(
        "/tarifas/nueva/",
        {
            "clinic": str(room.clinic.pk),
            "room": str(room.pk),
            "name": "Tarifa lunes a jueves",
            "weekdays": [
                str(Weekday.MONDAY),
                str(Weekday.TUESDAY),
                str(Weekday.WEDNESDAY),
                str(Weekday.THURSDAY),
            ],
            "start_time": "08:00",
            "end_time": "13:00",
            "start_date": "2026-06-29",
            "price_type": PriceType.HOURLY,
            "amount": "75.00",
            "currency": "MXN",
            "priority": "1",
            "notes": "",
            "is_active": "on",
        },
    )

    rule = RateRule.objects.get(name="Tarifa lunes a jueves")
    assert response.status_code == 302
    assert rule.weekdays == [
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
    ]
