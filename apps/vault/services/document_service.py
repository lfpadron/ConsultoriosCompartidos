"""Document upload, versioning and review services."""

import hashlib
import mimetypes
from dataclasses import dataclass
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.db.models import Model, QuerySet
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.catalog.models import ConsultingRoom, OwnerProfile, TenantDoctorProfile
from apps.finance.models import Payment, Settlement
from apps.scheduling.models import Reservation
from apps.vault.models import DocumentAsset, DocumentStatus, DocumentType

ENTITY_FIELDS = (
    "owner",
    "tenant_doctor",
    "room",
    "reservation",
    "payment",
    "settlement",
)

LEGAL_DOCUMENT_TYPES = {
    DocumentType.CONTRACT,
    DocumentType.INE,
    DocumentType.RFC,
    DocumentType.PROFESSIONAL_LICENSE,
    DocumentType.ADDRESS_PROOF,
}


@dataclass(frozen=True)
class DocumentEntity:
    field_name: str
    label: str
    value: Model


@transaction.atomic
def upload_document(
    *,
    title: str,
    document_type: str,
    file: File,
    notes: str = "",
    actor: Model | None = None,
    **entities: Model | None,
) -> DocumentAsset:
    clean_entities = _normalize_entities(entities)
    sha256_hash = calculate_sha256(file)
    original_name = getattr(file, "name", "") or "documento"
    mime_type = getattr(file, "content_type", "") or _guess_mime_type(original_name)
    size_bytes = int(getattr(file, "size", 0) or 0)

    version = create_new_version(
        document_type=document_type,
        sha256_hash=sha256_hash,
        entities=clean_entities,
    )

    document = DocumentAsset(
        title=title,
        document_type=document_type,
        file=file,
        original_name=original_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        version=version,
        status=DocumentStatus.RECEIVED,
        notes=notes,
        **clean_entities,
    )
    if actor is not None:
        document.created_by = cast(Any, actor)
        document.updated_by = cast(Any, actor)
    document.save()

    record_event(
        event_type="document.received",
        object_label=str(document),
        actor=actor,
        payload=_document_payload(document, actor=actor),
    )
    if version > 1:
        record_event(
            event_type="document.versioned",
            object_label=str(document),
            actor=actor,
            payload=_document_payload(document, actor=actor),
        )
    return document


def calculate_sha256(file: File) -> str:
    digest = hashlib.sha256()
    for chunk in file.chunks():
        digest.update(chunk)
    if hasattr(file, "seek"):
        file.seek(0)
    return digest.hexdigest()


def create_new_version(
    *,
    document_type: str,
    sha256_hash: str,
    entities: dict[str, Model],
) -> int:
    queryset = _documents_for_entity_map(entities).filter(document_type=document_type)
    if queryset.filter(sha256_hash=sha256_hash).exists():
        raise ValidationError(
            {
                "file": (
                    "Ya existe un documento con el mismo archivo, entidad y tipo. "
                    "Sube un archivo distinto para crear una nueva versión."
                )
            }
        )

    latest_version = (
        queryset.order_by("-version").values_list("version", flat=True).first() or 0
    )
    queryset.exclude(status=DocumentStatus.CANCELLED).update(
        status=DocumentStatus.REPLACED,
        updated_at=timezone.now(),
    )
    return latest_version + 1


@transaction.atomic
def mark_document_in_review(
    *,
    document: DocumentAsset,
    actor: Model | None = None,
) -> DocumentAsset:
    document.status = DocumentStatus.IN_REVIEW
    _touch_review(document, actor=actor, update_review_fields=False)
    document.save()
    _record_document_event("document.in_review", document, actor=actor)
    return document


@transaction.atomic
def approve_document(
    *,
    document: DocumentAsset,
    actor: Model | None = None,
) -> DocumentAsset:
    document.status = DocumentStatus.APPROVED
    _touch_review(document, actor=actor, update_review_fields=True)
    document.rejection_reason = ""
    document.save()
    _record_document_event("document.approved", document, actor=actor)
    return document


