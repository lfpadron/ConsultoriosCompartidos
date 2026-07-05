"""Finance views for rate rules and manual payments."""

from datetime import date
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Model, Q, QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import FormMixin

from apps.astrotrace.services import record_event
from apps.core.form_utils import django_weekday_values
from apps.core.permissions import scope_queryset_for_user
from apps.core.templatetags.clinic_time import format_time_for_clinic
from apps.finance.forms import (
    PaymentFilterForm,
    PaymentRegistrationForm,
    PaymentRejectForm,
    RateRuleFilterForm,
    RateRuleForm,
    SettlementFilterForm,
    SettlementGenerateForm,
    SettlementPaidForm,
)
from apps.finance.models import Payment, RateRule, Settlement
from apps.finance.services.payment_service import (
    cancel_payment,
    get_payment_summary_for_reservation,
    register_payment,
    reject_payment,
    validate_payment,
)
from apps.finance.services.settlement_service import (
    cancel_settlement,
    generate_settlement_for_reservation,
    get_settlement_summary_for_owner,
    mark_settlement_as_paid,
)
from apps.scheduling.models import Reservation, Weekday
from apps.vault.services.document_service import (
    get_document_field_for_object,
    get_documents_for_object,
)


def resolve_value(instance: RateRule, field_path: str) -> str:
    if field_path == "weekdays":
        return _format_weekdays(instance.weekdays)
    if field_path == "time_range":
        return (
            f"{format_time_for_clinic(instance.start_time, instance.room)} - "
            f"{format_time_for_clinic(instance.end_time, instance.room)}"
        )
    if field_path == "amount":
        return f"{instance.amount} {instance.currency}"

    value: Any = instance
    for attr in field_path.split("."):
        value = getattr(value, attr)
        if callable(value):
            value = value()

    if isinstance(value, bool):
        return "Activo" if value else "Inactivo"
    if isinstance(value, date):
        return f"{value:%Y-%m-%d}"
    return str(value) if value not in ("", None) else "Sin datos"


def _cleaned_filter_data(form: Any) -> dict[str, Any]:
    if form.is_bound:
        form.is_valid()
        return form.cleaned_data
    return {}


def _effective_date_range(form: Any, cleaned_data: dict[str, Any]) -> tuple[date, date]:
    date_from = cleaned_data.get("date_from") or form.initial["date_from"]
    date_to = cleaned_data.get("date_to") or form.initial["date_to"]
    return cast(date, date_from), cast(date, date_to)


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


class RateRuleBaseMixin(LoginRequiredMixin):
    list_columns = (
        ("Nombre", "name"),
        ("Consultorio", "room"),
        ("Días", "weekdays"),
        ("Horario", "time_range"),
        ("Tipo", "get_price_type_display"),
        ("Importe", "amount"),
        ("Prioridad", "priority"),
        ("Estado", "is_active"),
    )
    detail_fields = (
        ("Consultorio", "room"),
        ("Nombre", "name"),
        ("Días", "weekdays"),
        ("Hora inicio", "start_time"),
        ("Hora fin", "end_time"),
        ("Fecha inicio", "start_date"),
        ("Fecha fin", "end_date"),
        ("Tipo de precio", "get_price_type_display"),
        ("Importe", "amount"),
        ("Prioridad", "priority"),
        ("Notas", "notes"),
        ("Estado", "is_active"),
    )

    def get_queryset(self) -> QuerySet[RateRule]:
        return RateRule.objects.filter(is_deleted=False).select_related(
            "room",
            "room__clinic",
            "room__owner",
        )

    def add_rate_context(self, context: dict[str, Any]) -> dict[str, Any]:
        context["page_title"] = "Tarifas"
        return context


class RateRuleListView(RateRuleBaseMixin, ListView):
    template_name = "finance/rate_rule_list.html"
    context_object_name = "objects"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[RateRule]:
        queryset = super().get_queryset()
        self.filter_form = RateRuleFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(self.filter_form)
        clinic = cleaned_data.get("clinic")
        owner = cleaned_data.get("owner")
        room = cleaned_data.get("room")
        is_active = cleaned_data.get("is_active")
        weekdays = cleaned_data.get("weekdays") or []
        date_from, date_to = _effective_date_range(self.filter_form, cleaned_data)

        if clinic:
            queryset = queryset.filter(room__clinic=clinic)
        if owner:
            queryset = queryset.filter(room__owner=owner)
        if room:
            queryset = queryset.filter(room=room)
        queryset = queryset.filter(start_date__lte=date_to).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=date_from)
        )
        if weekdays:
            rule_ids = [
                rule.pk
                for rule in queryset
                if set(rule.weekdays).intersection(set(weekdays))
            ]
            queryset = queryset.filter(pk__in=rule_ids)
        if is_active == "true":
            queryset = queryset.filter(is_active=True)
        elif is_active == "false":
            queryset = queryset.filter(is_active=False)
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_rate_context(super().get_context_data(**kwargs))
        objects = context["objects"]
        context["filter_form"] = self.filter_form
        context["rows"] = [
            {
                "object": item,
                "cells": [
                    resolve_value(item, field_path)
                    for _, field_path in self.list_columns
                ],
            }
            for item in objects
        ]
        context["list_columns"] = self.list_columns
        return context


