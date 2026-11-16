import json
import logging

from django.conf import settings
from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .. import meta_leads
from ..tasks import enqueue_meta_lead
from ..throttles import MetaWebhookThrottle

log = logging.getLogger(__name__)


class LeadgenWebhookView(APIView):
    """Meta -> CRM. Public URL; the X-Hub-Signature-256 HMAC is the authentication."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MetaWebhookThrottle]

    def get(self, request):  # verification handshake
        token = settings.META_LEADS_VERIFY_TOKEN
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
            return HttpResponse("bad json", status=400)
        ids = []
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                leadgen_id = (change.get("value") or {}).get("leadgen_id")
                if change.get("field") == "leadgen" and leadgen_id:
                    ids.append(str(leadgen_id))
        for leadgen_id in dict.fromkeys(ids):  # fetching is idempotent, but don't queue the same id twice per delivery
            enqueue_meta_lead(leadgen_id)
        log.info("meta leadgen webhook: queued %d lead(s)", len(ids))
        return HttpResponse("ok")  # return fast: Meta retries on slow responses


