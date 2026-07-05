"""Document vault views."""

from datetime import date
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Model, Q, QuerySet
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import FormMixin

from apps.core.form_utils import django_weekday_values
from apps.core.permissions import scope_queryset_for_user
from apps.vault.forms import DocumentFilterForm, DocumentRejectForm, DocumentUploadForm
from apps.vault.models import DocumentAsset
from apps.vault.services.document_service import (
    approve_document,
    cancel_document,
    entity_from_document,
    mark_document_in_review,
    reject_document,
    upload_document,
)


def _cleaned_filter_data(form: Any) -> dict[str, Any]:
    if form.is_bound:
        form.is_valid()
        return form.cleaned_data
    return {}


def _effective_date_range(form: Any, cleaned_data: dict[str, Any]) -> tuple[date, date]:
    date_from = cleaned_data.get("date_from") or form.initial["date_from"]
    date_to = cleaned_data.get("date_to") or form.initial["date_to"]
    return cast(date, date_from), cast(date, date_to)


class DocumentListView(LoginRequiredMixin, ListView):
    template_name = "vault/document_list.html"
    context_object_name = "documents"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[DocumentAsset]:
        queryset = DocumentAsset.objects.filter(is_deleted=False).select_related(
            "owner",
            "owner__user",
            "tenant_doctor",
            "tenant_doctor__user",
            "room",
            "room__clinic",
            "reservation",
            "payment",
            "settlement",
        )
        self.filter_form = DocumentFilterForm(
            self.request.GET or None,
            user=self.request.user,
        )
        cleaned_data = _cleaned_filter_data(self.filter_form)
        search_query = cleaned_data.get("q")
        clinic = cleaned_data.get("clinic")
        owner = cleaned_data.get("owner")
        room = cleaned_data.get("room")
        tenant_doctor = cleaned_data.get("tenant_doctor")
        document_type = cleaned_data.get("document_type")
        status = cleaned_data.get("status")
        entity = cleaned_data.get("entity")
        weekdays = cleaned_data.get("weekdays") or []
        date_from, date_to = _effective_date_range(self.filter_form, cleaned_data)

        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query)
                | Q(original_name__icontains=search_query)
                | Q(sha256_hash__icontains=search_query)
                | Q(notes__icontains=search_query)
                | Q(owner__display_name__icontains=search_query)
                | Q(owner__user__email__icontains=search_query)
                | Q(tenant_doctor__display_name__icontains=search_query)
                | Q(tenant_doctor__user__email__icontains=search_query)
                | Q(room__name__icontains=search_query)
                | Q(room__clinic__name__icontains=search_query)
                | Q(payment__reference__icontains=search_query)
                | Q(settlement__payment_reference__icontains=search_query)
            )
        if document_type:
            queryset = queryset.filter(document_type=document_type)
        if status:
            queryset = queryset.filter(status=status)
        if clinic:
            queryset = queryset.filter(
                Q(room__clinic=clinic)
                | Q(reservation__room__clinic=clinic)
                | Q(payment__reservation__room__clinic=clinic)
                | Q(settlement__room__clinic=clinic)
            )
        if owner:
            queryset = queryset.filter(
                Q(owner=owner)
                | Q(room__owner=owner)
                | Q(reservation__room__owner=owner)
                | Q(payment__reservation__room__owner=owner)
                | Q(settlement__owner=owner)
            )
        if room:
            queryset = queryset.filter(
                Q(room=room)
                | Q(reservation__room=room)
                | Q(payment__reservation__room=room)
                | Q(settlement__room=room)
            )
        if tenant_doctor:
            queryset = queryset.filter(
                Q(tenant_doctor=tenant_doctor)
                | Q(reservation__tenant_doctor=tenant_doctor)
                | Q(payment__tenant_doctor=tenant_doctor)
                | Q(settlement__reservation__tenant_doctor=tenant_doctor)
            )
        if entity:
            queryset = queryset.filter(
                Q(owner__display_name__icontains=entity)
                | Q(owner__user__email__icontains=entity)
                | Q(tenant_doctor__display_name__icontains=entity)
                | Q(tenant_doctor__user__email__icontains=entity)
                | Q(room__name__icontains=entity)
                | Q(room__clinic__name__icontains=entity)
                | Q(reservation__room__name__icontains=entity)
                | Q(payment__reference__icontains=entity)
                | Q(settlement__payment_reference__icontains=entity)
            )
        queryset = queryset.filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        if weekdays:
            queryset = queryset.filter(
                created_at__week_day__in=django_weekday_values(weekdays)
            )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Documentos"
        context["filter_form"] = self.filter_form
        context["document_rows"] = [
            {"document": document, "entity": entity_from_document(document)}
            for document in context["documents"]
        ]
        return context


