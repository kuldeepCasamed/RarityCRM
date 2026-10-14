"""Two-way email with leads over Resend: send, receive replies, track delivery.

Reply routing: every outgoing email carries Reply-To  t-<thread token>@EMAIL_REPLY_DOMAIN. When the lead replies, Resend
receives it (MX on that subdomain) and calls our webhook; the token in the recipient address identifies the thread
and therefore the lead. Fallbacks: the sender's address matching a lead, otherwise the 'Unmatched' inbox.
Nothing here depends on the In-Reply-To header being present on inbound mail.
"""
import base64
import hashlib
import hmac
import logging
import re
import time
from datetime import timedelta
from email.utils import parseaddr, formataddr
from html import escape
from html.parser import HTMLParser

import requests
from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    ContactLog, EmailMessage, EmailSequence, EmailThread, Lead, LeadStatus, Note, Priority, SequenceEnrollment, Task, WebhookEvent,
)
from django.db.models import Q

log = logging.getLogger(__name__)

THREAD_ADDR_RE = re.compile(r"\bt-([0-9a-f]{16})@", re.I)
MERGE_FIELDS = {
    "first_name": "Lead's first name", "name": "Lead's full name", "treatment": "First treatment they asked about",
    "sender_name": "Name of the person sending", "clinic_name": "Clinic name", "clinic_phone": "Clinic phone",
    "clinic_website": "Clinic website",
}


class EmailError(Exception):
    """A problem the user should see (shown as-is in the UI)."""


# ---------------------------------------------------------------- sender identity
def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""


def default_from_address() -> str:
    return settings.EMAIL_DEFAULT_FROM.strip() or parseaddr(settings.CRM_FROM_EMAIL)[1] or "onboarding@raritydental.com"


def sender_identity(profile) -> dict:
    """Who the email appears to come from. A person's own address is only used when its domain is verified in Resend,
    otherwise we send from the default address but keep their name. Replies always return to the CRM thread address."""
    name = (profile.user.get_full_name() if profile else "") or (profile.user.username if profile else "") or settings.CLINIC_NAME
    wanted = ((profile.email_from or profile.user.email) if profile else "").strip().lower()
    if wanted and _domain(wanted) in settings.EMAIL_VERIFIED_DOMAINS:
        address, fallback = wanted, False
    else:
        address, fallback = default_from_address(), bool(wanted)
    return {"name": name, "address": address, "formatted": formataddr((name, address)),
            "used_fallback": fallback, "wanted": wanted}


def reply_address(thread: EmailThread) -> str:
    return f"t-{thread.token}@{settings.EMAIL_REPLY_DOMAIN}" if settings.EMAIL_REPLY_DOMAIN else ""


# ---------------------------------------------------------------- templates / rendering
_MERGE_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def merge_values(lead: Lead, sender_name: str = "") -> dict:
    from .models import Treatment

    first = (lead.name or "").split(" ")[0] if lead.name else ""
    labels = dict(Treatment.choices)
    treatment = labels.get((lead.treatment_interests or [""])[0], "") if lead.treatment_interests else ""
    return {"first_name": first, "name": lead.name or "", "treatment": treatment, "sender_name": sender_name,
            "clinic_name": settings.CLINIC_NAME, "clinic_phone": settings.CLINIC_PHONE, "clinic_website": settings.CLINIC_WEBSITE}


def render_merge(text: str, values: dict) -> str:
    """Plain-text substitution. Unknown {{fields}} become empty so a typo never leaks raw braces to a patient."""
    return _MERGE_RE.sub(lambda m: str(values.get(m.group(1), "")), text or "")


