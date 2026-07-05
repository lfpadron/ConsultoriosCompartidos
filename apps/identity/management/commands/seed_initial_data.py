"""Seed initial operational data."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.identity.models import UserRole


class Command(BaseCommand):
    help = "Crea datos iniciales de identidad sin credenciales hardcodeadas."

    required_env_vars = (
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        "ADMIN_FIRST_NAME",
        "ADMIN_LAST_NAME",
    )

    def handle(self, *args: object, **options: object) -> None:
        missing = [name for name in self.required_env_vars if not self._env(name)]
        if missing:
            joined = ", ".join(missing)
            msg = f"Variables de entorno requeridas faltantes: {joined}"
            raise CommandError(msg)

        email = self._env("ADMIN_EMAIL")
        password = self._env("ADMIN_PASSWORD")
        first_name = self._env("ADMIN_FIRST_NAME")
        last_name = self._env("ADMIN_LAST_NAME")

        user_model = get_user_model()
        existing_user = user_model.objects.filter(email=email.lower()).first()
        if existing_user:
            self.stdout.write(
                self.style.WARNING("El superadmin semilla ya existe; no se modificó.")
            )
            return

        user_model.objects.create_superuser(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.SUPERADMIN,
        )
        self.stdout.write(self.style.SUCCESS("Superadmin semilla creado."))

    @staticmethod
    def _env(name: str) -> str:
        from os import environ

        return environ.get(name, "").strip()
