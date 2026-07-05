"""Shared validators."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_sha256(value: str) -> None:
    if len(value) != 64:
        raise ValidationError(_("El hash SHA-256 debe tener 64 caracteres."))

    allowed = set("0123456789abcdefABCDEF")
    if any(character not in allowed for character in value):
        raise ValidationError(_("El hash SHA-256 debe estar en hexadecimal."))
