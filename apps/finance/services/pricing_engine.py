"""Initial pricing engine for availability blocks."""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from apps.catalog.models import ConsultingRoom
from apps.core.constants import DEFAULT_CURRENCY
from apps.finance.models import PriceType, RateRule


class PricingConfigurationError(Exception):
    """Raised when active pricing rules have an ambiguous priority."""


@dataclass(frozen=True)
class BlockPrice:
    room: ConsultingRoom
    date: date
    start_time: time
    end_time: time
    duration_hours: Decimal
    applied_rule: RateRule | None
    price_type: str | None
    base_rate: Decimal | None
    subtotal: Decimal | None
    currency: str
    explanation: str


def calculate_block_price(
    consulting_room: ConsultingRoom,
    date: date,
    start_time: time,
    end_time: time,
) -> BlockPrice:
    duration_hours = _calculate_duration_hours(start_time, end_time)
    applicable_rules = list(
        get_applicable_rate_rules(
            consulting_room=consulting_room,
            date=date,
            start_time=start_time,
            end_time=end_time,
        )
    )

    if not applicable_rules:
        return BlockPrice(
            room=consulting_room,
            date=date,
            start_time=start_time,
            end_time=end_time,
            duration_hours=duration_hours,
            applied_rule=None,
            price_type=None,
            base_rate=None,
            subtotal=None,
            currency=DEFAULT_CURRENCY,
            explanation="Sin tarifa configurada",
        )

    max_priority = max(rule.priority for rule in applicable_rules)
    winning_rules = [rule for rule in applicable_rules if rule.priority == max_priority]
    if len(winning_rules) > 1:
        msg = "Empate de prioridad entre reglas tarifarias aplicables."
        raise PricingConfigurationError(msg)

    rule = winning_rules[0]
    subtotal = _calculate_subtotal(rule, duration_hours)
    explanation = _build_explanation(rule, duration_hours, subtotal)

    return BlockPrice(
        room=consulting_room,
        date=date,
        start_time=start_time,
        end_time=end_time,
        duration_hours=duration_hours,
        applied_rule=rule,
        price_type=rule.price_type,
        base_rate=rule.amount,
        subtotal=subtotal,
        currency=rule.currency,
        explanation=explanation,
    )


def get_applicable_rate_rules(
    *,
    consulting_room: ConsultingRoom,
    date: date,
    start_time: time,
    end_time: time,
) -> list[RateRule]:
    candidate_rules = RateRule.objects.filter(
        room=consulting_room,
        is_active=True,
        is_deleted=False,
        start_time__lte=start_time,
        end_time__gte=end_time,
        start_date__lte=date,
    ).filter(end_date__isnull=True) | RateRule.objects.filter(
        room=consulting_room,
        is_active=True,
        is_deleted=False,
        start_time__lte=start_time,
        end_time__gte=end_time,
        start_date__lte=date,
        end_date__gte=date,
    )
    weekday = date.weekday()
    return [rule for rule in candidate_rules if weekday in rule.weekdays]


def validate_rate_rule_conflicts(rate_rule: RateRule) -> None:
    rate_rule.full_clean()


def _calculate_duration_hours(start_time: time, end_time: time) -> Decimal:
    start = datetime.combine(date.min, start_time)
    end = datetime.combine(date.min, end_time)
    minutes = Decimal((end - start).total_seconds()) / Decimal("60")
    return (minutes / Decimal("60")).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _calculate_subtotal(rule: RateRule, duration_hours: Decimal) -> Decimal:
    if rule.price_type == PriceType.HOURLY:
        subtotal = rule.amount * duration_hours
    else:
        subtotal = rule.amount
    return subtotal.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _build_explanation(
    rule: RateRule,
    duration_hours: Decimal,
    subtotal: Decimal,
) -> str:
    if rule.price_type == PriceType.HOURLY:
        return (
            f"{rule.name}: {rule.amount} {rule.currency}/h x "
            f"{duration_hours} h = {subtotal} {rule.currency}"
        )
    return f"{rule.name}: {rule.amount} {rule.currency} por bloque"
