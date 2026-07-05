"""HTML and CSV views for MVP reports."""

import csv
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from apps.astrotrace.services import record_event
from apps.reports.forms import (
    DocumentsReportFilterForm,
    IncomeReportFilterForm,
    OccupancyReportFilterForm,
    PaymentsReportFilterForm,
    SettlementsReportFilterForm,
    TraceabilityReportFilterForm,
)
from apps.reports.services.report_service import (
    ReportResult,
    format_report_value,
    get_documents_report,
    get_income_by_room_report,
    get_occupancy_report,
    get_payments_report,
    get_settlements_report,
    get_traceability_report,
    report_rows_for_display,
    report_totals_for_display,
    serialize_report_filters,
)


@dataclass(frozen=True)
class ReportDefinition:
    slug: str
    title: str
    description: str
    icon: str
    html_url_name: str
    csv_url_name: str
    csv_filename: str
    form_class: type[Any]
    selector: Callable[[dict[str, Any] | None], ReportResult]


REPORT_DEFINITIONS: dict[str, ReportDefinition] = {
    "ocupacion": ReportDefinition(
        slug="ocupacion",
        title="Ocupación",
        description=(
            "Disponibilidad, reservaciones activas y ocupación por consultorio."
        ),
        icon="bi-calendar-week",
        html_url_name="report_occupancy",
        csv_url_name="report_occupancy_csv",
        csv_filename="reporte_ocupacion.csv",
        form_class=OccupancyReportFilterForm,
        selector=get_occupancy_report,
    ),
    "ingresos": ReportDefinition(
        slug="ingresos",
        title="Ingresos",
        description="Subtotal reservado, pagos, saldos, comisiones y neto propietario.",
        icon="bi-cash-coin",
        html_url_name="report_income",
        csv_url_name="report_income_csv",
        csv_filename="reporte_ingresos.csv",
        form_class=IncomeReportFilterForm,
        selector=get_income_by_room_report,
    ),
    "pagos": ReportDefinition(
        slug="pagos",
        title="Pagos",
        description="Estado de cuenta, pagos validados y saldo por reservación.",
        icon="bi-credit-card",
        html_url_name="report_payments",
        csv_url_name="report_payments_csv",
        csv_filename="reporte_pagos.csv",
        form_class=PaymentsReportFilterForm,
        selector=get_payments_report,
    ),
    "liquidaciones": ReportDefinition(
        slug="liquidaciones",
        title="Liquidaciones",
        description="Subtotal, comisión, neto propietario y estado de liquidación.",
        icon="bi-bank",
        html_url_name="report_settlements",
        csv_url_name="report_settlements_csv",
        csv_filename="reporte_liquidaciones.csv",
        form_class=SettlementsReportFilterForm,
        selector=get_settlements_report,
    ),
    "documentos": ReportDefinition(
        slug="documentos",
        title="Documentos",
        description="Documentos recibidos, entidad relacionada, versión y hash.",
        icon="bi-file-earmark-text",
        html_url_name="report_documents",
        csv_url_name="report_documents_csv",
        csv_filename="reporte_documentos.csv",
        form_class=DocumentsReportFilterForm,
        selector=get_documents_report,
    ),
    "trazabilidad": ReportDefinition(
        slug="trazabilidad",
        title="Trazabilidad",
        description="Eventos AstroTrace ordenados para auditoría básica.",
        icon="bi-diagram-3",
        html_url_name="report_traceability",
        csv_url_name="report_traceability_csv",
        csv_filename="reporte_trazabilidad.csv",
        form_class=TraceabilityReportFilterForm,
        selector=get_traceability_report,
    ),
}


class ReportIndexView(LoginRequiredMixin, TemplateView):
    template_name = "reports/index.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Reportes"
        context["reports"] = REPORT_DEFINITIONS.values()
        return context


@login_required
@require_GET
def report_html(request: HttpRequest, report_type: str) -> HttpResponse:
    definition = _get_definition(report_type)
    form, result = _build_report(request, definition)
    query_string = request.GET.urlencode()
    csv_url = reverse(definition.csv_url_name)
    if query_string:
        csv_url = f"{csv_url}?{query_string}"
    return render(
        request,
        "reports/report.html",
        {
            "page_title": f"Reporte: {definition.title}",
            "definition": definition,
            "filter_form": form,
            "result": result,
            "table_rows": report_rows_for_display(result),
            "display_totals": report_totals_for_display(result),
            "csv_url": csv_url,
        },
    )


@login_required
@require_GET
def report_csv(request: HttpRequest, report_type: str) -> HttpResponse:
    definition = _get_definition(report_type)
    form, result = _build_report(request, definition)
    filters = form.cleaned_data if form.is_valid() else {}
    _record_export_event(request, definition, filters)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="{definition.csv_filename}"'
    )
    writer = csv.writer(response)
    writer.writerow([column.label for column in result.columns])
    for row in result.rows:
        writer.writerow(
            [format_report_value(row.get(column.key, "")) for column in result.columns]
        )
    return response


def _build_report(
    request: HttpRequest,
    definition: ReportDefinition,
) -> tuple[Any, ReportResult]:
    form = definition.form_class(request.GET or None)
    if form.is_bound:
        filters = form.cleaned_data if form.is_valid() else {}
    else:
        filters = {
            key: value
            for key, value in form.initial.items()
            if key in form.fields and value not in ("", None)
        }
    return form, definition.selector(filters)


def _get_definition(report_type: str) -> ReportDefinition:
    try:
        return REPORT_DEFINITIONS[report_type]
    except KeyError as exc:
        raise Http404("Reporte no encontrado.") from exc


def _record_export_event(
    request: HttpRequest,
    definition: ReportDefinition,
    filters: dict[str, Any],
) -> None:
    user = request.user
    record_event(
        event_type="report.exported",
        object_label=definition.title,
        actor=cast(Model, user),
        payload={
            "level": "informativo",
            "report_type": definition.slug,
            "filters": serialize_report_filters(filters),
            "user": getattr(user, "email", ""),
        },
    )