class DocumentDetailView(LoginRequiredMixin, DetailView):
    template_name = "vault/document_detail.html"
    context_object_name = "document"

    def get_queryset(self) -> QuerySet[DocumentAsset]:
        queryset = DocumentAsset.objects.filter(is_deleted=False).select_related(
            "owner",
            "owner__user",
            "tenant_doctor",
            "tenant_doctor__user",
            "room",
            "room__clinic",
            "reservation",
            "payment",
            "settlement",
            "reviewed_by",
        )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Detalle de documento"
        context["document_entity"] = entity_from_document(context["document"])
        return context


class DocumentUploadView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "vault/document_form.html"
    form_class = DocumentUploadForm

    def get_initial(self) -> dict[str, Any]:
        initial = super().get_initial()
        for field_name in (
            "owner",
            "tenant_doctor",
            "room",
            "reservation",
            "payment",
            "settlement",
        ):
            value = self.request.GET.get(field_name)
            if value:
                initial[field_name] = value
        return initial

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Subir documento"
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: DocumentUploadForm) -> HttpResponse:
        try:
            document = upload_document(
                title=form.cleaned_data["title"],
                document_type=form.cleaned_data["document_type"],
                file=form.cleaned_data["file"],
                notes=form.cleaned_data["notes"],
                actor=cast(Model, self.request.user),
                owner=form.cleaned_data["owner"],
                tenant_doctor=form.cleaned_data["tenant_doctor"],
                room=form.cleaned_data["room"],
                reservation=form.cleaned_data["reservation"],
                payment=form.cleaned_data["payment"],
                settlement=form.cleaned_data["settlement"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(self.request, "Documento recibido.")
        return redirect("document_detail", pk=document.pk)


class DocumentFileView(LoginRequiredMixin, DetailView):
    def get_queryset(self) -> QuerySet[DocumentAsset]:
        return DocumentAsset.objects.filter(is_deleted=False)

    def get(
        self,
        request: HttpRequest,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        document = self.get_object()
        return cast(
            HttpResponse,
            FileResponse(
                document.file.open("rb"),
                as_attachment=False,
                filename=document.original_name,
                content_type=document.mime_type or "application/octet-stream",
            ),
        )


class DocumentInReviewView(LoginRequiredMixin, TemplateView):
    template_name = "vault/document_in_review.html"

    def get_document(self) -> DocumentAsset:
        return DocumentAsset.objects.get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Enviar documento a revisión"
        context["document"] = self.get_document()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        document = self.get_document()
        mark_document_in_review(document=document, actor=cast(Model, request.user))
        messages.success(request, "Documento enviado a revisión.")
        return redirect("document_detail", pk=document.pk)


class DocumentApproveView(LoginRequiredMixin, TemplateView):
    template_name = "vault/document_approve.html"

    def get_document(self) -> DocumentAsset:
        return DocumentAsset.objects.get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Aprobar documento"
        context["document"] = self.get_document()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        document = self.get_document()
        approve_document(document=document, actor=cast(Model, request.user))
        messages.success(request, "Documento aprobado.")
        return redirect("document_detail", pk=document.pk)


class DocumentRejectView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "vault/document_reject.html"
    form_class = DocumentRejectForm

    def get_document(self) -> DocumentAsset:
        return DocumentAsset.objects.get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Rechazar documento"
        context["document"] = self.get_document()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            document = self.get_document()
            try:
                reject_document(
                    document=document,
                    reason=form.cleaned_data["reason"],
                    actor=cast(Model, request.user),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
                return self.form_invalid(form)
            messages.success(request, "Documento rechazado.")
            return redirect("document_detail", pk=document.pk)
        return self.form_invalid(form)


class DocumentCancelView(LoginRequiredMixin, TemplateView):
    template_name = "vault/document_cancel.html"

    def get_document(self) -> DocumentAsset:
        return DocumentAsset.objects.get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Cancelar documento"
        context["document"] = self.get_document()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        document = self.get_document()
        cancel_document(document=document, actor=cast(Model, request.user))
        messages.success(request, "Documento cancelado.")
        return redirect("document_detail", pk=document.pk)
