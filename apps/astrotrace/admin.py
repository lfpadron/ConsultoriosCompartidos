"""Admin configuration for traceability."""

from django.contrib import admin

from apps.astrotrace.models import Evidence, TraceEvent


@admin.register(TraceEvent)
class TraceEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "object_label", "actor", "occurred_at")
    list_filter = ("event_type",)
    search_fields = ("event_type", "object_label", "actor__email")


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("label", "event", "document", "is_active")
    search_fields = ("label", "event__event_type", "document__title")
