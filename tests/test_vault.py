from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.astrotrace.models import TraceEvent
from apps.vault.models import DocumentAsset, DocumentStatus, DocumentType
from apps.vault.services.document_service import (
    approve_document,
    cancel_document,
    get_documents_for_object,
    reject_document,
    upload_document,
)
from tests.test_reservations import (
    create_room,
    create_user,
    create_valid_reservation,
)


def uploaded_file(
    name: str = "documento.pdf",
    content: bytes = b"documento de prueba",
) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/pdf")


@pytest.mark.django_db
def test_valid_document_linked_to_owner() -> None:
    room = create_room("Consultorio Documento Owner")

    document = upload_document(
        title="INE propietario",
        document_type=DocumentType.INE,
        file=uploaded_file(),
        owner=room.owner,
    )

    assert document.owner == room.owner
    assert document.status == DocumentStatus.RECEIVED


@pytest.mark.django_db
def test_reject_document_without_linked_entity() -> None:
    with pytest.raises(ValidationError):
        upload_document(
            title="Sin entidad",
            document_type=DocumentType.OTHER,
            file=uploaded_file(),
        )


@pytest.mark.django_db
def test_sha256_hash_is_calculated() -> None:
    room = create_room("Consultorio Documento Hash")

    document = upload_document(
        title="Contrato",
        document_type=DocumentType.CONTRACT,
        file=uploaded_file(content=b"hash me"),
        owner=room.owner,
    )

    assert len(document.sha256_hash) == 64
    assert document.sha256_hash == (
        "eb201af5aaf0d60629d3d2a61e466cfc" "0fedb517add831ecac5235e1daa963d6"
    )


@pytest.mark.django_db
def test_initial_version_is_one() -> None:
    room = create_room("Consultorio Documento Version Uno")

    document = upload_document(
        title="RFC",
        document_type=DocumentType.RFC,
        file=uploaded_file(content=b"version uno"),
        owner=room.owner,
    )

    assert document.version == 1


@pytest.mark.django_db
def test_new_version_replaces_previous_document() -> None:
    room = create_room("Consultorio Documento Version Dos")
    first_document = upload_document(
        title="Contrato",
        document_type=DocumentType.CONTRACT,
        file=uploaded_file(content=b"version uno"),
        owner=room.owner,
    )

    second_document = upload_document(
        title="Contrato actualizado",
        document_type=DocumentType.CONTRACT,
        file=uploaded_file(content=b"version dos"),
        owner=room.owner,
    )
    first_document.refresh_from_db()

    assert second_document.version == 2
    assert first_document.status == DocumentStatus.REPLACED
    assert TraceEvent.objects.filter(event_type="document.versioned").exists()


@pytest.mark.django_db
def test_reject_duplicate_exact_file_for_same_entity_and_type() -> None:
    room = create_room("Consultorio Documento Duplicado")
    upload_document(
        title="Comprobante",
        document_type=DocumentType.ADDRESS_PROOF,
        file=uploaded_file(content=b"duplicado"),
        owner=room.owner,
    )

    with pytest.raises(ValidationError):
        upload_document(
            title="Comprobante duplicado",
            document_type=DocumentType.ADDRESS_PROOF,
            file=uploaded_file(content=b"duplicado"),
            owner=room.owner,
        )


@pytest.mark.django_db
def test_rejection_requires_reason() -> None:
    room = create_room("Consultorio Documento Rechazo")
    document = upload_document(
        title="Documento",
        document_type=DocumentType.OTHER,
        file=uploaded_file(),
        owner=room.owner,
    )

    with pytest.raises(ValidationError):
        reject_document(document=document, reason="")


@pytest.mark.django_db
def test_upload_document_creates_document_and_trace_event() -> None:
    room = create_room("Consultorio Documento Evento")

    document = upload_document(
        title="Reglamento",
        document_type=DocumentType.REGULATIONS,
        file=uploaded_file(content=b"reglamento"),
        room=room,
    )

    assert document.pk is not None
    assert TraceEvent.objects.filter(event_type="document.received").exists()


