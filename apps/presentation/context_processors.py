"""Template context processors."""

from typing import Any

from django.http import HttpRequest

from apps.presentation.navigation import NAVIGATION_ITEMS


def navigation(request: HttpRequest) -> dict[str, Any]:
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return {
        "navigation_items": NAVIGATION_ITEMS,
        "pagination_querystring": query_params.urlencode(),
    }
