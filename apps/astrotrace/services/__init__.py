"""Small trace recording helpers."""

from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Model

from apps.astrotrace.models import TraceEvent


def record_event(
    *,
    event_type: str,
    object_label: str,
    actor: Model | None = None,
    payload: dict[str, Any] | None = None,
) -> TraceEvent:
    user_model = get_user_model()
    normalized_actor = actor if isinstance(actor, user_model) else None

    return TraceEvent.objects.create(
        event_type=event_type,
        object_label=object_label,
        actor=normalized_actor,
        payload=payload or {},
    )