@pytest.mark.django_db
def test_approve_document_changes_status_and_reviewer() -> None:
    user = create_user("reviewer@example.com")
    room = create_room("Consultorio Documento Aprobar")
    document = upload_document(
        title="Cédula",
        document_type=DocumentType.PROFESSIONAL_LICENSE,
        file=uploaded_file(),
        owner=room.owner,
    )

    approve_document(document=document, actor=user)
    document.refresh_from_db()

    assert document.status == DocumentStatus.APPROVED
    assert document.reviewed_by == user
    assert document.reviewed_at is not None
    assert TraceEvent.objects.filter(event_type="document.approved").exists()


@pytest.mark.django_db
def test_reject_document_changes_status_and_reason() -> None:
    room = create_room("Consultorio Documento Rechazado")
    document = upload_document(
        title="RFC",
        document_type=DocumentType.RFC,
        file=uploaded_file(),
        owner=room.owner,
    )

    reject_document(document=document, reason="No legible")
    document.refresh_from_db()

    assert document.status == DocumentStatus.REJECTED
    assert document.rejection_reason == "No legible"
    assert TraceEvent.objects.filter(event_type="document.rejected").exists()


@pytest.mark.django_db
def test_cancel_document_changes_status() -> None:
    room = create_room("Consultorio Documento Cancelado")
    document = upload_document(
        title="Fotografía",
        document_type=DocumentType.PHOTO,
        file=uploaded_file(),
        room=room,
    )

    cancel_document(document=document)
    document.refresh_from_db()

    assert document.status == DocumentStatus.CANCELLED
    assert TraceEvent.objects.filter(event_type="document.cancelled").exists()


@pytest.mark.django_db
def test_get_documents_for_object_returns_related_documents() -> None:
    room = create_room("Consultorio Documento Selector")
    document = upload_document(
        title="Reglamento",
        document_type=DocumentType.REGULATIONS,
        file=uploaded_file(),
        room=room,
    )

    documents = get_documents_for_object(room)

    assert list(documents) == [document]


@pytest.mark.django_db
def test_document_list_requires_login(client: Any) -> None:
    response = client.get("/documentos/")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


@pytest.mark.django_db
def test_document_list_responds_200(client: Any) -> None:
    user = create_user("vault-list@example.com")
    client.force_login(user)

    response = client.get("/documentos/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_upload_document_from_ui(client: Any) -> None:
    user = create_user("vault-upload@example.com")
    room = create_room("Consultorio Documento UI")
    client.force_login(user)

    response = client.post(
        "/documentos/subir/",
        {
            "title": "Comprobante UI",
            "document_type": DocumentType.ADDRESS_PROOF,
            "file": uploaded_file(content=b"ui"),
            "owner": str(room.owner.pk) if room.owner is not None else "",
            "notes": "Subido desde UI",
        },
    )

    assert response.status_code == 302
    assert DocumentAsset.objects.filter(title="Comprobante UI").exists()


@pytest.mark.django_db
def test_document_detail_shows_abbreviated_hash(client: Any) -> None:
    user = create_user("vault-detail@example.com")
    room = create_room("Consultorio Documento Detalle")
    document = upload_document(
        title="Detalle",
        document_type=DocumentType.OTHER,
        file=uploaded_file(content=b"detalle"),
        owner=room.owner,
    )
    client.force_login(user)

    response = client.get(f"/documentos/{document.pk}/")

    assert response.status_code == 200
    assert document.sha256_hash[:12] in response.content.decode()


@pytest.mark.django_db
def test_reservation_detail_shows_related_documents(client: Any) -> None:
    user = create_user("vault-reservation@example.com")
    reservation = create_valid_reservation(room_name="Consultorio Documento Reserva")
    upload_document(
        title="Comprobante reservación",
        document_type=DocumentType.PAYMENT_RECEIPT,
        file=uploaded_file(content=b"reserva"),
        reservation=reservation,
    )
    client.force_login(user)

    response = client.get(f"/reservaciones/{reservation.pk}/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Documentos" in content
    assert "Comprobante reservación" not in content
    assert "Comprobante pago" in content
