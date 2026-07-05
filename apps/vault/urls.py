"""Document vault URL patterns."""

from django.urls import path

from apps.vault import views

urlpatterns = [
    path("documentos/", views.DocumentListView.as_view(), name="documents"),
    path(
        "documentos/subir/",
        views.DocumentUploadView.as_view(),
        name="document_upload",
    ),
    path(
        "documentos/<uuid:pk>/",
        views.DocumentDetailView.as_view(),
        name="document_detail",
    ),
    path(
        "documentos/<uuid:pk>/archivo/",
        views.DocumentFileView.as_view(),
        name="document_file",
    ),
    path(
        "documentos/<uuid:pk>/revision/",
        views.DocumentInReviewView.as_view(),
        name="document_in_review",
    ),
    path(
        "documentos/<uuid:pk>/aprobar/",
        views.DocumentApproveView.as_view(),
        name="document_approve",
    ),
    path(
        "documentos/<uuid:pk>/rechazar/",
        views.DocumentRejectView.as_view(),
        name="document_reject",
    ),
    path(
        "documentos/<uuid:pk>/cancelar/",
        views.DocumentCancelView.as_view(),
        name="document_cancel",
    ),
]