@transaction.atomic
def reject_document(
    *,
    document: DocumentAsset,
    reason: str,
    actor: Model | None = None,
) -> DocumentAsset:
    if not reason.strip():
        raise ValidationError(
            {"rejection_reason": "El motivo de rechazo es obligatorio."}
        )
    document.status = DocumentStatus.REJECTED
    document.rejection_reason = reason
    _touch_review(document, actor=actor, update_review_fields=True)
    document.save()
    _record_document_event("document.rejected", document, actor=actor)
    return document


@transaction.atomic
def cancel_document(
    *,
    document: DocumentAsset,
    actor: Model | None = None,
) -> DocumentAsset:
    document.status = DocumentStatus.CANCELLED
    if actor is not None:
        document.updated_by = cast(Any, actor)
    document.save()
    _record_document_event("document.cancelled", document, actor=actor)
    return document


def get_documents_for_object(obj: Model) -> QuerySet[DocumentAsset]:
    field_name = get_document_field_for_object(obj)
    return DocumentAsset.objects.filter(
        is_deleted=False,
        **{field_name: obj},
    ).order_by("-created_at", "-version")


def get_document_field_for_object(obj: Model) -> str:
    return _field_for_object(obj)


def entity_from_document(document: DocumentAsset) -> DocumentEntity | None:
    for field_name in ENTITY_FIELDS:
        value = getattr(document, field_name)
        if value is not None:
            return DocumentEntity(
                field_name=field_name,
                label=_entity_label(field_name),
                value=value,
            )
    return None


def _normalize_entities(entities: dict[str, Model | None]) -> dict[str, Model]:
    clean_entities = {
        field_name: value
        for field_name, value in entities.items()
        if field_name in ENTITY_FIELDS and value is not None
    }
    if not clean_entities:
        raise ValidationError({"owner": "Vincula el documento al menos a una entidad."})
    return clean_entities


def _documents_for_entity_map(entities: dict[str, Model]) -> QuerySet[DocumentAsset]:
    filters: dict[str, Any] = {field_name: None for field_name in ENTITY_FIELDS}
    filters.update(entities)
    return DocumentAsset.objects.filter(is_deleted=False, **filters)


def _field_for_object(obj: Model) -> str:
    if isinstance(obj, OwnerProfile):
        return "owner"
    if isinstance(obj, TenantDoctorProfile):
        return "tenant_doctor"
    if isinstance(obj, ConsultingRoom):
        return "room"
    if isinstance(obj, Reservation):
        return "reservation"
    if isinstance(obj, Payment):
        return "payment"
    if isinstance(obj, Settlement):
        return "settlement"
    msg = f"Tipo de entidad no soportado para documentos: {type(obj).__name__}"
    raise ValueError(msg)


def _entity_label(field_name: str) -> str:
    return {
        "owner": "Propietario",
        "tenant_doctor": "Médico arrendatario",
        "room": "Consultorio",
        "reservation": "Reservación",
        "payment": "Pago",
        "settlement": "Liquidación",
    }[field_name]


def _guess_mime_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _touch_review(
    document: DocumentAsset,
    *,
    actor: Model | None,
    update_review_fields: bool,
) -> None:
    if actor is not None:
        document.updated_by = cast(Any, actor)
        if update_review_fields:
            document.reviewed_by = cast(Any, actor)
    if update_review_fields:
        document.reviewed_at = timezone.now()


def _record_document_event(
    event_type: str,
    document: DocumentAsset,
    *,
    actor: Model | None,
) -> None:
    record_event(
        event_type=event_type,
        object_label=str(document),
        actor=actor,
        payload=_document_payload(document, actor=actor),
    )


def _document_payload(
    document: DocumentAsset,
    *,
    actor: Model | None,
) -> dict[str, str]:
    entity = entity_from_document(document)
    return {
        "model": document._meta.label,
        "id": str(document.pk),
        "level": _trace_level(document.document_type),
        "document_type": document.document_type,
        "entity_type": entity.label if entity is not None else "",
        "entity_id": str(entity.value.pk) if entity is not None else "",
        "entity": str(entity.value) if entity is not None else "",
        "version": str(document.version),
        "sha256_hash": document.sha256_hash,
        "status": document.status,
        "actor_id": str(actor.pk) if actor is not None else "",
    }


def _trace_level(document_type: str) -> str:
    if document_type in LEGAL_DOCUMENT_TYPES:
        return "legal"
    if document_type == DocumentType.PAYMENT_RECEIPT:
        return "financiero"
    return "operativo"
