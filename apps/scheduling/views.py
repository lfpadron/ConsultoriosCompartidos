"""Scheduling views for availability rules, exceptions and weekly calendar."""

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any, cast
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Model, Q, QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import FormMixin

from apps.astrotrace.services import record_event
from apps.catalog.models import ConsultingRoom
from apps.core.form_utils import django_weekday_values
from apps.core.permissions import scope_queryset_for_user
from apps.core.templatetags.clinic_time import format_time_for_clinic
from apps.finance.models import RateRule, StatementStatus
from apps.finance.services.payment_service import get_payment_summary_for_reservation
from apps.finance.services.pricing_engine import (
    PricingConfigurationError,
    calculate_block_price,
)
from apps.finance.services.settlement_service import (
    get_settlement_summary_for_reservation,
)
from apps.integration.services.access_simulator import get_access_status_for_reservation
from apps.scheduling.forms import (
    AvailabilityExceptionForm,
    AvailabilityRuleForm,
    AvailabilityTariffBlockForm,
    AvailabilityTariffFilterForm,
    OperationalFilterForm,
    ReservationCancelForm,
    ReservationFilterForm,
    ReservationRequestForm,
    WeeklyCalendarFilterForm,
)
from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityRule,
    Reservation,
    Weekday,
    rule_weekdays,
)
from apps.scheduling.services import (
    BLOCK_STATUS_EXCEPTION,
    BLOCK_STATUS_FREE,
    BLOCK_STATUS_RESERVED_FUTURE,
    generate_availability_blocks,
    get_week_start,
)
from apps.scheduling.services.reservation_service import (
    cancel_reservation,
    confirm_reservation,
    create_reservation,
)
from apps.vault.services.document_service import (
    get_document_field_for_object,
    get_documents_for_object,
)


@dataclass(frozen=True)
class SchedulingResource:
    model: type[Any]
    form_class: type[ModelForm]
    singular_label: str
    plural_label: str
    list_url_name: str
    create_url_name: str
    detail_url_name: str
    update_url_name: str
    deactivate_url_name: str
    list_columns: tuple[tuple[str, str], ...]
    detail_fields: tuple[tuple[str, str], ...]
    create_event_type: str
    update_event_type: str
    deactivate_event_type: str
    select_related: tuple[str, ...] = ()


RULE = SchedulingResource(
    model=AvailabilityRule,
    form_class=AvailabilityRuleForm,
    singular_label="Regla de disponibilidad",
    plural_label="Reglas de disponibilidad",
    list_url_name="availability",
    create_url_name="availability_rule_create",
    detail_url_name="availability_rule_detail",
    update_url_name="availability_rule_update",
    deactivate_url_name="availability_rule_deactivate",
    list_columns=(
        ("Nombre", "name"),
        ("Consultorio", "room"),
        ("Días", "weekdays"),
        ("Horario", "time_range"),
        ("Estado", "is_active"),
    ),
    detail_fields=(
        ("Consultorio", "room"),
        ("Nombre", "name"),
        ("Días", "weekdays"),
        ("Hora inicio", "start_time"),
        ("Hora fin", "end_time"),
        ("Fecha inicio", "start_date"),
        ("Fecha fin", "end_date"),
        ("Notas", "notes"),
        ("Estado", "is_active"),
    ),
    create_event_type="availability_rule.created",
    update_event_type="availability_rule.updated",
    deactivate_event_type="availability_rule.deactivated",
    select_related=("room", "room__clinic", "room__owner"),
)

EXCEPTION = SchedulingResource(
    model=AvailabilityException,
    form_class=AvailabilityExceptionForm,
    singular_label="Excepción de disponibilidad",
    plural_label="Excepciones de disponibilidad",
    list_url_name="availability_exceptions",
    create_url_name="availability_exception_create",
    detail_url_name="availability_exception_detail",
    update_url_name="availability_exception_update",
    deactivate_url_name="availability_exception_deactivate",
    list_columns=(
        ("Consultorio", "room"),
        ("Fecha", "date"),
        ("Horario", "time_range"),
        ("Tipo", "get_exception_type_display"),
        ("Estado", "is_active"),
    ),
    detail_fields=(
        ("Consultorio", "room"),
        ("Fecha", "date"),
        ("Hora inicio", "start_time"),
        ("Hora fin", "end_time"),
        ("Tipo", "get_exception_type_display"),
        ("Motivo", "reason"),
        ("Estado", "is_active"),
    ),
    create_event_type="availability_exception.created",
    update_event_type="availability_exception.updated",
    deactivate_event_type="availability_exception.deactivated",
    select_related=("room", "room__clinic", "room__owner"),
)


def resolve_value(instance: Model, field_path: str) -> str:
    if field_path == "weekdays" and isinstance(instance, AvailabilityRule):
        return _format_weekdays(rule_weekdays(instance))
    if field_path == "time_range":
        start_time = getattr(instance, "start_time", None)
        end_time = getattr(instance, "end_time", None)
        if start_time and end_time:
            room = getattr(instance, "room", None)
            return (
                f"{format_time_for_clinic(start_time, room)} - "
                f"{format_time_for_clinic(end_time, room)}"
            )
        return "Día completo"

    value: Any = instance
    for attr in field_path.split("."):
        value = getattr(value, attr)
        if callable(value):
            value = value()

    if isinstance(value, bool):
        return "Activo" if value else "Inactivo"
    if isinstance(value, date):
        return f"{value:%Y-%m-%d}"
    if isinstance(value, time):
        return format_time_for_clinic(value, getattr(instance, "room", None))
    if value not in ("", None):
        return str(value)
    return "Sin datos"


def _cleaned_filter_data(form: Any) -> dict[str, Any]:
    if form.is_bound:
        form.is_valid()
        return form.cleaned_data
    return {}