class RateRuleDetailView(RateRuleBaseMixin, DetailView):
    template_name = "finance/rate_rule_detail.html"
    context_object_name = "object"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_rate_context(super().get_context_data(**kwargs))
        instance = context["object"]
        context["field_rows"] = [
            (label, resolve_value(instance, field_path))
            for label, field_path in self.detail_fields
        ]
        return context


class RateRuleFormView(RateRuleBaseMixin, FormMixin, TemplateView):
    template_name = "finance/rate_rule_form.html"
    form_class: type[ModelForm] = RateRuleForm
    object: RateRule | None = None
    is_create = False

    def get_object(self) -> RateRule | None:
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
        context = self.add_rate_context(super().get_context_data(**kwargs))
        action = "Alta" if self.is_create else "Edición"
        context["page_title"] = f"{action} de regla tarifaria"
        context["object"] = self.get_object()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: ModelForm) -> HttpResponse:
        instance = form.save(commit=False)
        user = cast(Any, self.request.user)
        if self.is_create:
            instance.created_by = user
        instance.updated_by = user
        instance.save()
        self.object = instance
        self._record_trace(instance)
        messages.success(self.request, "Regla tarifaria guardada.")
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        instance = self.get_object()
        if instance is None:
            return reverse("rates")
        return reverse("rate_detail", kwargs={"pk": instance.pk})

    def _record_trace(self, instance: RateRule) -> None:
        record_event(
            event_type=("rate_rule.created" if self.is_create else "rate_rule.updated"),
            object_label=str(instance),
            actor=cast(Model, self.request.user),
            payload=_trace_payload(instance),
        )


class RateRuleDeactivateView(RateRuleBaseMixin, TemplateView):
    template_name = "finance/rate_rule_confirm_deactivate.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_rate_context(super().get_context_data(**kwargs))
        context["object"] = self.get_queryset().get(pk=self.kwargs["pk"])
        context["page_title"] = "Desactivar regla tarifaria"
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        instance = self.get_queryset().get(pk=self.kwargs["pk"])
        instance.is_active = False
        instance.updated_by = cast(Any, request.user)
        instance.save(update_fields=["is_active", "updated_by", "updated_at"])
        record_event(
            event_type="rate_rule.deactivated",
            object_label=str(instance),
            actor=cast(Model, request.user),
            payload={**_trace_payload(instance), "action": "deactivate"},
        )
        messages.success(request, "Regla tarifaria desactivada.")
        return redirect("rates")


class RateRuleCreateView(RateRuleFormView):
    is_create = True


class RateRuleUpdateView(RateRuleFormView):
    pass


class PaymentListView(LoginRequiredMixin, ListView):
    template_name = "finance/payment_list.html"
    context_object_name = "payments"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[Payment]:
        queryset = Payment.objects.filter(is_deleted=False).select_related(
            "reservation",
            "reservation__room",
            "reservation__room__clinic",
            "statement",
            "tenant_doctor",
            "tenant_doctor__user",
        )
        queryset = queryset.select_related("reservation__room__owner")
        self.filter_form = PaymentFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(self.filter_form)
        search_query = cleaned_data.get("q")
        status = cleaned_data.get("status")
        clinic = cleaned_data.get("clinic")
        owner = cleaned_data.get("owner")
        room = cleaned_data.get("room")
        tenant_doctor = cleaned_data.get("tenant_doctor")
        weekdays = cleaned_data.get("weekdays") or []
        date_from, date_to = _effective_date_range(self.filter_form, cleaned_data)

        if search_query:
            queryset = queryset.filter(
                Q(reference__icontains=search_query)
                | Q(notes__icontains=search_query)
                | Q(reservation__room__name__icontains=search_query)
                | Q(reservation__room__clinic__name__icontains=search_query)
                | Q(tenant_doctor__display_name__icontains=search_query)
                | Q(tenant_doctor__user__email__icontains=search_query)
            )
        if status:
            queryset = queryset.filter(status=status)
        if clinic:
            queryset = queryset.filter(reservation__room__clinic=clinic)
        if owner:
            queryset = queryset.filter(reservation__room__owner=owner)
        if room:
            queryset = queryset.filter(reservation__room=room)
        if tenant_doctor:
            queryset = queryset.filter(tenant_doctor=tenant_doctor)
        queryset = queryset.filter(
            payment_date__gte=date_from, payment_date__lte=date_to
        )
        if weekdays:
            queryset = queryset.filter(
                reservation__date__week_day__in=django_weekday_values(weekdays)
            )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Pagos"
        context["filter_form"] = self.filter_form
        return context


