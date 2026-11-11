"""Meta Conversions API: server-side events matching the website's browser Pixel events.

Only contact match keys and the event name are sent. Never treatment or health details."""
import hashlib
import logging
import re
import time

import requests
from django.conf import settings

from .models import LeadStatus

log = logging.getLogger(__name__)

# lead status -> (CAPI event name, event_id builder). Each is sent once per lead.
STATUS_EVENTS = {
    LeadStatus.CONSULT_BOOKED: ("Schedule", lambda lead: f"schedule-{lead.id}"),
    LeadStatus.TREATMENT_BOOKED: ("TreatmentBooked", lambda lead: f"treatment-{lead.id}"),
}


def enabled() -> bool:
    return bool(settings.META_PIXEL_ID and settings.META_CAPI_TOKEN)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def build_user_data(lead) -> dict:
    first, _, last = (lead.name or "").strip().partition(" ")
    phone = re.sub(r"\D", "", lead.phone or "")  # digits with country code, e.g. 918796319927
    hashed = {
        "em": _norm(lead.email),
        "ph": phone,
        "fn": _norm(first),
        "ln": _norm(last),
        "external_id": str(lead.id),
    }
    user = {k: [_sha(v)] for k, v in hashed.items() if v}
    raw = {  # sent unhashed, as Meta requires
        "fbp": lead.meta_fbp,
        "fbc": lead.meta_fbc,
        "client_ip_address": lead.meta_client_ip,
        "client_user_agent": lead.meta_client_user_agent,
    }
    user.update({k: v for k, v in raw.items() if v})
    return user


def send_capi_event(lead, event_name: str, event_id: str) -> str:
    """POST one event to Meta. Returns a status string for logs; raises on failure so the task retries."""
    if not enabled():
        return "skipped: Meta CAPI not configured"
    event = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": event_id,
        "action_source": "website",
        "event_source_url": lead.meta_event_source_url or lead.landing_url or "https://www.raritydental.com/",
        "user_data": build_user_data(lead),
    }
    body = {"data": [event], "access_token": settings.META_CAPI_TOKEN}  # token in the body, so it never shows in logged URLs
    if settings.META_TEST_EVENT_CODE:
        body["test_event_code"] = settings.META_TEST_EVENT_CODE
    res = requests.post(
        f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}/{settings.META_PIXEL_ID}/events",
        json=body,
        timeout=15,
    )
    if not res.ok:
        log.error("Meta CAPI %s for lead %s failed: %s %s", event_name, lead.id, res.status_code, res.text[:300])
        res.raise_for_status()
    return f"sent {event_name} ({event_id})"