def _effective_date_range(form: Any, cleaned_data: dict[str, Any]) -> tuple[date, date]:
    date_from = cleaned_data.get("date_from") or form.initial["date_from"]
    date_to = cleaned_data.get("date_to") or form.initial["date_to"]
    return cast(date, date_from), cast(date, date_to)


def _date_range(start_date: date, end_date: date) -> list[date]:
    return [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]


def _week_groups(start_date: date, end_date: date) -> list[list[date]]:
    week_start = get_week_start(start_date)
    final_week_start = get_week_start(end_date)
    weeks: list[list[date]] = []
    current_week = week_start
    while current_week <= final_week_start:
        weeks.append([current_week + timedelta(days=offset) for offset in range(7)])
        current_week += timedelta(days=7)
    return weeks


def _format_weekdays(weekdays: list[int]) -> str:
    labels = dict(Weekday.choices)
    return ", ".join(str(labels[day]) for day in weekdays)


def _initial_from_query_data(request: HttpRequest) -> dict[str, Any]:
    if request.method != "GET":
        return {}
    return {
        key: values if len(values) > 1 else values[0]
        for key, values in request.GET.lists()
    }


class SchedulingBaseMixin(LoginRequiredMixin):
    resource: SchedulingResource

    def get_queryset(self) -> QuerySet[Any]:
        queryset = self.resource.model._default_manager.filter(is_deleted=False)
        if self.resource.select_related:
            queryset = queryset.select_related(*self.resource.select_related)
        request = cast(Any, self).request
        return scope_queryset_for_user(queryset, request.user)

    def add_resource_context(self, context: dict[str, Any]) -> dict[str, Any]:
        context["resource"] = self.resource
        context["page_title"] = self.resource.plural_label
        return context


class SchedulingListView(SchedulingBaseMixin, ListView):
    template_name = "scheduling/list.html"
    context_object_name = "objects"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[Any]:
        queryset = super().get_queryset()
        self.filter_form = OperationalFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(self.filter_form)
        search_query = (cleaned_data.get("q") or "").strip()
        clinic = cleaned_data.get("clinic")
        owner = cleaned_data.get("owner")
        room = cleaned_data.get("room")
        weekdays = cleaned_data.get("weekdays") or []
        date_from, date_to = _effective_date_range(self.filter_form, cleaned_data)

        if search_query:
            queryset = self._filter_search(queryset, search_query)
        if clinic:
            queryset = queryset.filter(room__clinic=clinic)
        if owner:
            queryset = queryset.filter(room__owner=owner)
        if room:
            queryset = queryset.filter(room=room)

        if self.resource.model is AvailabilityRule:
            queryset = queryset.filter(start_date__lte=date_to).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=date_from)
            )
            if weekdays:
                rule_ids = [
                    rule.pk
                    for rule in queryset
                    if set(rule_weekdays(rule)).intersection(set(weekdays))
                ]
                queryset = queryset.filter(pk__in=rule_ids)
        elif self.resource.model is AvailabilityException:
            queryset = queryset.filter(date__gte=date_from, date__lte=date_to)
            if weekdays:
                queryset = queryset.filter(
                    date__week_day__in=django_weekday_values(weekdays)
                )

        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        objects = context["objects"]
        context["filter_form"] = self.filter_form
        context["rows"] = [
            {
                "object": item,
                "cells": [
                    resolve_value(item, field_path)
                    for _, field_path in self.resource.list_columns
                ],
            }
            for item in objects
        ]
        return context

    def _filter_search(
        self,
        queryset: QuerySet[Any],
        search_query: str,
    ) -> QuerySet[Any]:
        if self.resource.model is AvailabilityRule:
            return queryset.filter(
                Q(name__icontains=search_query)
                | Q(room__name__icontains=search_query)
                | Q(room__clinic__name__icontains=search_query)
                | Q(room__owner__display_name__icontains=search_query)
            )
        return queryset.filter(
            Q(reason__icontains=search_query)
            | Q(room__name__icontains=search_query)
            | Q(room__clinic__name__icontains=search_query)
            | Q(room__owner__display_name__icontains=search_query)
        )


class SchedulingDetailView(SchedulingBaseMixin, DetailView):
    template_name = "scheduling/detail.html"
    context_object_name = "object"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        instance = context["object"]
        context["field_rows"] = [
            (label, resolve_value(instance, field_path))
            for label, field_path in self.resource.detail_fields
        ]
        return context


class SchedulingFormView(SchedulingBaseMixin, FormMixin, TemplateView):
    template_name = "scheduling/form.html"
    object: Model | None = None
    is_create = False

    def get_form_class(self) -> type[ModelForm]:
        return self.resource.form_class

    def get_object(self) -> Model | None:
        if self.object is not None:
            return self.object
        pk = self.kwargs.get("pk")
        if pk is None:
            return None
        self.object = self.get_queryset().get(pk=pk)
        return self.object

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["filter_data"] = self.request.GET
        kwargs["initial"] = {
            **kwargs.get("initial", {}),
            **_initial_from_query_data(self.request),
        }
        instance = self.get_object()
        if instance is not None:
            kwargs["instance"] = instance
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        action = "Alta" if self.is_create else "Edición"
        context["page_title"] = f"{action} de {self.resource.singular_label.lower()}"
        context["object"] = self.get_object()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: ModelForm) -> HttpResponse:
        instance = form.save(commit=False)
        user = self.request.user
        if self.is_create and hasattr(instance, "created_by"):
            instance.created_by = user
        if hasattr(instance, "updated_by"):
            instance.updated_by = user
        instance.save()
        self.object = instance
        self._record_trace(instance)
        messages.success(self.request, f"{self.resource.singular_label} guardada.")
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        instance = self.get_object()
        if instance is None:
            return reverse(self.resource.list_url_name)
        return reverse(self.resource.detail_url_name, kwargs={"pk": instance.pk})

    def _record_trace(self, instance: Model) -> None:
        record_event(
            event_type=(
                self.resource.create_event_type
                if self.is_create
                else self.resource.update_event_type
            ),
            object_label=str(instance),
            actor=cast(Model, self.request.user),
            payload=_trace_payload(instance),
        )


