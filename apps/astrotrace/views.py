"""Visual timeline views for AstroTrace events."""

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.views.generic import DetailView, TemplateView

from apps.astrotrace.forms import TimelineFilterForm
from apps.astrotrace.models import TraceEvent
from apps.astrotrace.services.timeline_service import (
    build_timeline_item,
    get_global_timeline,
    get_timeline_for_consulting_room,
    get_timeline_for_object,
    get_timeline_for_owner,
    get_timeline_for_reservation,
    get_timeline_for_tenant_doctor,
)
from apps.catalog.models import ConsultingRoom, OwnerProfile, TenantDoctorProfile
from apps.finance.models import Payment, Settlement
from apps.scheduling.models import Reservation
from apps.vault.models import DocumentAsset


class TimelineMixin(LoginRequiredMixin, TemplateView):
    template_name = "astrotrace/timeline.html"
    page_title = "Timeline"
    object_label = ""

    def get_items(self) -> list[Any]:
        return []

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        add_paginated_timeline(context, self.request, self.get_items())
        context["object_label"] = self.object_label
        return context


class GlobalTimelineView(LoginRequiredMixin, TemplateView):
    template_name = "astrotrace/timeline.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = TimelineFilterForm(self.request.GET or None)
        if form.is_bound:
            form.is_valid()
            filters = form.cleaned_data
        else:
            filters = {
                key: value
                for key, value in form.initial.items()
                if key in form.fields and value not in ("", None)
            }
        context["page_title"] = "Timeline"
        context["filter_form"] = form
        add_paginated_timeline(context, self.request, get_global_timeline(filters))
        context["object_label"] = ""
        return context


def add_paginated_timeline(
    context: dict[str, Any],
    request: Any,
    items: list[Any],
) -> None:
    paginator = Paginator(items, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    context["timeline_items"] = page_obj.object_list
    context["page_obj"] = page_obj
    context["paginator"] = paginator
    context["is_paginated"] = page_obj.has_other_pages()


class ReservationTimelineView(TimelineMixin):
    page_title = "Timeline de reservación"

    def get_reservation(self) -> Reservation:
        return Reservation.objects.select_related(
            "room",
            "tenant_doctor",
        ).get(pk=self.kwargs["pk"], is_deleted=False)

    def get_items(self) -> list[Any]:
        reservation = self.get_reservation()
        self.object_label = str(reservation)
        return get_timeline_for_reservation(reservation)


class ConsultingRoomTimelineView(TimelineMixin):
    page_title = "Timeline de consultorio"

    def get_room(self) -> ConsultingRoom:
        return ConsultingRoom.objects.select_related("clinic", "owner").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_items(self) -> list[Any]:
        room = self.get_room()
        self.object_label = str(room)
        return get_timeline_for_consulting_room(room)


class OwnerTimelineView(TimelineMixin):
    page_title = "Timeline de propietario"

    def get_owner(self) -> OwnerProfile:
        return OwnerProfile.objects.select_related("user").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_items(self) -> list[Any]:
        owner = self.get_owner()
        self.object_label = str(owner)
        return get_timeline_for_owner(owner)


class TenantDoctorTimelineView(TimelineMixin):
    page_title = "Timeline de médico arrendatario"

    def get_tenant_doctor(self) -> TenantDoctorProfile:
        return TenantDoctorProfile.objects.select_related("user").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_items(self) -> list[Any]:
        tenant_doctor = self.get_tenant_doctor()
        self.object_label = str(tenant_doctor)
        return get_timeline_for_tenant_doctor(tenant_doctor)


class PaymentTimelineView(TimelineMixin):
    page_title = "Timeline de pago"

    def get_payment(self) -> Payment:
        return Payment.objects.select_related("reservation", "statement").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_items(self) -> list[Any]:
        payment = self.get_payment()
        self.object_label = str(payment)
        return get_timeline_for_object(payment)


class SettlementTimelineView(TimelineMixin):
    page_title = "Timeline de liquidación"

    def get_settlement(self) -> Settlement:
        return Settlement.objects.select_related("reservation", "statement").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_items(self) -> list[Any]:
        settlement = self.get_settlement()
        self.object_label = str(settlement)
        return get_timeline_for_object(settlement)


class DocumentTimelineView(TimelineMixin):
    page_title = "Timeline de documento"

    def get_document(self) -> DocumentAsset:
        return DocumentAsset.objects.get(pk=self.kwargs["pk"], is_deleted=False)

    def get_items(self) -> list[Any]:
        document = self.get_document()
        self.object_label = str(document)
        return get_timeline_for_object(document)


class TraceEventDetailView(LoginRequiredMixin, DetailView):
    template_name = "astrotrace/event_detail.html"
    context_object_name = "event"

    def get_queryset(self) -> QuerySet[TraceEvent]:
        return TraceEvent.objects.filter(is_deleted=False).select_related("actor")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Detalle técnico de evento"
        context["timeline_item"] = build_timeline_item(context["event"])
        return context
