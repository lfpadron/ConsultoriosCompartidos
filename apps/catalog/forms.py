"""Forms for catalog CRUD screens."""

from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    Equipment,
    OwnerProfile,
    Specialty,
    TenantDoctorProfile,
)
from apps.core.form_utils import selected_model_pk, style_form_fields


def set_model_queryset(field: forms.Field, queryset: QuerySet[Any]) -> None:
    if isinstance(field, forms.ModelChoiceField | forms.ModelMultipleChoiceField):
        field.queryset = queryset


class BootstrapModelForm(forms.ModelForm):
    checkbox_fields = {"is_active"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        style_form_fields(self.fields)

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError(_("El nombre es obligatorio."))
        return name


def room_queryset_for_filters(data: Any = None) -> QuerySet[Any]:
    queryset = ConsultingRoom.objects.filter(is_deleted=False).select_related(
        "clinic",
        "owner",
        "owner__user",
    )
    clinic_pk = selected_model_pk(data, "clinic")
    owner_pk = selected_model_pk(data, "owner")
    if clinic_pk:
        queryset = queryset.filter(clinic_id=clinic_pk)
    if owner_pk:
        queryset = queryset.filter(owner_id=owner_pk)
    return queryset.order_by("clinic__name", "owner__display_name", "name")


class RoomCatalogFilterForm(forms.Form):
    q = forms.CharField(label="Buscar", required=False)
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )
    owner = forms.ModelChoiceField(
        label="Médico propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )
    room = forms.ModelChoiceField(
        label="Consultorio",
        queryset=ConsultingRoom.objects.none(),
        required=False,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_model_queryset(
            self.fields["clinic"],
            Clinic.objects.filter(is_deleted=False).order_by("name"),
        )
        set_model_queryset(
            self.fields["owner"],
            OwnerProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )
        set_model_queryset(
            self.fields["room"],
            room_queryset_for_filters(self.data if self.is_bound else None),
        )
        style_form_fields(self.fields)


class ClinicForm(BootstrapModelForm):
    class Meta:
        model = Clinic
        fields = (
            "name",
            "address",
            "phone",
            "email",
            "schedule_text",
            "timezone",
            "hour_format",
            "is_active",
        )
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "schedule_text": forms.Textarea(attrs={"rows": 3}),
        }


class SpecialtyForm(BootstrapModelForm):
    class Meta:
        model = Specialty
        fields = ("name", "description", "is_active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class EquipmentForm(BootstrapModelForm):
    class Meta:
        model = Equipment
        fields = ("name", "description", "is_active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class OwnerProfileForm(BootstrapModelForm):
    class Meta:
        model = OwnerProfile
        fields = (
            "user",
            "display_name",
            "professional_license",
            "tax_id",
            "phone",
            "notes",
            "is_active",
        )
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        queryset = user_model.objects.filter(is_active=True)

        if self.instance.pk:
            queryset = queryset.filter(
                Q(owner_profile__isnull=True) | Q(pk=self.instance.user_id)
            )
        else:
            queryset = queryset.filter(owner_profile__isnull=True)

        set_model_queryset(self.fields["user"], queryset.order_by("email"))


class TenantDoctorProfileForm(BootstrapModelForm):
    class Meta:
        model = TenantDoctorProfile
        fields = (
            "user",
            "display_name",
            "specialties",
            "professional_license",
            "tax_id",
            "phone",
            "status",
            "notes",
            "is_active",
        )
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        queryset = user_model.objects.filter(is_active=True)

        if self.instance.pk:
            queryset = queryset.filter(
                Q(tenant_doctor_profile__isnull=True) | Q(pk=self.instance.user_id)
            )
        else:
            queryset = queryset.filter(tenant_doctor_profile__isnull=True)

        set_model_queryset(self.fields["user"], queryset.order_by("email"))
        set_model_queryset(
            self.fields["specialties"],
            Specialty.objects.filter(is_deleted=False).order_by("name"),
        )


class ConsultingRoomForm(BootstrapModelForm):
    class Meta:
        model = ConsultingRoom
        fields = (
            "clinic",
            "owner",
            "name",
            "description",
            "floor",
            "capacity",
            "allowed_specialties",
            "excluded_specialties",
            "equipment",
            "regulations_text",
            "status",
            "is_active",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "regulations_text": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_model_queryset(
            self.fields["clinic"],
            Clinic.objects.filter(is_deleted=False).order_by("name"),
        )
        set_model_queryset(
            self.fields["owner"],
            OwnerProfile.objects.filter(is_deleted=False).order_by("display_name"),
        )
        set_model_queryset(
            self.fields["allowed_specialties"],
            Specialty.objects.filter(is_deleted=False).order_by("name"),
        )
        set_model_queryset(
            self.fields["excluded_specialties"],
            Specialty.objects.filter(is_deleted=False).order_by("name"),
        )
        set_model_queryset(
            self.fields["equipment"],
            Equipment.objects.filter(is_deleted=False).order_by("name"),
        )

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        allowed = set(cleaned_data.get("allowed_specialties") or [])
        excluded = set(cleaned_data.get("excluded_specialties") or [])

        if allowed.intersection(excluded):
            msg = _("Las especialidades permitidas y excluidas no deben traslaparse.")
            self.add_error("excluded_specialties", msg)

        return cleaned_data