class SchedulingDeactivateView(SchedulingBaseMixin, TemplateView):
    template_name = "scheduling/confirm_deactivate.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        context["object"] = self.get_queryset().get(pk=self.kwargs["pk"])
        context["page_title"] = f"Desactivar {self.resource.singular_label.lower()}"
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        instance = self.get_queryset().get(pk=self.kwargs["pk"])
        instance.is_active = False
        instance.updated_by = request.user
        instance.save(update_fields=["is_active", "updated_by", "updated_at"])
        record_event(
            event_type=self.resource.deactivate_event_type,
            object_label=str(instance),
            actor=cast(Model, request.user),
            payload={**_trace_payload(instance), "action": "deactivate"},
        )
        messages.success(request, f"{self.resource.singular_label} desactivada.")
        return redirect(self.resource.list_url_name)


def _trace_payload(instance: Model) -> dict[str, str]:
    payload = {"model": instance._meta.label, "id": str(instance.pk)}
    if isinstance(instance, AvailabilityException):
        level = (
            "legal_operativo"
            if instance.exception_type in {"maintenance", "unavailable"}
            else "operativo"
        )
        payload["level"] = level
    else:
        payload["level"] = "operativo"
    return payload


class AvailabilityRuleListView(SchedulingListView):
    resource = RULE


class AvailabilityRuleCreateView(SchedulingFormView):
    resource = RULE
    is_create = True


class AvailabilityRuleUpdateView(SchedulingFormView):
    resource = RULE


class AvailabilityRuleDetailView(SchedulingDetailView):
    resource = RULE


class AvailabilityRuleDeactivateView(SchedulingDeactivateView):
    resource = RULE


