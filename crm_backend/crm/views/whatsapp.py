import json
import logging

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django_q.tasks import async_task
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .. import meta_leads, whatsapp
from ..models import WhatsAppConnection
from ..throttles import MetaWebhookThrottle

log = logging.getLogger(__name__)


class WhatsAppWebhookView(APIView):
    """Meta -> CRM. Public URL; the X-Hub-Signature-256 HMAC is the authentication."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MetaWebhookThrottle]

    def get(self, request):
        token = settings.META_WA_VERIFY_TOKEN
        if token and request.GET.get("hub.mode") == "subscribe" and request.GET.get("hub.verify_token") == token:
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponse("forbidden", status=403)

    def post(self, request):
        body = request.body
        if not meta_leads.verify_signature(body, request.headers.get("X-Hub-Signature-256", "")):
            return HttpResponse("invalid signature", status=403)
        try:
            payload = json.loads(body)
        except ValueError:
            return HttpResponse("ok")  # nothing to retry
        queued = 0
        if payload.get("object") == "whatsapp_business_account":
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    if change.get("field") in whatsapp.HANDLERS:
                        async_task("crm.tasks.process_whatsapp_change", change["field"], change.get("value") or {})
                        queued += 1
        log.info("whatsapp webhook: queued %d change(s)", queued)
        return HttpResponse("ok")


@staff_member_required
def connect_page(request):
    return render(request, "crm/whatsapp_connect.html", {
        "app_id": settings.META_APP_ID, "config_id": settings.META_WA_ES_CONFIG_ID, "graph_version": settings.META_GRAPH_VERSION,
        "connection": WhatsAppConnection.objects.first(),
    })


@staff_member_required
@require_POST
def connect_complete(request):
    try:
        data = json.loads(request.body)
        result = whatsapp.complete_onboarding(str(data["code"]), str(data["waba_id"]), str(data["phone_number_id"]))
    except (KeyError, ValueError) as e:
        return JsonResponse({"ok": False, "error": str(e) or "Missing signup details"}, status=400)
    return JsonResponse({"ok": True, **result})
