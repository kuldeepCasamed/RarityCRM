import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


def crm_profile(request):
    user = request.user
    if not user or not user.is_authenticated:
        return None
    profile = getattr(user, "crm_profile", None)
    return profile if profile and profile.is_active else None


class IsCRMUser(BasePermission):
    def has_permission(self, request, view):
        return crm_profile(request) is not None


class IsCRMManager(BasePermission):
    def has_permission(self, request, view):
        p = crm_profile(request)
        return bool(p and p.is_manager)


class HasIntakeKey(BasePermission):
    """Public intake is only callable by the marketing site's server (shared secret)."""

    def has_permission(self, request, view):
        expected = settings.CRM_INTAKE_API_KEY
        given = request.headers.get("X-Rarity-Key", "")
        return bool(expected) and hmac.compare_digest(expected, given)