class AvailabilityTariffListView(LoginRequiredMixin, ListView):
    template_name = "scheduling/availability_tariff_list.html"
    context_object_name = "rooms"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[ConsultingRoom]:
        queryset = (
            ConsultingRoom.objects.filter(is_deleted=False)
            .select_related("clinic", "owner", "owner__user")
            .annotate(
                active_availability_count=Count(
                    "availability_rules",
                    filter=Q(
                        availability_rules__is_active=True,
                        availability_rules__is_deleted=False,
                    ),
                    distinct=True,
                ),
                active_rate_count=Count(
                    "rate_rules",
                    filter=Q(
                        rate_rules__is_active=True,
                        rate_rules__is_deleted=False,
                    ),
                    distinct=True,
                ),
            )
        )
        self.filter_form = AvailabilityTariffFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(self.filter_form)
        search_query = (cleaned_data.get("q") or "").strip()
        clinic = cleaned_data.get("clinic")
        campus = cleaned_data.get("campus")
        owner = cleaned_data.get("owner")
        is_active = cleaned_data.get("is_active")

        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query)
                | Q(number__icontains=search_query)
                | Q(campus__icontains=search_query)
                | Q(tower__icontains=search_query)
                | Q(clinic__name__icontains=search_query)
                | Q(owner__display_name__icontains=search_query)
                | Q(owner__user__email__icontains=search_query)
            )
        if clinic:
            queryset = queryset.filter(clinic=clinic)
        if campus:
            queryset = queryset.filter(campus=campus)
        if owner:
            queryset = queryset.filter(owner=owner)
        if is_active == "true":
            queryset = queryset.filter(is_active=True)
        elif is_active == "false":
            queryset = queryset.filter(is_active=False)

        return scope_queryset_for_user(
            queryset.order_by("clinic__name", "campus", "number", "name"),
            self.request.user,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rooms = list(context["rooms"])
        context.update(
            {
                "page_title": "Disponibilidad y tarifas",
                "filter_form": self.filter_form,
                "rows": [self._room_row(room) for room in rooms],
                "new_room_url": (
                    reverse("availability_room_detail", kwargs={"pk": rooms[0].pk})
                    if rooms
                    else ""
                ),
            }
        )
        return context

    @staticmethod
    def _room_row(room: ConsultingRoom) -> dict[str, Any]:
        return {
            "room": room,
            "edit_url": reverse("availability_room_detail", kwargs={"pk": room.pk}),
            "clinic": room.clinic,
            "campus": room.campus or "Sin campus",
            "tower": room.tower or "Sin torre",
            "number": room.number or "Sin número",
            "owner": room.owner,
            "active_availability_count": getattr(
                room,
                "active_availability_count",
                0,
            ),
            "active_rate_count": getattr(room, "active_rate_count", 0),
            "is_active": "Activo" if room.is_active else "Inactivo",
        }


class AvailabilityTariffDetailView(LoginRequiredMixin, TemplateView):
    template_name = "scheduling/availability_tariff_detail.html"

    def get_room(self) -> ConsultingRoom:
        queryset = ConsultingRoom.objects.filter(is_deleted=False).select_related(
            "clinic",
            "owner",
            "owner__user",
        )
        queryset = scope_queryset_for_user(queryset, self.request.user)
        return queryset.get(pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        room = self.get_room()
        form = kwargs.get("form") or AvailabilityTariffBlockForm(
            initial=self._initial_block_data(room)
        )
        context.update(
            {
                "page_title": "Detalle de disponibilidad",
                "room": room,
                "blocks": availability_tariff_blocks(room),
                "form": form,
                "is_editing": bool(
                    self.request.GET.get("availability_rule")
                    or self.request.GET.get("rate_rule")
                ),
            }
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        room = self.get_room()
        form = AvailabilityTariffBlockForm(request.POST)
        if form.is_valid():
            try:
                self._save_block(room, form)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    "Bloque de disponibilidad y tarifa guardado.",
                )
                return redirect("availability_room_detail", pk=room.pk)
        return self.render_to_response(self.get_context_data(form=form))

    def _initial_block_data(self, room: ConsultingRoom) -> dict[str, Any]:
        availability_rule = self._get_availability_rule(room)
        rate_rule = self._get_rate_rule(room)
        weekday = self._selected_weekday(availability_rule, rate_rule)
        source = availability_rule or rate_rule
        if source is None:
            return {"is_active": True}

        initial = {
            "availability_rule_id": getattr(availability_rule, "pk", None),
            "rate_rule_id": getattr(rate_rule, "pk", None),
            "weekday": weekday,
            "start_time": source.start_time,
            "end_time": source.end_time,
            "start_date": source.start_date,
            "end_date": source.end_date,
            "is_active": source.is_active,
        }
        if rate_rule is not None:
            initial.update(
                {
                    "price_type": rate_rule.price_type,
                    "amount": rate_rule.amount,
                }
            )
        return initial

    def _get_availability_rule(self, room: ConsultingRoom) -> AvailabilityRule | None:
        rule_id = self.request.GET.get("availability_rule")
        if not rule_id:
            return None
        return AvailabilityRule.objects.filter(
            pk=rule_id,
            room=room,
            is_deleted=False,
        ).first()

    def _get_rate_rule(self, room: ConsultingRoom) -> RateRule | None:
        rule_id = self.request.GET.get("rate_rule")
        if not rule_id:
            return None
        return RateRule.objects.filter(pk=rule_id, room=room, is_deleted=False).first()

    def _selected_weekday(
        self,
        availability_rule: AvailabilityRule | None,
        rate_rule: RateRule | None,
    ) -> int | None:
        weekday_text = self.request.GET.get("weekday")
        if isinstance(weekday_text, str) and weekday_text:
            return int(weekday_text)
        if availability_rule is not None:
            return rule_weekdays(availability_rule)[0]
        if rate_rule is not None and rate_rule.weekdays:
            return int(rate_rule.weekdays[0])
        return None

    def _save_block(
        self,
        room: ConsultingRoom,
        form: AvailabilityTariffBlockForm,
    ) -> None:
        cleaned_data = form.cleaned_data
        weekday = cleaned_data["weekday"]
        label = _block_name(
            room,
            weekday,
            cleaned_data["start_time"],
            cleaned_data["end_time"],
        )
        actor = cast(Any, self.request.user)
        with transaction.atomic():
            availability_rule, availability_created = self._save_availability_rule(
                room,
                label,
                cleaned_data,
                actor,
            )
            rate_rule, rate_created = self._save_rate_rule(
                room,
                label,
                cleaned_data,
                actor,
            )

        record_event(
            event_type=(
                "availability_rule.created"
                if availability_created
                else "availability_rule.updated"
            ),
            object_label=str(availability_rule),
            actor=cast(Model, self.request.user),
            payload=_trace_payload(availability_rule),
        )
        record_event(
            event_type="rate_rule.created" if rate_created else "rate_rule.updated",
            object_label=str(rate_rule),
            actor=cast(Model, self.request.user),
            payload=_rate_trace_payload(rate_rule),
        )

    def _save_availability_rule(
        self,
        room: ConsultingRoom,
        label: str,
        cleaned_data: dict[str, Any],
        actor: Any,
    ) -> tuple[AvailabilityRule, bool]:
        rule_id = cleaned_data.get("availability_rule_id")
        rule = None
        if rule_id:
            rule = AvailabilityRule.objects.filter(
                pk=rule_id,
                room=room,
                is_deleted=False,
            ).first()
        created = rule is None
        if rule is None:
            rule = AvailabilityRule(room=room, created_by=actor)

        rule.name = f"Disponibilidad {label}"
        rule.weekday = cleaned_data["weekday"]
        rule.weekdays = [cleaned_data["weekday"]]
        rule.start_time = cleaned_data["start_time"]
        rule.end_time = cleaned_data["end_time"]
        rule.start_date = cleaned_data["start_date"]
        rule.end_date = cleaned_data["end_date"]
        rule.is_active = cleaned_data["is_active"]
        rule.updated_by = actor
        rule.save()
        return rule, created

    def _save_rate_rule(
        self,
        room: ConsultingRoom,
        label: str,
        cleaned_data: dict[str, Any],
        actor: Any,
    ) -> tuple[RateRule, bool]:
        rule_id = cleaned_data.get("rate_rule_id")
        rule = None
        if rule_id:
            rule = RateRule.objects.filter(
                pk=rule_id, room=room, is_deleted=False
            ).first()
        created = rule is None
        if rule is None:
            rule = RateRule(
                room=room,
                created_by=actor,
                currency="MXN",
                priority=1,
            )

        rule.name = f"Tarifa {label}"
        rule.weekdays = [cleaned_data["weekday"]]
        rule.start_time = cleaned_data["start_time"]
        rule.end_time = cleaned_data["end_time"]
        rule.start_date = cleaned_data["start_date"]
        rule.end_date = cleaned_data["end_date"]
        rule.price_type = cleaned_data["price_type"]
        rule.amount = cleaned_data["amount"]
        rule.is_active = cleaned_data["is_active"]
        rule.updated_by = actor
        rule.save()
        return rule, created


def availability_tariff_blocks(room: ConsultingRoom) -> list[dict[str, Any]]:
    availability_rules = list(
        AvailabilityRule.objects.filter(room=room, is_deleted=False).order_by(
            "weekday",
            "start_time",
            "end_time",
        )
    )
    rate_rules = list(
        RateRule.objects.filter(room=room, is_deleted=False).order_by(
            "start_time",
            "end_time",
        )
    )
    used_rate_keys: set[tuple[str, int]] = set()
    blocks: list[dict[str, Any]] = []

    for availability_rule in availability_rules:
        for weekday in rule_weekdays(availability_rule):
            rate_rule = _matching_rate_rule(availability_rule, weekday, rate_rules)
            if rate_rule is not None:
                used_rate_keys.add((str(rate_rule.pk), weekday))
            blocks.append(
                _availability_tariff_block(room, availability_rule, rate_rule, weekday)
            )

    for rate_rule in rate_rules:
        for weekday in rate_rule.weekdays:
            rate_key = (str(rate_rule.pk), int(weekday))
            if rate_key in used_rate_keys:
                continue
            blocks.append(
                _availability_tariff_block(room, None, rate_rule, int(weekday))
            )

    return sorted(
        blocks,
        key=lambda block: (
            block["weekday"],
            block["start_time"] or time.min,
            block["end_time"] or time.min,
        ),
    )


def _matching_rate_rule(
    availability_rule: AvailabilityRule,
    weekday: int,
    rate_rules: list[RateRule],
) -> RateRule | None:
    for rate_rule in rate_rules:
        if int(weekday) not in {int(day) for day in rate_rule.weekdays}:
            continue
        if (
            availability_rule.start_time == rate_rule.start_time
            and availability_rule.end_time == rate_rule.end_time
            and availability_rule.start_date == rate_rule.start_date
            and availability_rule.end_date == rate_rule.end_date
        ):
            return rate_rule
    return None


def _availability_tariff_block(
    room: ConsultingRoom,
    availability_rule: AvailabilityRule | None,
    rate_rule: RateRule | None,
    weekday: int,
) -> dict[str, Any]:
    source = availability_rule or rate_rule
    weekday_labels = dict(Weekday.choices)
    edit_params: dict[str, str] = {"weekday": str(weekday)}
    if availability_rule is not None:
        edit_params["availability_rule"] = str(availability_rule.pk)
    if rate_rule is not None:
        edit_params["rate_rule"] = str(rate_rule.pk)
    edit_url = (
        f"{reverse('availability_room_detail', kwargs={'pk': room.pk})}"
        f"?{urlencode(edit_params)}"
    )
    return {
        "availability_rule": availability_rule,
        "rate_rule": rate_rule,
        "weekday": weekday,
        "weekday_label": weekday_labels[weekday],
        "start_time": getattr(source, "start_time", None),
        "end_time": getattr(source, "end_time", None),
        "time_range": (
            f"{format_time_for_clinic(source.start_time, room)} - "
            f"{format_time_for_clinic(source.end_time, room)}"
            if source is not None
            else "Sin horario"
        ),
        "price_type": (
            rate_rule.get_price_type_display()
            if rate_rule is not None
            else "Sin tarifa"
        ),
        "amount": (
            f"{rate_rule.amount} {rate_rule.currency}"
            if rate_rule is not None
            else "Sin tarifa"
        ),
        "start_date": getattr(source, "start_date", None),
        "end_date": getattr(source, "end_date", None),
        "status": "Activo" if getattr(source, "is_active", False) else "Inactivo",
        "edit_url": edit_url,
    }


def _block_name(
    room: ConsultingRoom,
    weekday: int,
    start_time: time,
    end_time: time,
) -> str:
    weekday_label = dict(Weekday.choices)[weekday]
    return f"{room.name} {weekday_label} {start_time:%H:%M}-{end_time:%H:%M}"


def _rate_trace_payload(instance: RateRule) -> dict[str, str]:
    description = (
        f"{instance.room} {instance.start_time:%H:%M}-{instance.end_time:%H:%M} "
        f"{instance.amount} {instance.currency} prioridad {instance.priority}"
    )
    return {
        "model": instance._meta.label,
        "id": str(instance.pk),
        "level": "financiero",
        "description": description,
        "room": str(instance.room),
        "amount": str(instance.amount),
        "currency": instance.currency,
        "priority": str(instance.priority),
    }


class AvailabilityExceptionListView(SchedulingListView):
    resource = EXCEPTION


class AvailabilityExceptionCreateView(SchedulingFormView):
    resource = EXCEPTION
    is_create = True


class AvailabilityExceptionUpdateView(SchedulingFormView):
    resource = EXCEPTION


class AvailabilityExceptionDetailView(SchedulingDetailView):
    resource = EXCEPTION


class AvailabilityExceptionDeactivateView(SchedulingDeactivateView):
    resource = EXCEPTION


class ReservationListView(LoginRequiredMixin, ListView):
    template_name = "scheduling/reservation_list.html"
    context_object_name = "reservations"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[Reservation]:
        queryset = Reservation.objects.filter(is_deleted=False).select_related(
            "room",
            "room__clinic",
            "room__owner",
            "tenant_doctor",
            "tenant_doctor__user",
        )
        self.filter_form = ReservationFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(self.filter_form)
        self.search_query = (cleaned_data.get("q") or "").strip()
        clinic = cleaned_data.get("clinic")
        owner = cleaned_data.get("owner")
        room = cleaned_data.get("room")
        tenant_doctor = cleaned_data.get("tenant_doctor")
        weekdays = cleaned_data.get("weekdays") or []
        date_from, date_to = _effective_date_range(self.filter_form, cleaned_data)

        if self.search_query:
            queryset = queryset.filter(
                Q(room__name__icontains=self.search_query)
                | Q(room__clinic__name__icontains=self.search_query)
                | Q(room__owner__display_name__icontains=self.search_query)
                | Q(tenant_doctor__display_name__icontains=self.search_query)
                | Q(tenant_doctor__user__email__icontains=self.search_query)
                | Q(notes__icontains=self.search_query)
                | Q(status__icontains=self.search_query)
            )
        if clinic:
            queryset = queryset.filter(room__clinic=clinic)
        if owner:
            queryset = queryset.filter(room__owner=owner)
        if room:
            queryset = queryset.filter(room=room)
        if tenant_doctor:
            queryset = queryset.filter(tenant_doctor=tenant_doctor)
        queryset = queryset.filter(date__gte=date_from, date__lte=date_to)
        if weekdays:
            queryset = queryset.filter(
                date__week_day__in=django_weekday_values(weekdays)
            )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Reservaciones"
        context["search_query"] = self.search_query
        context["filter_form"] = self.filter_form
        return context


class ReservationDetailView(LoginRequiredMixin, DetailView):
    template_name = "scheduling/reservation_detail.html"
    context_object_name = "reservation"

    def get_queryset(self) -> QuerySet[Reservation]:
        queryset = Reservation.objects.filter(is_deleted=False).select_related(
            "room",
            "room__clinic",
            "tenant_doctor",
            "tenant_doctor__user",
        )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        reservation = context["reservation"]
        context["page_title"] = "Detalle de reservación"
        context["statement"] = (
            reservation.statements.filter(status=StatementStatus.CURRENT)
            .select_related("applied_rate_rule")
            .first()
        )
        context["payment_summary"] = get_payment_summary_for_reservation(reservation)
        context["settlement_summary"] = get_settlement_summary_for_reservation(
            reservation
        )
        context["access_status"] = get_access_status_for_reservation(reservation)
        context["related_documents"] = get_documents_for_object(reservation)
        context["document_upload_field"] = get_document_field_for_object(reservation)
        return context


class ReservationRequestView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "scheduling/reservation_form.html"
    form_class = ReservationRequestForm

    def get_initial(self) -> dict[str, Any]:
        initial = super().get_initial()
        for field in ("room", "date", "start_time", "end_time"):
            value = self.request.GET.get(field)
            if value:
                initial[field] = value
        return initial

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Solicitar reservación"
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: ReservationRequestForm) -> HttpResponse:
        try:
            reservation = create_reservation(
                room=form.cleaned_data["room"],
                tenant_doctor=form.cleaned_data["tenant_doctor"],
                reservation_date=form.cleaned_data["date"],
                start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"],
                notes=form.cleaned_data["notes"],
                actor=cast(Model, self.request.user),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(self.request, "Reservación solicitada.")
        return redirect("reservation_detail", pk=reservation.pk)


class ReservationCancelView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "scheduling/reservation_cancel.html"
    form_class = ReservationCancelForm

    def get_reservation(self) -> Reservation:
        return Reservation.objects.get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Cancelar reservación"
        context["reservation"] = self.get_reservation()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            reservation = cancel_reservation(
                reservation=self.get_reservation(),
                reason=form.cleaned_data["reason"],
                actor=cast(Model, request.user),
            )
            messages.success(request, "Reservación cancelada.")
            return redirect("reservation_detail", pk=reservation.pk)
        return self.form_invalid(form)


class ReservationConfirmView(LoginRequiredMixin, TemplateView):
    template_name = "scheduling/reservation_confirm.html"

    def get_reservation(self) -> Reservation:
        return Reservation.objects.get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Confirmar reservación"
        context["reservation"] = self.get_reservation()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        reservation = confirm_reservation(
            reservation=self.get_reservation(),
            actor=cast(Model, request.user),
        )
        messages.success(request, "Reservación confirmada.")
        return redirect("reservation_detail", pk=reservation.pk)


class WeeklyCalendarView(LoginRequiredMixin, TemplateView):
    template_name = "scheduling/calendar_week.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = WeeklyCalendarFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(form)
        date_from, date_to = self._calendar_date_range(form, cleaned_data)
        week_dates = _date_range(date_from, date_to)
        selected_room = cleaned_data.get("room")
        selected_clinic = cleaned_data.get("clinic")
        selected_owner = cleaned_data.get("owner")
        selected_tenant_doctor = cleaned_data.get("tenant_doctor")
        selected_weekdays = set(cleaned_data.get("weekdays") or [])
        weeks = _week_groups(date_from, date_to)
        rooms = self._get_rooms(selected_room, selected_clinic, selected_owner)
        today = timezone.localdate()

        context.update(
            {
                "page_title": "Calendario",
                "form": form,
                "week_start": date_from,
                "week_end": date_to,
                "week_dates": week_dates,
                "calendar_rows": [
                    {
                        "room": room,
                        "weeks": self._calendar_weeks_for_room(
                            room,
                            weeks,
                            date_from,
                            date_to,
                            today,
                            selected_weekdays,
                            selected_tenant_doctor,
                        ),
                    }
                    for room in rooms
                ],
                "previous_week_url": self._range_url(
                    date_from - timedelta(days=7),
                    date_to - timedelta(days=7),
                ),
                "next_week_url": self._range_url(
                    date_from + timedelta(days=7),
                    date_to + timedelta(days=7),
                ),
            }
        )
        return context

    @staticmethod
    def _priced_blocks(
        blocks: list[Any], *, is_past: bool = False
    ) -> list[dict[str, Any]]:
        priced_blocks: list[dict[str, Any]] = []
        for block in blocks:
            pricing = None
            pricing_error = ""
            if block.status == BLOCK_STATUS_FREE:
                try:
                    pricing = calculate_block_price(
                        consulting_room=block.room,
                        date=block.date,
                        start_time=block.start_time,
                        end_time=block.end_time,
                    )
                except PricingConfigurationError as exc:
                    pricing_error = str(exc)

            priced_blocks.append(
                {
                    "block": block,
                    "pricing": pricing,
                    "pricing_error": pricing_error,
                    "can_request": block.status == BLOCK_STATUS_FREE and not is_past,
                }
            )
        return priced_blocks

    @staticmethod
    def _calendar_date_range(
        form: WeeklyCalendarFilterForm,
        cleaned_data: dict[str, Any],
    ) -> tuple[date, date]:
        selected_week = cleaned_data.get("week")
        if (
            selected_week
            and not form.data.get("date_from")
            and not form.data.get("date_to")
        ):
            week_start = get_week_start(selected_week)
            return week_start, week_start + timedelta(days=6)
        date_from, date_to = _effective_date_range(form, cleaned_data)
        week_start = get_week_start(date_from)
        week_end = get_week_start(date_to) + timedelta(days=6)
        return week_start, week_end

    def _calendar_weeks_for_room(
        self,
        room: ConsultingRoom,
        weeks: list[list[date]],
        date_from: date,
        date_to: date,
        today: date,
        selected_weekdays: set[int],
        selected_tenant_doctor: Any,
    ) -> list[dict[str, Any]]:
        grouped_blocks: dict[date, list[Any]] = {
            day: [] for day in _date_range(date_from, date_to)
        }
        for block in generate_availability_blocks(room, date_from, date_to):
            grouped_blocks[block.date].append(block)

        return [
            {
                "week_start": week[0],
                "week_end": week[-1],
                "days": [
                    self._calendar_day(
                        day,
                        grouped_blocks.get(day, []),
                        today,
                        selected_weekdays,
                        selected_tenant_doctor,
                    )
                    for day in week
                ],
            }
            for week in weeks
        ]

    def _calendar_day(
        self,
        day: date,
        blocks: list[Any],
        today: date,
        selected_weekdays: set[int],
        selected_tenant_doctor: Any,
    ) -> dict[str, Any]:
        is_filtered = bool(selected_weekdays) and day.weekday() not in selected_weekdays
        visible_blocks = (
            []
            if is_filtered
            else self._filter_blocks_by_tenant_doctor(blocks, selected_tenant_doctor)
        )
        return {
            "date": day,
            "is_past": day < today,
            "is_filtered": is_filtered,
            "blocks": self._priced_blocks(
                visible_blocks,
                is_past=day < today,
            ),
        }

    @staticmethod
    def _filter_blocks_by_tenant_doctor(
        blocks: list[Any],
        selected_tenant_doctor: Any,
    ) -> list[Any]:
        if not selected_tenant_doctor:
            return blocks
        return [
            block
            for block in blocks
            if block.status != BLOCK_STATUS_RESERVED_FUTURE
            or (
                block.reservation
                and block.reservation.tenant_doctor_id == selected_tenant_doctor.pk
            )
        ]

    @staticmethod
    def _get_rooms(
        selected_room: ConsultingRoom | None,
        selected_clinic: Any,
        selected_owner: Any,
    ) -> QuerySet[ConsultingRoom] | list[ConsultingRoom]:
        if selected_room:
            return [selected_room]

        queryset = ConsultingRoom.objects.filter(
            is_active=True,
            is_deleted=False,
        ).select_related("clinic", "owner")
        if selected_clinic:
            queryset = queryset.filter(clinic=selected_clinic)
        if selected_owner:
            queryset = queryset.filter(owner=selected_owner)
        return queryset.order_by("clinic__name", "name")

    def _range_url(self, date_from: date, date_to: date) -> str:
        params = self.request.GET.copy()
        params.pop("week", None)
        params["date_from"] = date_from.isoformat()
        params["date_to"] = date_to.isoformat()
        return f"{reverse('calendar_week')}?{urlencode(params, doseq=True)}"


class QuickCalendarView(LoginRequiredMixin, TemplateView):
    template_name = "scheduling/calendar_quick.html"
    detail_paginate_by = 25

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = WeeklyCalendarFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(form)
        date_from, date_to = WeeklyCalendarView._calendar_date_range(
            form,
            cleaned_data,
        )
        selected_date = self._selected_date(date_from, date_to)
        rooms = list(
            WeeklyCalendarView._get_rooms(
                cleaned_data.get("room"),
                cleaned_data.get("clinic"),
                cleaned_data.get("owner"),
            )
        )
        selected_weekdays = set(cleaned_data.get("weekdays") or [])
        selected_tenant_doctor = cleaned_data.get("tenant_doctor")
        today = timezone.localdate()
        blocks_by_room = self._blocks_by_room(rooms, date_from, date_to)
        detail_rows = self._detail_rows(
            rooms,
            blocks_by_room,
            selected_date,
            today,
            selected_tenant_doctor,
        )
        page_obj = Paginator(detail_rows, self.detail_paginate_by).get_page(
            self.request.GET.get("page")
        )

        context.update(
            {
                "page_title": "Vista rápida",
                "form": form,
                "date_from": date_from,
                "date_to": date_to,
                "weeks": self._summary_weeks(
                    _week_groups(date_from, date_to),
                    rooms,
                    blocks_by_room,
                    today,
                    selected_weekdays,
                    selected_tenant_doctor,
                    selected_date,
                ),
                "selected_date": selected_date,
                "detail_rows": page_obj.object_list,
                "page_obj": page_obj,
                "paginator": page_obj.paginator,
                "is_paginated": page_obj.has_other_pages(),
                "pagination_querystring": self._pagination_querystring(),
            }
        )
        return context

    def _selected_date(self, date_from: date, date_to: date) -> date:
        selected_date_text = self.request.GET.get("selected_date")
        if selected_date_text:
            try:
                selected_date = date.fromisoformat(selected_date_text)
            except ValueError:
                selected_date = date_from
            else:
                if date_from <= selected_date <= date_to:
                    return selected_date
        today = timezone.localdate()
        if date_from <= today <= date_to:
            return today
        return date_from

    @staticmethod
    def _blocks_by_room(
        rooms: list[ConsultingRoom],
        date_from: date,
        date_to: date,
    ) -> dict[ConsultingRoom, dict[date, list[Any]]]:
        result: dict[ConsultingRoom, dict[date, list[Any]]] = {}
        for room in rooms:
            grouped_blocks: dict[date, list[Any]] = {
                day: [] for day in _date_range(date_from, date_to)
            }
            for block in generate_availability_blocks(room, date_from, date_to):
                grouped_blocks[block.date].append(block)
            result[room] = grouped_blocks
        return result

    def _summary_weeks(
        self,
        weeks: list[list[date]],
        rooms: list[ConsultingRoom],
        blocks_by_room: dict[ConsultingRoom, dict[date, list[Any]]],
        today: date,
        selected_weekdays: set[int],
        selected_tenant_doctor: Any,
        selected_date: date,
    ) -> list[dict[str, Any]]:
        return [
            {
                "week_start": week[0],
                "week_end": week[-1],
                "days": [
                    self._day_summary(
                        day,
                        rooms,
                        blocks_by_room,
                        today,
                        selected_weekdays,
                        selected_tenant_doctor,
                        selected_date,
                    )
                    for day in week
                ],
            }
            for week in weeks
        ]

    def _day_summary(
        self,
        day: date,
        rooms: list[ConsultingRoom],
        blocks_by_room: dict[ConsultingRoom, dict[date, list[Any]]],
        today: date,
        selected_weekdays: set[int],
        selected_tenant_doctor: Any,
        selected_date: date,
    ) -> dict[str, Any]:
        is_filtered = bool(selected_weekdays) and day.weekday() not in selected_weekdays
        day_blocks = (
            []
            if is_filtered
            else self._blocks_for_day(
                rooms,
                blocks_by_room,
                day,
                selected_tenant_doctor,
            )
        )
        has_free = any(block.status == BLOCK_STATUS_FREE for block in day_blocks)
        has_reserved = any(
            block.status == BLOCK_STATUS_RESERVED_FUTURE for block in day_blocks
        )

        if is_filtered:
            status = {
                "label": "Filtrado",
                "icon": "bi-dash-lg",
                "css_class": "bg-light text-secondary border-secondary-subtle",
            }
        elif day < today:
            status = (
                {
                    "label": "Pasado con reservación",
                    "icon": "bi-calendar-check",
                    "css_class": "bg-secondary text-white border-secondary",
                }
                if has_reserved
                else {
                    "label": "Pasado sin reservación",
                    "icon": "bi-dash-lg",
                    "css_class": "bg-light text-secondary border-secondary-subtle",
                }
            )
        elif has_free:
            status = {
                "label": "Consultorios libres",
                "icon": "bi-check-lg",
                "css_class": "bg-success text-white border-success",
            }
        elif has_reserved:
            status = {
                "label": "Sólo reservados",
                "icon": "bi-exclamation-triangle",
                "css_class": "bg-warning text-dark border-warning",
            }
        else:
            status = {
                "label": "Sin consultorios libres",
                "icon": "bi-x-lg",
                "css_class": "bg-danger text-white border-danger",
            }

        return {
            "date": day,
            "url": self._date_url(day),
            "is_selected": day == selected_date,
            **status,
        }

    @staticmethod
    def _blocks_for_day(
        rooms: list[ConsultingRoom],
        blocks_by_room: dict[ConsultingRoom, dict[date, list[Any]]],
        selected_date: date,
        selected_tenant_doctor: Any,
    ) -> list[Any]:
        blocks: list[Any] = []
        for room in rooms:
            room_blocks = blocks_by_room.get(room, {}).get(selected_date, [])
            blocks.extend(
                WeeklyCalendarView._filter_blocks_by_tenant_doctor(
                    room_blocks,
                    selected_tenant_doctor,
                )
            )
        return blocks

    def _detail_rows(
        self,
        rooms: list[ConsultingRoom],
        blocks_by_room: dict[ConsultingRoom, dict[date, list[Any]]],
        selected_date: date,
        today: date,
        selected_tenant_doctor: Any,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for room in rooms:
            blocks = WeeklyCalendarView._filter_blocks_by_tenant_doctor(
                blocks_by_room.get(room, {}).get(selected_date, []),
                selected_tenant_doctor,
            )
            if not blocks:
                rows.append(
                    {
                        "room": room,
                        "date": selected_date,
                        "start_time": None,
                        "end_time": None,
                        "status": "Sin disponibilidad",
                        "label": "Sin bloques calculados",
                        "tenant_doctor": "",
                        "reservation_url": "",
                    }
                )
                continue
            for block in blocks:
                rows.append(self._detail_row(room, block, selected_date, today))
        return rows

    @staticmethod
    def _detail_row(
        room: ConsultingRoom,
        block: Any,
        selected_date: date,
        today: date,
    ) -> dict[str, Any]:
        tenant_doctor = ""
        if block.reservation:
            tenant_doctor = str(block.reservation.tenant_doctor)
        status_labels = {
            BLOCK_STATUS_FREE: "Libre",
            BLOCK_STATUS_RESERVED_FUTURE: "Reservado",
            BLOCK_STATUS_EXCEPTION: "Bloqueado",
        }
        reservation_url = ""
        if block.status == BLOCK_STATUS_FREE and selected_date >= today:
            query = urlencode(
                {
                    "room": str(room.pk),
                    "date": selected_date.isoformat(),
                    "start_time": f"{block.start_time:%H:%M}",
                    "end_time": f"{block.end_time:%H:%M}",
                }
            )
            reservation_url = f"{reverse('reservation_request')}?{query}"
        return {
            "room": room,
            "date": selected_date,
            "start_time": block.start_time,
            "end_time": block.end_time,
            "status": status_labels.get(block.status, block.status),
            "label": block.label,
            "tenant_doctor": tenant_doctor,
            "reservation_url": reservation_url,
        }

    def _date_url(self, selected_date: date) -> str:
        params = self.request.GET.copy()
        params.pop("page", None)
        params["selected_date"] = selected_date.isoformat()
        return f"{reverse('calendar_quick')}?{urlencode(params, doseq=True)}"

    def _pagination_querystring(self) -> str:
        params = self.request.GET.copy()
        params.pop("page", None)
        return urlencode(params, doseq=True)
