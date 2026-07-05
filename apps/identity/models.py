"""Identity models."""

import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.identity.managers import CustomUserManager


class UserRole(models.TextChoices):
    SUPERADMIN = "superadmin", _("Superadministrador")
    ADMIN = "admin", _("Administrador")
    OPERATOR = "operator", _("Operador")
    RECEPTIONIST = "receptionist", _("Recepcionista")
    OWNER = "owner", _("Dueño")
    TENANT_DOCTOR = "tenant_doctor", _("Médico Arrendatario")
    AUDITOR = "auditor", _("Auditor")


class CustomUser(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("correo electrónico"), unique=True)
    first_name = models.CharField(_("nombre"), max_length=150)
    last_name = models.CharField(_("apellidos"), max_length=150)
    role = models.CharField(
        _("rol"),
        max_length=32,
        choices=UserRole.choices,
        default=UserRole.TENANT_DOCTOR,
    )
    is_active = models.BooleanField(_("activo"), default=True)
    is_staff = models.BooleanField(_("staff"), default=False)
    date_joined = models.DateTimeField(_("fecha de registro"), default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")
        ordering = ("email",)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def clean(self) -> None:
        super().clean()
        self.email = type(self).objects.normalize_email(self.email).lower()

    def __str__(self) -> str:
        return self.email
