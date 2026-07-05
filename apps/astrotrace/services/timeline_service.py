"""Timeline selectors and presentation helpers for AstroTrace events."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import Model

from apps.astrotrace.models import TraceEvent
from apps.catalog.models import ConsultingRoom, OwnerProfile, TenantDoctorProfile
from apps.finance.models import Payment, RateRule, Settlement
from apps.scheduling.models import AvailabilityRule, Reservation
from apps.vault.models import DocumentAsset

LEVEL_INFO = "informativo"
LEVEL_OPERATIONAL = "operativo"
LEVEL_FINANCIAL = "financiero"
LEVEL_LEGAL = "legal"

LEVEL_CHOICES = (
    (LEVEL_INFO, "Informativo"),
    (LEVEL_OPERATIONAL, "Operativo"),
    (LEVEL_FINANCIAL, "Financiero"),
    (LEVEL_LEGAL, "Legal"),
)

LEVEL_STYLES = {
    LEVEL_INFO: ("bi-info-circle", "text-bg-secondary"),
    LEVEL_OPERATIONAL: ("bi-gear", "text-bg-primary"),
    LEVEL_FINANCIAL: ("bi-cash-coin", "text-bg-warning"),
    LEVEL_LEGAL: ("bi-shield-check", "text-bg-danger"),
}

MODULE_LABELS = {
    "access": "Accesos",
    "availability_exception": "Disponibilidad",
    "availability_rule": "Disponibilidad",
    "clinic": "Catálogo",
    "consulting_room": "Catálogo",
    "document": "Bóveda",
    "equipment": "Catálogo",
    "owner": "Catálogo",
    "payment": "Pagos",
    "rate_rule": "Tarifas",
    "report": "Reportes",
    "reservation": "Reservaciones",
    "settlement": "Liquidaciones",
    "specialty": "Catálogo",
    "statement": "Estados de Cuenta",
    "tenant_doctor": "Catálogo",
}

ACTION_LABELS = {
    "approved": "aprobado",
    "cancelled": "cancelado",
    "confirmed": "confirmado",
    "created": "creado",
    "deactivated": "desactivado",
    "exported": "exportado",
    "expired": "expirado",
    "generated": "generado",
    "in_review": "en revisión",
    "marked_paid": "marcada como pagada",
    "paid": "pagada",
    "provisioned": "habilitado",
    "received": "recibido",
    "registered": "registrado",
    "rejected": "rechazado",
    "requested": "solicitada",
    "revoked": "revocado",
    "updated": "actualizado",
    "used": "usado",
    "validated": "validado",
    "versioned": "versionado",
}


@dataclass(frozen=True)
class TimelineItem:
    event: TraceEvent
    level: str
    module: str
    action: str
    title: str
    description: str
    actor_label: str
    actor_role: str
    icon: str
    badge_class: str
    related_object: str
    metadata: dict[str, Any]
    hash_value: str


def get_timeline_for_object(obj: Model) -> list[TimelineItem]:
    if isinstance(obj, Reservation):
        return get_timeline_for_reservation(obj)
    if isinstance(obj, ConsultingRoom):
        return get_timeline_for_consulting_room(obj)
    if isinstance(obj, OwnerProfile):
        return get_timeline_for_owner(obj)
    if isinstance(obj, TenantDoctorProfile):
        return get_timeline_for_tenant_doctor(obj)
    if isinstance(obj, Payment):
        return _timeline_for_model_ids(
            _base_related_identifiers(obj)
            | {("scheduling.Reservation", str(obj.reservation_id))},
            extra_payload_matches={"reservation_id": str(obj.reservation_id)},
        )
    if isinstance(obj, Settlement):
        return _timeline_for_model_ids(
            _base_related_identifiers(obj)
            | {("scheduling.Reservation", str(obj.reservation_id))},
            extra_payload_matches={"reservation_id": str(obj.reservation_id)},
        )
    if isinstance(obj, DocumentAsset):
        return _timeline_for_model_ids(_base_related_identifiers(obj))
    return _timeline_for_model_ids(_base_related_identifiers(obj))


def get_timeline_for_reservation(reservation: Reservation) -> list[TimelineItem]:
    identifiers = _base_related_identifiers(reservation)
    identifiers.update(
        ("finance.Statement", str(item.pk))
        for item in reservation.statements.filter(is_deleted=False)
    )
    identifiers.update(
        ("finance.Payment", str(item.pk))
        for item in reservation.payments.filter(is_deleted=False)
    )
    identifiers.update(
        ("finance.Settlement", str(item.pk))
        for item in reservation.settlements.filter(is_deleted=False)
    )
    identifiers.update(
        ("vault.DocumentAsset", str(item.pk))
        for item in DocumentAsset.objects.filter(
            reservation=reservation,
            is_deleted=False,
        )
    )
    identifiers.update(
        ("vault.DocumentAsset", str(item.pk))
        for item in DocumentAsset.objects.filter(
            payment__reservation=reservation,
            is_deleted=False,
        )
    )
    identifiers.update(
        ("vault.DocumentAsset", str(item.pk))
        for item in DocumentAsset.objects.filter(
            settlement__reservation=reservation,
            is_deleted=False,
        )
    )
    return _timeline_for_model_ids(
        identifiers,
        extra_payload_matches={"reservation_id": str(reservation.pk)},
    )


def get_timeline_for_consulting_room(room: ConsultingRoom) -> list[TimelineItem]:
    identifiers = _base_related_identifiers(room)
    identifiers.update(
        ("scheduling.AvailabilityRule", str(item.pk))
        for item in AvailabilityRule.objects.filter(room=room, is_deleted=False)
    )
    identifiers.update(
        ("finance.RateRule", str(item.pk))
        for item in RateRule.objects.filter(room=room, is_deleted=False)
    )
    identifiers.update(
        ("scheduling.Reservation", str(item.pk))
        for item in Reservation.objects.filter(room=room, is_deleted=False)
    )
    identifiers.update(
        ("vault.DocumentAsset", str(item.pk))
        for item in DocumentAsset.objects.filter(room=room, is_deleted=False)
    )
    return _timeline_for_model_ids(
        identifiers,
        extra_payload_matches={"room_id": str(room.pk), "room": str(room)},
    )


def get_timeline_for_owner(owner: OwnerProfile) -> list[TimelineItem]:
    identifiers = _base_related_identifiers(owner)
    identifiers.update(
        ("catalog.ConsultingRoom", str(item.pk))
        for item in owner.consulting_rooms.filter(is_deleted=False)
    )
    identifiers.update(
        ("vault.DocumentAsset", str(item.pk))
        for item in DocumentAsset.objects.filter(owner=owner, is_deleted=False)
    )
    return _timeline_for_model_ids(
        identifiers,
        extra_payload_matches={"owner_id": str(owner.pk), "owner": str(owner)},
    )


def get_timeline_for_tenant_doctor(
    tenant_doctor: TenantDoctorProfile,
) -> list[TimelineItem]:
    identifiers = _base_related_identifiers(tenant_doctor)
    identifiers.update(
        ("scheduling.Reservation", str(item.pk))
        for item in tenant_doctor.reservations.filter(is_deleted=False)
    )
    identifiers.update(
        ("vault.DocumentAsset", str(item.pk))
        for item in DocumentAsset.objects.filter(
            tenant_doctor=tenant_doctor,
            is_deleted=False,
        )
    )
    return _timeline_for_model_ids(
        identifiers,
        extra_payload_matches={
            "tenant_doctor_id": str(tenant_doctor.pk),
            "tenant_doctor": str(tenant_doctor),
        },
    )


def get_global_timeline(filters: dict[str, Any] | None = None) -> list[TimelineItem]:
    filters = filters or {}
    queryset = TraceEvent.objects.select_related("actor").order_by("-occurred_at")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    user = filters.get("user")

    if isinstance(date_from, date):
        queryset = queryset.filter(occurred_at__date__gte=date_from)
    if isinstance(date_to, date):
        queryset = queryset.filter(occurred_at__date__lte=date_to)
    if user:
        queryset = queryset.filter(actor=user)

    items = [_build_item(event) for event in queryset]
    level = filters.get("level")
    module = filters.get("module")
    action = filters.get("action")
    query = str(filters.get("q") or "").strip().lower()
    if level:
        items = [item for item in items if item.level == level]
    if module:
        items = [
            item
            for item in items
            if item.module.lower() == str(module).strip().lower()
            or _module_key(item.event.event_type) == str(module).strip().lower()
        ]
    if action:
        items = [
            item
            for item in items
            if item.action.lower() == str(action).strip().lower()
            or _action_key(item.event.event_type) == str(action).strip().lower()
        ]
    if query:
        items = [
            item
            for item in items
            if query
            in " ".join(
                (
                    item.title,
                    item.description,
                    item.actor_label,
                    item.module,
                    item.action,
                    item.related_object,
                    str(item.metadata),
                )
            ).lower()
        ]
    return items


def build_timeline_item(event: TraceEvent) -> TimelineItem:
    return _build_item(event)


def _timeline_for_model_ids(
    identifiers: set[tuple[str, str]],
    *,
    extra_payload_matches: dict[str, str] | None = None,
) -> list[TimelineItem]:
    events = TraceEvent.objects.select_related("actor").order_by("-occurred_at")
    matches = []
    extra_payload_matches = extra_payload_matches or {}
    for event in events:
        payload = event.payload or {}
        model = str(payload.get("model", ""))
        object_id = str(payload.get("id", ""))
        if (model, object_id) in identifiers:
            matches.append(event)
            continue
        if any(
            str(payload.get(key, "")) == value
            for key, value in extra_payload_matches.items()
        ):
            matches.append(event)
    return [_build_item(event) for event in matches]


def _base_related_identifiers(obj: Model) -> set[tuple[str, str]]:
    return {(obj._meta.label, str(obj.pk))}


def _build_item(event: TraceEvent) -> TimelineItem:
    payload = event.payload or {}
    level = _normalize_level(str(payload.get("level", "")), event.event_type)
    icon, badge_class = LEVEL_STYLES[level]
    module = _module_label(event.event_type)
    action = _action_label(event.event_type)
    title = str(payload.get("title") or f"{module}: {action.capitalize()}")
    description = _description(event, payload)
    actor_label = event.actor.email if event.actor else "Sistema"
    actor_role = getattr(event.actor, "role", "") if event.actor else ""
    return TimelineItem(
        event=event,
        level=level,
        module=module,
        action=action,
        title=title,
        description=description,
        actor_label=actor_label,
        actor_role=actor_role,
        icon=icon,
        badge_class=badge_class,
        related_object=str(
            payload.get("entity") or payload.get("reservation") or event.object_label
        ),
        metadata=payload,
        hash_value=str(
            payload.get("hash")
            or payload.get("sha256_hash")
            or payload.get("hash_evento")
            or ""
        ),
    )


def _normalize_level(raw_level: str, event_type: str) -> str:
    level = raw_level.lower()
    if "legal" in level:
        return LEVEL_LEGAL
    if "financiero" in level or "financial" in level:
        return LEVEL_FINANCIAL
    if "operativo" in level or "operational" in level:
        return LEVEL_OPERATIONAL

    module_key = _module_key(event_type)
    if module_key in {"payment", "settlement", "statement", "rate_rule"}:
        return LEVEL_FINANCIAL
    if module_key == "document":
        return LEVEL_LEGAL
    if module_key in {"clinic", "owner", "tenant_doctor", "consulting_room"}:
        return LEVEL_INFO
    return LEVEL_OPERATIONAL


def _module_key(event_type: str) -> str:
    return event_type.rsplit(".", maxsplit=1)[0]


def _action_key(event_type: str) -> str:
    parts = event_type.rsplit(".", maxsplit=1)
    return parts[-1] if parts else event_type


def _module_label(event_type: str) -> str:
    return MODULE_LABELS.get(_module_key(event_type), _module_key(event_type).title())


def _action_label(event_type: str) -> str:
    return ACTION_LABELS.get(_action_key(event_type), _action_key(event_type))


def _description(event: TraceEvent, payload: dict[str, Any]) -> str:
    description = payload.get("description")
    if description:
        return str(description)
    entity = payload.get("entity") or payload.get("reservation")
    if entity:
        return f"{event.event_type} sobre {entity}"
    return f"{event.event_type} sobre {event.object_label}"
