"""WhatsApp (Cloud API, coexistence with the WhatsApp Business app) -> CRM.

Read-only: we receive messages, never send. Never log message text, phone numbers or tokens."""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import meta_leads, services
from .models import Lead, LeadSource, LeadStatus, WhatsAppConnection, WhatsAppEvent, WhatsAppMessage

log = logging.getLogger(__name__)

CLOSED = meta_leads.CLOSED
REPEAT_WINDOW_DAYS = meta_leads.REPEAT_WINDOW_DAYS
_ad_cache: dict[str, dict] = {}


def connection() -> tuple[str, str]:
    """(waba_id, phone_number_id): the onboarded row wins, env is the fallback."""
    row = WhatsAppConnection.objects.first()
    if row:
        return row.waba_id, row.phone_number_id
    return settings.META_WABA_ID, settings.META_WA_PHONE_NUMBER_ID


def is_our_number(metadata: dict) -> bool:
    """Only handle events for the onboarded number (never another number, e.g. the Haptik one)."""
    _, pnid = connection()
    got = str((metadata or {}).get("phone_number_id") or "")
    return bool(pnid) and got == pnid if got else bool(pnid)


def _ts(value) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError):
        return timezone.now()


def _text(msg: dict) -> str | None:
    if msg.get("type") == "text":
        return (msg.get("text") or {}).get("body")
    for kind in ("button", "interactive"):
        if kind in msg:
            return str(msg[kind].get("text") or msg[kind].get("title") or "")[:1000] or None
    return None


def _phone(wa_id: str) -> str:
    """Digits only: newer API versions may send an opaque user id instead of a number, which we can't use as a phone."""
    digits = str(wa_id or "").lstrip("+")
    return services.normalize_phone("+" + digits) if digits.isdigit() else ""


def _store(*, phone, msg, direction, lead=None, is_history=False) -> tuple[WhatsAppMessage | None, bool]:
    """Idempotent on the WhatsApp message id."""
    try:
        with transaction.atomic():
            row = WhatsAppMessage.objects.create(
                lead=lead, phone=phone, wa_message_id=str(msg["id"]), direction=direction, type=(msg.get("type") or "text")[:30],
                text=_text(msg), timestamp=_ts(msg.get("timestamp")), is_history=is_history,
            )
        return row, True
    except IntegrityError:
        return None, False


def open_lead_for(phone: str):
    return (Lead.objects.alive().filter(phone=phone, created_at__gte=timezone.now() - timedelta(days=REPEAT_WINDOW_DAYS))
            .exclude(status__in=CLOSED).order_by("-created_at").first())


def any_lead_for(phone: str):
    return Lead.objects.alive().filter(phone=phone).order_by("-created_at").first()


def resolve_ad(ad_id: str) -> dict:
    """ad id -> names via Graph (needs ads_read). Cached; failures just leave names blank."""
    if not ad_id:
        return {}
    if ad_id in _ad_cache:
        return _ad_cache[ad_id]
    try:
        data = meta_leads.graph_get(ad_id, {"fields": "name,adset{name},campaign{name}"})
    except requests.RequestException:
        return {}
    out = {"ad": data.get("name", ""), "adset": (data.get("adset") or {}).get("name", ""), "campaign": (data.get("campaign") or {}).get("name", "")}
    _ad_cache[ad_id] = out
    return out


def process_inbound(value: dict) -> list[int]:
    """`messages` field. Returns ids of leads created (the caller alerts the front desk)."""
    created: list[int] = []
    if not is_our_number(value.get("metadata")):
        return created
    contacts = {c.get("wa_id"): ((c.get("profile") or {}).get("name") or "") for c in value.get("contacts", [])}
    for msg in value.get("messages", []):  # `statuses` are ignored on purpose
        wa_id = msg.get("from") or ""
        phone = _phone(wa_id)
        if not phone:  # v24+ payloads: the number can be in contacts[].wa_id while `from` is a user id
            for c in value.get("contacts", []):
                if _phone(c.get("wa_id")):
                    wa_id, phone = c["wa_id"], _phone(c["wa_id"])
                    break
        if not phone or not msg.get("id"):
            if msg.get("id"):
                log.warning("whatsapp message %s skipped: no usable phone number in payload", msg["id"])
            continue
        if WhatsAppMessage.objects.filter(wa_message_id=str(msg["id"])).exists():
            continue  # duplicate delivery
        when = _ts(msg.get("timestamp"))
        referral = msg.get("referral") or {}
        lead = open_lead_for(phone)
        made = False
        if not lead:
            lead = _create_lead(phone, contacts.get(wa_id, ""), msg, referral, when)
            made = True
        row, new = _store(phone=phone, msg=msg, direction="in", lead=lead)
        if not new:
            continue
        if made:
            created.append(lead.id)
        else:
            Lead.objects.filter(pk=lead.pk).update(last_whatsapp_at=when, updated_at=timezone.now())
    return created


