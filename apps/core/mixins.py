"""Shared model mixins."""

from django.db import models


class ActiveQuerySet(models.QuerySet):
    def active(self) -> models.QuerySet:
        return self.filter(is_active=True, is_deleted=False)
