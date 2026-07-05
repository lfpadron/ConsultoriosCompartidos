"""Report URL patterns."""

from django.urls import path

from apps.reports import views

urlpatterns = [
    path("reportes/", views.ReportIndexView.as_view(), name="reports"),
    path(
        "reportes/ocupacion/",
        views.report_html,
        {"report_type": "ocupacion"},
        name="report_occupancy",
    ),
    path(
        "reportes/ocupacion.csv",
        views.report_csv,
        {"report_type": "ocupacion"},
        name="report_occupancy_csv",
    ),
    path(
        "reportes/ingresos/",
        views.report_html,
        {"report_type": "ingresos"},
        name="report_income",
    ),
    path(
        "reportes/ingresos.csv",
        views.report_csv,
        {"report_type": "ingresos"},
        name="report_income_csv",
    ),
    path(
        "reportes/pagos/",
        views.report_html,
        {"report_type": "pagos"},
        name="report_payments",
    ),
    path(
        "reportes/pagos.csv",
        views.report_csv,
        {"report_type": "pagos"},
        name="report_payments_csv",
    ),
    path(
        "reportes/liquidaciones/",
        views.report_html,
        {"report_type": "liquidaciones"},
        name="report_settlements",
    ),
    path(
        "reportes/liquidaciones.csv",
        views.report_csv,
        {"report_type": "liquidaciones"},
        name="report_settlements_csv",
    ),
    path(
        "reportes/documentos/",
        views.report_html,
        {"report_type": "documentos"},
        name="report_documents",
    ),
    path(
        "reportes/documentos.csv",
        views.report_csv,
        {"report_type": "documentos"},
        name="report_documents_csv",
    ),
    path(
        "reportes/trazabilidad/",
        views.report_html,
        {"report_type": "trazabilidad"},
        name="report_traceability",
    ),
    path(
        "reportes/trazabilidad.csv",
        views.report_csv,
        {"report_type": "trazabilidad"},
        name="report_traceability_csv",
    ),
]
