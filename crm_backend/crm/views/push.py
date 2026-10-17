from django.conf import settings
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import PushSubscription
from ..permissions import IsCRMUser, crm_profile
from ..push import is_configured, send_push


class PushConfigView(APIView):
    """GET: the VAPID public key the browser needs to subscribe."""

    permission_classes = [IsCRMUser]

    def get(self, request):
        return Response({"configured": is_configured(), "public_key": settings.VAPID_PUBLIC_KEY})


class PushSubscribeView(APIView):
    """POST: register this device (a browser PushSubscription JSON). DELETE: unregister it ({endpoint})."""

    permission_classes = [IsCRMUser]

    def post(self, request):
        endpoint = request.data.get("endpoint") or ""
        keys = request.data.get("keys") or {}
        if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise ValidationError("Invalid push subscription.")
        # An endpoint belongs to one browser profile: if another user signed in there before, it moves to this user.
        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={"user": crm_profile(request), "p256dh": keys["p256dh"], "auth": keys["auth"],
                      "user_agent": request.headers.get("User-Agent", "")[:300]},
        )
        return Response({"subscribed": True}, status=201)

    def delete(self, request):
        PushSubscription.objects.filter(user=crm_profile(request), endpoint=request.data.get("endpoint") or "").delete()
        return Response(status=204)


class PushTestView(APIView):
    """POST: send a test notification to the current user's devices."""

    permission_classes = [IsCRMUser]

    def post(self, request):
        return Response({"result": send_push([crm_profile(request)], "Rarity CRM", "Notifications are working on this device.", "/settings", "test")})