def text_to_html(text: str) -> str:
    paragraphs = [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    body = "".join(f'<p style="margin:0 0 14px">{escape(p).replace(chr(10), "<br>")}</p>' for p in paragraphs)
    return f'<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#222">{body}</div>'


def with_signature(body: str, profile) -> str:
    sig = (profile.email_signature or "").strip() if profile else ""
    return f"{body.rstrip()}\n\n{sig}" if sig else body


# ---------------------------------------------------------------- unsubscribe (required on every automated email)
UNSUB_SALT = "rarity-email-unsub"


def unsubscribe_token(lead: Lead) -> str:
    return signing.dumps({"l": lead.id}, salt=UNSUB_SALT)


def lead_from_unsubscribe_token(token: str) -> Lead | None:
    try:
        return Lead.objects.filter(pk=signing.loads(token, salt=UNSUB_SALT)["l"]).first()
    except (signing.BadSignature, KeyError, TypeError):
        return None


def unsubscribe_url(lead: Lead) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/crm/email/unsubscribe/{unsubscribe_token(lead)}/"


def unsubscribe_footer(lead: Lead) -> tuple[str, str]:
    url, addr = unsubscribe_url(lead), settings.CLINIC_ADDRESS
    text = f"--\nYou are receiving this because you contacted {settings.CLINIC_NAME}. To stop these emails, unsubscribe here: {url}"
    html = ('<p style="font-family:Arial,sans-serif;font-size:12px;color:#888;margin-top:24px;border-top:1px solid #eee;padding-top:12px">'
            f"You are receiving this because you contacted {escape(settings.CLINIC_NAME)}. "
            f'If you would rather not get these emails, <a href="{escape(url)}" style="color:#888">unsubscribe</a>.')
    if addr:
        text += f"\n{addr}"
        html += f"<br>{escape(addr)}"
    return text, html + "</p>"


# ---------------------------------------------------------------- sending
def _references(thread: EmailThread) -> list[str]:
    return [m.rfc_message_id for m in thread.messages.exclude(rfc_message_id="").order_by("created_at")][-10:]


def send_to_lead(lead: Lead, *, subject: str, body: str, by=None, thread: EmailThread | None = None, copy_to_sender: bool = False,
                 automated: bool = False, enrollment=None, step=None, campaign=None, idempotency_key: str = "") -> EmailMessage:
    """Send one email to a lead. Manual sends come from a person; automated sends (sequences) additionally carry an
    unsubscribe footer + one-click header, are never logged as human contact, and never reach unsubscribed leads."""
    if not lead.email:
        raise EmailError("This lead has no email address.")
    if lead.email_invalid:
        raise EmailError("Emails to this address bounced earlier. Fix the address before sending.")
    if automated and lead.email_opt_out:
        raise EmailError("This lead has unsubscribed from automated emails.")
    if not settings.RESEND_API_KEY:
        raise EmailError("Email isn't configured on the server (RESEND_API_KEY).")
    subject, body = (subject or "").strip(), (body or "").strip()
    if not subject or not body:
        raise EmailError("Subject and message are required.")

    who = sender_identity(by)
    values = merge_values(lead, who["name"])
    subject, body = render_merge(subject, values), render_merge(body, values)
    body = with_signature(body, by)

    if thread is None:
        thread = EmailThread.objects.create(lead=lead, subject=subject[:300])
    elif thread.lead_id != lead.id:
        raise EmailError("That conversation belongs to a different lead.")
    elif not automated and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    html, text = text_to_html(body), body
    headers, refs = {}, _references(thread)
    if refs:
        headers["In-Reply-To"], headers["References"] = refs[-1], " ".join(refs)
    if automated:
        ftext, fhtml = unsubscribe_footer(lead)
        text, html = f"{body}\n\n{ftext}", html + fhtml
        headers["List-Unsubscribe"] = f"<{unsubscribe_url(lead)}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    reply_to = reply_address(thread) or who["address"]

    msg = EmailMessage.objects.create(
        thread=thread, direction=EmailMessage.Direction.OUT, status=EmailMessage.Status.QUEUED, from_address=who["address"],
        to_address=lead.email, subject=subject[:300], body_text=body, body_html=html, in_reply_to=headers.get("In-Reply-To", ""),
        sent_by=None if automated else by, enrollment=enrollment, step=step, campaign=campaign,
    )
    params = {"from": who["formatted"], "to": [lead.email], "subject": subject, "html": html, "text": text, "reply_to": reply_to}
    if headers:
        params["headers"] = headers
    if copy_to_sender and who["wanted"]:
        params["bcc"] = [who["wanted"]]
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        sent = resend.Emails.send(params, {"idempotency_key": idempotency_key}) if idempotency_key else resend.Emails.send(params)
        msg.resend_id = (sent or {}).get("id", "") if isinstance(sent, dict) else getattr(sent, "id", "") or ""
        msg.status = EmailMessage.Status.SENT
    except Exception as e:
        msg.status, msg.error = EmailMessage.Status.FAILED, str(e)[:500]
        msg.save(update_fields=["status", "error"])
        raise EmailError(f"Could not send the email: {e}") from e
    msg.save(update_fields=["resend_id", "status"])

    now = timezone.now()
    if automated:
        EmailThread.objects.filter(pk=thread.pk).update(last_message_at=now)
    else:  # a person wrote to the lead: that is real contact, and it answers any pending reply
        EmailThread.objects.filter(pk=thread.pk).update(last_message_at=now, needs_reply=False)
        Lead.objects.filter(pk=lead.pk).update(last_contacted_at=now)
        ContactLog.objects.create(lead=lead, logged_by=by, method="email", direction="outbound", notes=f"Email: {subject}"[:500])
    return msg


# ---------------------------------------------------------------- webhook security (Resend signs with the Svix scheme)
def verify_signature(secret: str, headers, body: bytes, tolerance: int = 300) -> bool:
    if not secret:
        return False
    try:
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
        msg_id, ts, sigs = headers["svix-id"], headers["svix-timestamp"], headers["svix-signature"]
        if abs(time.time() - int(ts)) > tolerance:
            return False
        expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
        return any(v == "v1" and hmac.compare_digest(sig, expected) for v, _, sig in (p.partition(",") for p in sigs.split()))
    except (KeyError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------- inbound text handling
class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    """Inbound HTML is reduced to plain text and never stored or rendered as HTML (no script/XSS risk in the CRM)."""
    p = _Text()
    p.feed(html or "")
    return re.sub(r"\n{3,}", "\n\n", "".join(p.out)).strip()


_QUOTE_MARKERS = [
    re.compile(r"^\s*On .{5,200}wrote:\s*$", re.I | re.S),
    re.compile(r"^\s*-{2,}\s*(Original Message|Forwarded message)", re.I),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*From:\s.+", re.I),
]


def strip_quoted_reply(text: str) -> str:
    """Keep what the person wrote; drop the quoted history below it. If that leaves nothing, return the original."""
    lines, kept = (text or "").splitlines(), []
    for i, line in enumerate(lines):
        joined = line if i + 1 >= len(lines) else line + " " + lines[i + 1]  # 'On Tue ... \n wrote:' wraps onto 2 lines
        if line.lstrip().startswith(">") or any(r.match(line) or (r.pattern.startswith("^\\s*On") and r.match(joined)) for r in _QUOTE_MARKERS):
            break
        kept.append(line)
    clean = "\n".join(kept).strip()
    return clean or (text or "").strip()


_UNSUB_RE = re.compile(r"^\s*(stop|unsubscribe|remove me|do not (email|contact) me)\b", re.I)


# ---------------------------------------------------------------- inbound processing
def fetch_received(email_id: str) -> dict:
    r = requests.get(f"https://api.resend.com/emails/receiving/{email_id}",
                     headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"}, timeout=20)
    if r.status_code != 200:
        raise EmailError(f"Resend returned {r.status_code} when fetching received email {email_id}")
    return r.json()


def _header(headers, name: str) -> str:
    if isinstance(headers, dict):
        for k, v in headers.items():
            if k.lower() == name.lower():
                return str(v)
    return ""


def _find_thread(addresses: list[str], from_addr: str, subject: str) -> EmailThread:
    for a in addresses:
        m = THREAD_ADDR_RE.search(a or "")
        if m:
            t = EmailThread.objects.filter(token=m.group(1).lower()).first()
            if t:
                return t
    lead = Lead.objects.alive().filter(email__iexact=from_addr).order_by("-created_at").first() if from_addr else None
    if lead:
        t = lead.email_threads.order_by("-last_message_at").first()
        return t or EmailThread.objects.create(lead=lead, subject=subject[:300])
    return EmailThread.objects.create(lead=None, subject=subject[:300])  # unmatched: shown in the Unmatched inbox


@transaction.atomic
def handle_received(data: dict) -> EmailMessage | None:
    email_id = data.get("email_id", "")
    if not email_id:
        return None
    existing = EmailMessage.objects.filter(resend_id=email_id).first()
    if existing:
        return existing  # retried webhook

    full = fetch_received(email_id)
    from_name, from_addr = parseaddr(full.get("from") or data.get("from") or "")
    recipients = [*(full.get("to") or data.get("to") or []), *(data.get("received_for") or []), *(full.get("cc") or [])]
    subject = full.get("subject") or data.get("subject") or ""
    thread = _find_thread(recipients, from_addr.lower(), subject)

    raw = (full.get("text") or "").strip() or html_to_text(full.get("html") or "")
    msg = EmailMessage.objects.create(
        thread=thread, direction=EmailMessage.Direction.IN, status=EmailMessage.Status.RECEIVED,
        from_address=(f"{from_name} <{from_addr}>" if from_name else from_addr)[:320], to_address=", ".join(recipients)[:320],
        subject=subject[:300], body_text=strip_quoted_reply(raw), body_full=raw, resend_id=email_id,
        rfc_message_id=(full.get("message_id") or data.get("message_id") or "")[:300],
        in_reply_to=_header(full.get("headers"), "in-reply-to")[:300],
        attachments=[{"filename": a.get("filename", ""), "content_type": a.get("content_type", ""), "size": a.get("size")}
                     for a in (full.get("attachments") or data.get("attachments") or [])],
    )
    EmailThread.objects.filter(pk=thread.pk).update(last_message_at=timezone.now())
    if thread.lead_id:
        apply_inbound_effects(thread.lead, thread, msg)
    return msg


def apply_inbound_effects(lead: Lead, thread: EmailThread, msg: EmailMessage) -> None:
    """What a reply from a lead triggers automatically (forward-only, never reopens a closed lead)."""
    from . import services
    from .tasks import notify_email_reply

    EmailThread.objects.filter(pk=thread.pk).update(needs_reply=True)
    ContactLog.objects.create(lead=lead, method="email", direction="inbound", notes=f"Email reply: {msg.subject}"[:500])
    Lead.objects.filter(pk=lead.pk).update(last_contacted_at=timezone.now())

    lead.refresh_from_db()
    if lead.status == LeadStatus.NEW:
        services.change_status(lead, LeadStatus.CONTACTED, None)

    title = f"Reply to {lead.name}'s email"
    if not Task.objects.filter(lead=lead, is_done=False, title=title).exists():
        Task.objects.create(lead=lead, assigned_to=lead.assigned_to, title=title, priority=Priority.HIGH,
                            due_at=timezone.now() + timedelta(hours=2))

    if _UNSUB_RE.match(msg.body_text or ""):
        mark_opt_out(lead)
    notify_email_reply(lead.id, msg.id)
    stop_sequences(lead, "replied")


# ---------------------------------------------------------------- delivery events
_RANK = {"queued": 0, "sent": 1, "delayed": 1, "delivered": 2}
_STATUS = {"email.delivered": "delivered", "email.delivery_delayed": "delayed", "email.bounced": "bounced",
           "email.complained": "complained", "email.failed": "failed", "email.suppressed": "failed"}


def handle_delivery_event(event_type: str, data: dict) -> None:
    msg = EmailMessage.objects.filter(resend_id=data.get("email_id", ""), direction=EmailMessage.Direction.OUT).select_related("thread").first()
    if not msg:
        return
    now = timezone.now()
    lead = msg.thread.lead
    if event_type == "email.opened":
        msg.opened_at = msg.opened_at or now
        msg.save(update_fields=["opened_at"])
        return
    if event_type == "email.clicked":
        msg.clicked_at = msg.clicked_at or now
        msg.save(update_fields=["clicked_at"])
        return
    new = _STATUS.get(event_type)
    if not new:
        return
    # a late "sent/delayed" event must never overwrite "delivered"; failures always win
    if new in _RANK and _RANK.get(msg.status, 0) >= _RANK[new] and msg.status != new:
        return
    msg.status = new
    fields = ["status"]
    if new == "delivered":
        msg.delivered_at = now
        fields.append("delivered_at")
    if new in ("failed", "bounced"):
        info = data.get("bounce") or {}
        msg.error = (info.get("message") or data.get("reason") or "")[:500]
        fields.append("error")
        if lead and new == "bounced" and str(info.get("type", "")).lower() in ("permanent", "hard"):
            mark_invalid(lead)
    if new == "complained" and lead:
        mark_opt_out(lead)
    msg.save(update_fields=fields)


def process_event(event_id: str, payload: dict) -> str:
    """Apply one webhook event exactly once. Raises on failure so the provider retries (the dedupe row rolls back)."""
    with transaction.atomic():
        _, created = WebhookEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": payload.get("type", "")[:60]})
        if not created:
            return "duplicate"
        etype, data = payload.get("type", ""), payload.get("data") or {}
        if etype == "email.received":
            handle_received(data)
        else:
            handle_delivery_event(etype, data)
    return "ok"


# ================================================================ automated sequences
EARLY_STATUSES = [LeadStatus.NEW, LeadStatus.CONTACTED]
MAX_ATTEMPTS = 3


def mark_opt_out(lead: Lead) -> None:
    """Unsubscribed / complained / replied STOP: no automated or campaign email, and every running sequence stops."""
    Lead.objects.filter(pk=lead.pk).update(email_opt_out=True, unsubscribed_at=timezone.now())
    stop_sequences(lead, "unsubscribed")


def mark_invalid(lead: Lead) -> None:
    Lead.objects.filter(pk=lead.pk).update(email_invalid=True)
    stop_sequences(lead, "bad_email")


def stop_enrollment(enr: SequenceEnrollment, reason: str) -> None:
    SequenceEnrollment.objects.filter(pk=enr.pk, state=SequenceEnrollment.State.ACTIVE).update(
        state=SequenceEnrollment.State.STOPPED, stop_reason=reason, finished_at=timezone.now(), next_send_at=None)


def stop_sequences(lead: Lead, reason: str) -> int:
    """Stop every running sequence for this lead (replied, booked, unsubscribed, bounced, ...)."""
    return SequenceEnrollment.objects.filter(lead=lead, state=SequenceEnrollment.State.ACTIVE).update(
        state=SequenceEnrollment.State.STOPPED, stop_reason=reason, finished_at=timezone.now(), next_send_at=None)


def stop_sequences_for_status(lead: Lead) -> None:
    """Called when a lead's status changes: sequences only run while the lead is in their allowed statuses."""
    for enr in SequenceEnrollment.objects.filter(lead=lead, state=SequenceEnrollment.State.ACTIVE).select_related("sequence"):
        if lead.status not in (enr.sequence.run_while_status or EARLY_STATUSES):
            stop_enrollment(enr, "progressed")


def ineligible_reason(lead: Lead, sequence: EmailSequence) -> str | None:
    if lead.deleted_at:
        return "lead is archived"
    if not lead.email:
        return "no email address"
    if lead.email_invalid:
        return "email address bounced"
    if lead.email_opt_out:
        return "unsubscribed"
    if not sequence.is_active:
        return "sequence is not active"
    if not sequence.steps.exists():
        return "sequence has no steps"
    if lead.status not in (sequence.run_while_status or EARLY_STATUSES):
        return f"lead status '{lead.status}' is outside this sequence's statuses"
    if SequenceEnrollment.objects.filter(lead=lead, sequence=sequence, state=SequenceEnrollment.State.ACTIVE).exists():
        return "already enrolled"
    return None


def enroll(lead: Lead, sequence: EmailSequence, by=None) -> SequenceEnrollment:
    reason = ineligible_reason(lead, sequence)
    if reason:
        raise EmailError(f"Cannot enrol {lead.name}: {reason}.")
    first = sequence.steps.first()
    try:
        return SequenceEnrollment.objects.create(
            lead=lead, sequence=sequence, enrolled_by=by, current_step=0,
            next_send_at=timezone.now() + timedelta(hours=first.delay_hours))
    except IntegrityError:  # two requests raced; the DB constraint allows only one live enrolment
        raise EmailError(f"Cannot enrol {lead.name}: already enrolled.")


def auto_enroll(lead: Lead) -> int:
    """Enrol a newly created lead in every active auto-enrol sequence that matches its source."""
    from .tasks import kick_sequences

    n = 0
    for seq in EmailSequence.objects.filter(is_active=True, auto_enroll=True):
        if lead.source in (seq.auto_enroll_sources or []):
            try:
                enroll(lead, seq)
                n += 1
            except EmailError:
                pass
    if n:
        kick_sequences()  # send the first (immediate) email right away instead of waiting for the next scheduled tick
    return n


def in_send_window(now=None) -> bool:
    return settings.EMAIL_SEND_START_HOUR <= timezone.localtime(now or timezone.now()).hour < settings.EMAIL_SEND_END_HOUR


def _today_start():
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def sent_today() -> int:
    """Automated emails sent today (sequences + campaigns): this is what the daily cap limits."""
    return (EmailMessage.objects.filter(direction="out", created_at__gte=_today_start())
            .filter(Q(enrollment__isnull=False) | Q(campaign__isnull=False)).exclude(status="failed").count())


def campaign_sent_today() -> int:
    return EmailMessage.objects.filter(direction="out", campaign__isnull=False, created_at__gte=_today_start()).exclude(status="failed").count()


def _finish(enr: SequenceEnrollment, state, reason: str = "") -> None:
    enr.state, enr.stop_reason, enr.finished_at, enr.next_send_at = state, reason, timezone.now(), None
    enr.save(update_fields=["state", "stop_reason", "finished_at", "next_send_at"])


def process_enrollment(enrollment_id: int, now=None) -> str:
    """Handle one due enrolment. Returns: sent | failed | stopped | completed | paused | outside_window | skipped."""
    now = now or timezone.now()
    with transaction.atomic():
        enr = (SequenceEnrollment.objects.select_for_update(skip_locked=True).select_related("lead", "sequence", "thread")
               .filter(pk=enrollment_id, state=SequenceEnrollment.State.ACTIVE, next_send_at__lte=now).first())
        if enr is None:
            return "skipped"  # not due, finished meanwhile, or another worker holds it
        lead, seq = enr.lead, enr.sequence
        for bad, reason in ((lead.deleted_at is not None, "deleted"), (not lead.email, "no_email"), (lead.email_invalid, "bad_email"),
                            (lead.email_opt_out, "unsubscribed"), (lead.status not in (seq.run_while_status or EARLY_STATUSES), "progressed")):
            if bad:
                _finish(enr, SequenceEnrollment.State.STOPPED, reason)
                return "stopped"
        if not seq.is_active:
            return "paused"
        steps = list(seq.steps.select_related("template"))
        if enr.current_step >= len(steps):
            _finish(enr, SequenceEnrollment.State.COMPLETED)
            return "completed"
        if enr.current_step > 0 and not in_send_window(now):
            return "outside_window"  # follow-ups wait for office hours; the first email goes out immediately

        step = steps[enr.current_step]
        try:
            msg = send_to_lead(lead, subject=step.template.subject, body=step.template.body, by=lead.assigned_to or seq.from_user,
                               thread=enr.thread, automated=True, enrollment=enr, step=step,
                               idempotency_key=f"seq-{enr.id}-{step.id}-{enr.attempts}")
        except EmailError as e:
            EmailMessage.objects.filter(enrollment=enr, step=step, status="failed").delete()  # so a retry can re-create it
            enr.attempts += 1
            if enr.attempts >= MAX_ATTEMPTS:
                _finish(enr, SequenceEnrollment.State.STOPPED, "send_failed")
            else:
                enr.next_send_at = now + timedelta(minutes=15 * enr.attempts)
                enr.save(update_fields=["attempts", "next_send_at"])
            log.warning("sequence send failed (enrolment %s step %s): %s", enr.id, step.id, e)
            return "failed"

        enr.thread, enr.attempts, enr.last_sent_at = msg.thread, 0, now
        enr.current_step += 1
        if enr.current_step >= len(steps):
            enr.state, enr.finished_at, enr.next_send_at = SequenceEnrollment.State.COMPLETED, now, None
        else:
            enr.next_send_at = now + timedelta(hours=steps[enr.current_step].delay_hours)
        enr.save()
        return "sent"


def run_sequence_tick(batch: int = 50) -> dict:
    """Send everything that is due, within the daily cap. Safe to run often and concurrently."""
    out = {"sent": 0, "failed": 0, "stopped": 0, "completed": 0, "waiting": 0, "capped": False}
    if not settings.RESEND_API_KEY:
        out["skipped"] = "email not configured"
        return out
    remaining = max(0, settings.EMAIL_DAILY_CAP - sent_today())
    now = timezone.now()
    due = list(SequenceEnrollment.objects.filter(state=SequenceEnrollment.State.ACTIVE, next_send_at__lte=now)
               .order_by("next_send_at").values_list("id", flat=True)[:batch * 3])
    for eid in due:
        if out["sent"] >= remaining:
            out["capped"] = True
            break
        if out["sent"] + out["failed"] >= batch:
            break
        result = process_enrollment(eid, now)
        key = {"paused": "waiting", "outside_window": "waiting", "skipped": "waiting"}.get(result, result)
        out[key] = out.get(key, 0) + 1
    return out
