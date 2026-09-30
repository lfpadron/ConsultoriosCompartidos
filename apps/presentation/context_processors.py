"""Template context processors."""

from typing import Any

from django.http import HttpRequest

from apps.core.permissions import get_user_screen_access_map
from apps.identity.models import ScreenAccessLevel
from apps.presentation.navigation import NAVIGATION_ITEMS


def navigation(request: HttpRequest) -> dict[str, Any]:
    query_params = request.GET.copy()
    query_params.pop("page", None)
    access_map = get_user_screen_access_map(request.user)
    navigation_items = [
        {
            **item,
            "access_level": access_map.get(
                item["screen_key"],
                ScreenAccessLevel.NONE,
            ),
        }
        for item in NAVIGATION_ITEMS
    ]
    current_access = getattr(
        request,
        "current_screen_access",
        ScreenAccessLevel.NONE,
    )
    return {
        "navigation_items": navigation_items,
        "screen_access_map": access_map,
        "current_screen_access": current_access,
        "current_screen_can_edit": current_access == ScreenAccessLevel.EDIT,
        "pagination_querystring": query_params.urlencode(),
    }