class PaymentDetailView(LoginRequiredMixin, DetailView):
    template_name = "finance/payment_detail.html"
    context_object_name = "payment"

    def get_queryset(self) -> QuerySet[Payment]:
        queryset = Payment.objects.filter(is_deleted=False).select_related(
            "reservation",
            "reservation__room",
            "reservation__room__clinic",
            "statement",
            "tenant_doctor",
            "tenant_doctor__user",
            "validated_by",
        )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        payment = context["payment"]
        context["page_title"] = "Detalle de pago"
        context["payment_summary"] = get_payment_summary_for_reservation(
            payment.reservation
        )
        context["related_documents"] = get_documents_for_object(payment)
        context["document_upload_field"] = get_document_field_for_object(payment)
        return context


class PaymentRegisterView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "finance/payment_form.html"
    form_class = PaymentRegistrationForm

    def get_reservation(self) -> Reservation:
        return Reservation.objects.select_related(
            "room",
            "room__clinic",
            "tenant_doctor",
            "tenant_doctor__user",
        ).get(pk=self.kwargs["reservation_pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        reservation = self.get_reservation()
        context["page_title"] = "Registrar pago"
        context["reservation"] = reservation
        context["payment_summary"] = get_payment_summary_for_reservation(reservation)
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: PaymentRegistrationForm) -> HttpResponse:
        reservation = self.get_reservation()
        try:
            payment = register_payment(
                reservation=reservation,
                amount=form.cleaned_data["amount"],
                currency=form.cleaned_data["currency"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
                payment_date=form.cleaned_data["payment_date"],
                receipt=form.cleaned_data.get("receipt"),
                notes=form.cleaned_data["notes"],
                actor=cast(Model, self.request.user),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(self.request, "Pago registrado.")
        return redirect("payment_detail", pk=payment.pk)


class PaymentValidateView(LoginRequiredMixin, TemplateView):
    template_name = "finance/payment_validate.html"

    def get_payment(self) -> Payment:
        return Payment.objects.select_related("reservation", "statement").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Validar pago"
        context["payment"] = self.get_payment()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        payment = self.get_payment()
        try:
            validate_payment(payment=payment, actor=cast(Model, request.user))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("payment_detail", pk=payment.pk)

        messages.success(request, "Pago validado.")
        return redirect("payment_detail", pk=payment.pk)


class PaymentRejectView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "finance/payment_reject.html"
    form_class = PaymentRejectForm

    def get_payment(self) -> Payment:
        return Payment.objects.select_related("reservation", "statement").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Rechazar pago"
        context["payment"] = self.get_payment()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            payment = self.get_payment()
            try:
                reject_payment(
                    payment=payment,
                    reason=form.cleaned_data["reason"],
                    actor=cast(Model, request.user),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
                return self.form_invalid(form)

            messages.success(request, "Pago rechazado.")
            return redirect("payment_detail", pk=payment.pk)
        return self.form_invalid(form)


class PaymentCancelView(LoginRequiredMixin, TemplateView):
    template_name = "finance/payment_cancel.html"

    def get_payment(self) -> Payment:
        return Payment.objects.select_related("reservation", "statement").get(
            pk=self.kwargs["pk"],
            is_deleted=False,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Cancelar pago"
        context["payment"] = self.get_payment()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        payment = self.get_payment()
        try:
            cancel_payment(payment=payment, actor=cast(Model, request.user))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("payment_detail", pk=payment.pk)

        messages.success(request, "Pago cancelado.")
        return redirect("payment_detail", pk=payment.pk)


class SettlementListView(LoginRequiredMixin, ListView):
    template_name = "finance/settlement_list.html"
    context_object_name = "settlements"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[Settlement]:
        queryset = Settlement.objects.filter(is_deleted=False).select_related(
            "reservation",
            "owner",
            "owner__user",
            "room",
            "room__clinic",
            "statement",
        )
        self.filter_form = SettlementFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(self.filter_form)
        search_query = cleaned_data.get("q")
        status = cleaned_data.get("status")
        owner = cleaned_data.get("owner")
        clinic = cleaned_data.get("clinic")
        room = cleaned_data.get("room")
        weekdays = cleaned_data.get("weekdays") or []
        date_from, date_to = _effective_date_range(self.filter_form, cleaned_data)

        if search_query:
            queryset = queryset.filter(
                Q(payment_reference__icontains=search_query)
                | Q(notes__icontains=search_query)
                | Q(owner__display_name__icontains=search_query)
                | Q(owner__user__email__icontains=search_query)
                | Q(room__name__icontains=search_query)
                | Q(room__clinic__name__icontains=search_query)
                | Q(reservation__tenant_doctor__display_name__icontains=search_query)
                | Q(reservation__tenant_doctor__user__email__icontains=search_query)
            )
        if status:
            queryset = queryset.filter(status=status)
        if owner:
            queryset = queryset.filter(owner=owner)
        if clinic:
            queryset = queryset.filter(room__clinic=clinic)
        if room:
            queryset = queryset.filter(room=room)
        queryset = queryset.filter(
            generated_at__date__gte=date_from,
            generated_at__date__lte=date_to,
        )
        if weekdays:
            queryset = queryset.filter(
                reservation__date__week_day__in=django_weekday_values(weekdays)
            )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Liquidaciones"
        context["filter_form"] = self.filter_form
        return context


class SettlementDetailView(LoginRequiredMixin, DetailView):
    template_name = "finance/settlement_detail.html"
    context_object_name = "settlement"

    def get_queryset(self) -> QuerySet[Settlement]:
        queryset = Settlement.objects.filter(is_deleted=False).select_related(
            "reservation",
            "owner",
            "owner__user",
            "room",
            "room__clinic",
            "statement",
            "paid_by",
        )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        settlement = context["settlement"]
        context["page_title"] = "Detalle de liquidación"
        context["owner_summary"] = get_settlement_summary_for_owner(settlement.owner)
        context["related_documents"] = get_documents_for_object(settlement)
        context["document_upload_field"] = get_document_field_for_object(settlement)
        return context


class SettlementGenerateView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "finance/settlement_generate.html"
    form_class = SettlementGenerateForm

    def get_reservation(self) -> Reservation:
        return Reservation.objects.select_related(
            "room",
            "room__clinic",
            "room__owner",
            "tenant_doctor",
            "tenant_doctor__user",
        ).get(pk=self.kwargs["reservation_pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        reservation = self.get_reservation()
        context["page_title"] = "Generar liquidación"
        context["reservation"] = reservation
        context["statement"] = reservation.statements.order_by("-version").first()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: SettlementGenerateForm) -> HttpResponse:
        reservation = self.get_reservation()
        try:
            settlement = generate_settlement_for_reservation(
                reservation=reservation,
                notes=form.cleaned_data["notes"],
                actor=cast(Model, self.request.user),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(self.request, "Liquidación generada.")
        return redirect("settlement_detail", pk=settlement.pk)


class SettlementPaidView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "finance/settlement_paid.html"
    form_class = SettlementPaidForm

    def get_settlement(self) -> Settlement:
        return Settlement.objects.select_related(
            "reservation",
            "owner",
            "room",
            "statement",
        ).get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Marcar liquidación como pagada"
        context["settlement"] = self.get_settlement()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            settlement = self.get_settlement()
            try:
                mark_settlement_as_paid(
                    settlement=settlement,
                    reference=form.cleaned_data["reference"],
                    payment_date=form.cleaned_data["payment_date"],
                    notes=form.cleaned_data["notes"],
                    actor=cast(Model, request.user),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
                return self.form_invalid(form)

            messages.success(request, "Liquidación marcada como pagada.")
            return redirect("settlement_detail", pk=settlement.pk)
        return self.form_invalid(form)


class SettlementCancelView(LoginRequiredMixin, TemplateView):
    template_name = "finance/settlement_cancel.html"

    def get_settlement(self) -> Settlement:
        return Settlement.objects.select_related(
            "reservation",
            "owner",
            "room",
            "statement",
        ).get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Cancelar liquidación"
        context["settlement"] = self.get_settlement()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        settlement = self.get_settlement()
        try:
            cancel_settlement(settlement=settlement, actor=cast(Model, request.user))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("settlement_detail", pk=settlement.pk)

        messages.success(request, "Liquidación cancelada.")
        return redirect("settlement_detail", pk=settlement.pk)


def _trace_payload(instance: RateRule) -> dict[str, str]:
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