def _create_lead(phone, name, msg, referral, when) -> Lead:
    from_ad = referral.get("source_type") == "ad"
    fields = dict(
        name=(name or "WhatsApp enquiry")[:200], phone=phone,
        source=LeadSource.META_WHATSAPP if from_ad else LeadSource.WHATSAPP, status=LeadStatus.NEW,
        first_message=(_text(msg) or "")[:500], last_whatsapp_at=when,
        chief_complaint=(_text(msg) or "")[:500],
    )
    if from_ad:
        ad_id = str(referral.get("source_id") or "")
        names = resolve_ad(ad_id)
        fields.update(
            meta_ad_id=ad_id, meta_ctwa_clid=(referral.get("ctwa_clid") or "")[:300], meta_ad_headline=(referral.get("headline") or "")[:255],
            meta_ad_name=names.get("ad", "")[:255], meta_adset_name=names.get("adset", "")[:255], meta_campaign_name=names.get("campaign", "")[:255],
            utm_source="meta", utm_medium="whatsapp_ad",
            utm_campaign=names.get("campaign", "")[:200], utm_term=names.get("adset", "")[:200], utm_content=names.get("ad", "")[:200],
            meta_event_source_url=(referral.get("source_url") or "")[:1000],
        )
    lead, _ = services.create_lead(force=True, **fields)
    return lead


def process_echo(value: dict) -> int:
    """`smb_message_echoes`: messages staff send from the phone app."""
    if not is_our_number(value.get("metadata")):
        return 0
    n = 0
    for msg in value.get("message_echoes", []):
        phone = _phone(msg.get("to") or msg.get("recipient_id"))
        if not phone or not msg.get("id"):
            continue
        lead = any_lead_for(phone)
        row, new = _store(phone=phone, msg=msg, direction="out", lead=lead)
        if not new:
            continue
        n += 1
        if lead and not lead.first_response_at:
            first_in = lead.whatsapp_messages.filter(direction="in").order_by("timestamp").first()
            if first_in and row.timestamp >= first_in.timestamp:
                Lead.objects.filter(pk=lead.pk).update(first_response_at=row.timestamp)
    return n


def process_history(value: dict) -> int:
    """`history`: past chats synced after onboarding. Log only: no leads, no alerts."""
    if not is_our_number(value.get("metadata")):
        return 0
    n = 0
    for chunk in value.get("history", []):
        for thread in chunk.get("threads", []):
            customer = str(thread.get("id") or "")
            phone = _phone(customer)
            if not phone:
                continue
            lead = any_lead_for(phone)
            for msg in thread.get("messages", []):
                if not msg.get("id"):
                    continue
                direction = "in" if str(msg.get("from") or "") == customer else "out"
                _, new = _store(phone=phone, msg=msg, direction=direction, lead=lead, is_history=True)
                n += new
    return n


def process_state_sync(value: dict) -> int:
    """`smb_app_state_sync`: contact names. Only fills a blank/placeholder name on an existing lead."""
    if not is_our_number(value.get("metadata")):
        return 0
    n = 0
    for item in value.get("state_sync", []):
        contact = item.get("contact") or {}
        phone = _phone(contact.get("phone_number"))
        name = (contact.get("full_name") or contact.get("first_name") or "").strip()
        if not (phone and name):
            continue
        n += Lead.objects.alive().filter(phone=phone, name__in=["WhatsApp enquiry", "Meta lead"]).update(name=name[:200])
    return n


HANDLERS = {"messages": process_inbound, "smb_message_echoes": process_echo, "history": process_history, "smb_app_state_sync": process_state_sync}


def handle_change(field: str, value: dict):
    """Run one webhook change. For `messages`, returns the created lead ids."""
    WhatsAppEvent.objects.create(field=field[:40])
    handler = HANDLERS.get(field)
    return handler(value) if handler else None


# ---- onboarding (Embedded Signup, coexistence) ----
def _graph(method, path, token, **kw):
    res = requests.request(method, f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}/{path.lstrip('/')}",
                           headers={"Authorization": f"Bearer {token}"}, timeout=20, **kw)
    try:
        body = res.json()
    except ValueError:
        body = {}
    return res.ok, body


def exchange_code(code: str) -> str:
    res = requests.get(f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}/oauth/access_token", timeout=20,
                       params={"client_id": settings.META_APP_ID, "client_secret": settings.META_APP_SECRET, "code": code})
    if not res.ok:
        log.error("WhatsApp code exchange failed: %s", res.text[:200])
        raise ValueError("Meta rejected the signup code (it expires in minutes: run the signup again).")
    return res.json()["access_token"]


def complete_onboarding(code: str, waba_id: str, phone_number_id: str) -> dict:
    """Exchange the signup code, subscribe the app to the WABA's webhooks, request the one-time coexistence data sync.
    The exchanged token is used here only and never stored."""
    token = exchange_code(code)
    result: dict = {"waba_id": waba_id, "phone_number_id": phone_number_id}
    ok, body = _graph("GET", phone_number_id, token, params={"fields": "display_phone_number,verified_name"})
    result["display_number"] = body.get("display_phone_number", "")
    ok, body = _graph("POST", f"{waba_id}/subscribed_apps", token)
    result["subscribed"] = bool(ok and body.get("success"))
    for sync_type in ("smb_app_state_sync", "history"):
        ok, body = _graph("POST", f"{phone_number_id}/smb_app_data", token, json={"messaging_product": "whatsapp", "sync_type": sync_type})
        result[f"sync_{sync_type}"] = "requested" if ok else (body.get("error", {}).get("message", "failed")[:200])
    WhatsAppConnection.objects.all().delete()
    WhatsAppConnection.objects.create(waba_id=waba_id, phone_number_id=phone_number_id, display_number=result["display_number"], sync_result=result)
    return result
