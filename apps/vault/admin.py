"""Admin configuration for document vault."""

from django.contrib import admin

from apps.vault.models import DocumentAsset


@admin.register(DocumentAsset)
class DocumentAssetAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "document_type",
        "version",
        "status",
        "sha256_hash",
        "created_at",
    )
    list_filter = ("document_type", "status", "created_at")
    search_fields = (
        "title",
        "original_name",
        "sha256_hash",
        "owner__display_name",
        "tenant_doctor__display_name",
        "room__name",
    )
    readonly_fields = (
        "sha256_hash",
        "size_bytes",
        "mime_type",
        "version",
        "created_at",
        "updated_at",
    )
