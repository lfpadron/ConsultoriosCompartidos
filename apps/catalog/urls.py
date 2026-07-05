"""Catalog URL patterns."""

from django.urls import path

from apps.catalog import views

urlpatterns = [
    path("clinicas/", views.ClinicListView.as_view(), name="clinics"),
    path("clinicas/nueva/", views.ClinicCreateView.as_view(), name="clinic_create"),
    path("clinicas/<uuid:pk>/", views.ClinicDetailView.as_view(), name="clinic_detail"),
    path(
        "clinicas/<uuid:pk>/editar/",
        views.ClinicUpdateView.as_view(),
        name="clinic_update",
    ),
    path(
        "clinicas/<uuid:pk>/desactivar/",
        views.ClinicDeactivateView.as_view(),
        name="clinic_deactivate",
    ),
    path("consultorios/", views.RoomListView.as_view(), name="rooms"),
    path(
        "consultorios/nuevo/",
        views.RoomCreateView.as_view(),
        name="room_create",
    ),
    path(
        "consultorios/<uuid:pk>/",
        views.RoomDetailView.as_view(),
        name="room_detail",
    ),
    path(
        "consultorios/<uuid:pk>/editar/",
        views.RoomUpdateView.as_view(),
        name="room_update",
    ),
    path(
        "consultorios/<uuid:pk>/desactivar/",
        views.RoomDeactivateView.as_view(),
        name="room_deactivate",
    ),
    path(
        "especialidades/",
        views.SpecialtyListView.as_view(),
        name="specialties",
    ),
    path(
        "especialidades/nueva/",
        views.SpecialtyCreateView.as_view(),
        name="specialty_create",
    ),
    path(
        "especialidades/<uuid:pk>/",
        views.SpecialtyDetailView.as_view(),
        name="specialty_detail",
    ),
    path(
        "especialidades/<uuid:pk>/editar/",
        views.SpecialtyUpdateView.as_view(),
        name="specialty_update",
    ),
    path(
        "especialidades/<uuid:pk>/desactivar/",
        views.SpecialtyDeactivateView.as_view(),
        name="specialty_deactivate",
    ),
    path("equipamiento/", views.EquipmentListView.as_view(), name="equipment"),
    path(
        "equipamiento/nuevo/",
        views.EquipmentCreateView.as_view(),
        name="equipment_create",
    ),
    path(
        "equipamiento/<uuid:pk>/",
        views.EquipmentDetailView.as_view(),
        name="equipment_detail",
    ),
    path(
        "equipamiento/<uuid:pk>/editar/",
        views.EquipmentUpdateView.as_view(),
        name="equipment_update",
    ),
    path(
        "equipamiento/<uuid:pk>/desactivar/",
        views.EquipmentDeactivateView.as_view(),
        name="equipment_deactivate",
    ),
    path("propietarios/", views.OwnerListView.as_view(), name="owners"),
    path(
        "propietarios/nuevo/",
        views.OwnerCreateView.as_view(),
        name="owner_create",
    ),
    path(
        "propietarios/<uuid:pk>/",
        views.OwnerDetailView.as_view(),
        name="owner_detail",
    ),
    path(
        "propietarios/<uuid:pk>/editar/",
        views.OwnerUpdateView.as_view(),
        name="owner_update",
    ),
    path(
        "propietarios/<uuid:pk>/desactivar/",
        views.OwnerDeactivateView.as_view(),
        name="owner_deactivate",
    ),
    path(
        "medicos-arrendatarios/",
        views.TenantDoctorListView.as_view(),
        name="tenant_doctors",
    ),
    path(
        "medicos-arrendatarios/nuevo/",
        views.TenantDoctorCreateView.as_view(),
        name="tenant_doctor_create",
    ),
    path(
        "medicos-arrendatarios/<uuid:pk>/",
        views.TenantDoctorDetailView.as_view(),
        name="tenant_doctor_detail",
    ),
    path(
        "medicos-arrendatarios/<uuid:pk>/editar/",
        views.TenantDoctorUpdateView.as_view(),
        name="tenant_doctor_update",
    ),
    path(
        "medicos-arrendatarios/<uuid:pk>/desactivar/",
        views.TenantDoctorDeactivateView.as_view(),
        name="tenant_doctor_deactivate",
    ),
]
