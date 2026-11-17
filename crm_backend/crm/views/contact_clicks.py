import json
import logging
import re
from datetime import timedelta
from urllib.parse import urlparse

from django.core.cache import cache
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import ContactClick, LeadSource
from ..permissions import IsCRMUser
from .roi import parse_range

log = logging.getLogger(__name__)

ALLOWED_ORIGINS = {"https://www.raritydental.com", "https://raritydental.com"}
RATE_LIMIT_PER_MIN = 30
DEDUPE_SECONDS = 30

# field -> max length (the model's own limits)
TEXT_FIELDS = {
    "button_location": 50,
    "utm_source": 100,
    "utm_medium": 100,
    "utm_campaign": 200,
    "utm_term": 200,
    "utm_content": 200,
    "landing_page": 300,
    "session_id": 64,
}
PII = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|(?:\+?\d[\s().-]?){8,}")


def _cors(request, response):
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        response["Access-Control-Max-Age"] = "86400"
        response["Vary"] = "Origin"
    return response


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")) or "unknown"


def source_for(utm_source):
    s = (utm_source or "").lower()
    if s == "google":
        return LeadSource.GOOGLE_ADS
    if s in ("facebook", "instagram", "meta", "fb", "ig"):
        return LeadSource.META_ADS
    return LeadSource.WEBSITE


def clean(data):
    """Whitelist + truncate. Returns None if the click is invalid. Never stores anything that looks like PII."""
    if not isinstance(data, dict) or data.get("channel") not in ContactClick.Channel.values:
        return None
    out = {"channel": data["channel"]}
    path = data.get("page_path")
    path = urlparse(path).path if isinstance(path, str) else ""
    out["page_path"] = path[:300]
    for field, limit in TEXT_FIELDS.items():
        v = data.get(field)
        v = v.strip()[:limit] if isinstance(v, str) else ""
        if PII.search(v):
            v = ""
        out[field] = v or None
    # the landing page is a path: drop any query string
    if out["landing_page"]:
        out["landing_page"] = urlparse(out["landing_page"]).path[:300] or None
    out["source"] = source_for(out["utm_source"])
    return out


@csrf_exempt
def contact_click(request):
    """Public, always answers 204 so the page never waits. Accepts JSON or text/plain JSON (sendBeacon)."""
    resp = _cors(request, HttpResponse(status=204))
    if request.method != "POST":
        return resp
    try:
        key = f"cc-rate:{_client_ip(request)}"
        cache.add(key, 0, 60)
        if cache.incr(key) > RATE_LIMIT_PER_MIN:
            return resp
        try:
            data = json.loads(request.body[:4096] or b"{}")
        except ValueError:
            data = None
        row = clean(data)
        if row is None:
            log.warning("contact-click rejected: invalid payload")
            return resp
        if row["session_id"]:
            dedupe = f"cc-dup:{row['session_id']}:{row['channel']}:{row['page_path']}"
            if not cache.add(dedupe, 1, DEDUPE_SECONDS):
                return resp
        ContactClick.objects.create(**row)
    except Exception:  # never let a tracking call break or slow the page
        log.exception("contact-click failed")
    return resp


class ContactClickReportView(APIView):
    """Click counts per day x channel x source x campaign / ad set / ad."""

    permission_classes = [IsCRMUser]

    def get(self, request):
        start, end = parse_range(request.query_params)
        qs = ContactClick.objects.filter(created_at__date__range=(start, end))
        rows = (
            qs.annotate(date=TruncDate("created_at"))
            .values("date", "channel", "source", "utm_campaign", "utm_term", "utm_content")
            .annotate(clicks=Count("id"))
            .order_by("-date", "-clicks")
        )
        return Response({
            "from": start,
            "to": end,
            "results": [{**r, "utm_campaign": r["utm_campaign"] or "", "utm_term": r["utm_term"] or "", "utm_content": r["utm_content"] or ""} for r in rows],
        })
