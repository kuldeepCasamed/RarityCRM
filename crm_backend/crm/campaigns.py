"""One-off email campaigns: pick an audience with the Leads-page filters, send now or later, read the results.

Safety rules (always on): never emails opted-out / bounced / missing addresses, never the same lead twice in a campaign,
skips leads emailed recently or waiting on a person, every email has the unsubscribe link, shares the daily cap with sequences
(campaigns may use at most EMAIL_CAMPAIGN_SHARE of it) and only sends in office hours.
"""
import logging
import time
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from . import emailing
from .emailing import EmailError, merge_values, render_merge, sender_identity, text_to_html, with_signature
from .filters import LeadFilter
from .models import (
    Appointment, CampaignRecipient, EmailCampaign, EmailMessage, EmailThread, Lead, LeadSource, LeadStatus, TREATMENT_SLUGS,
)

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
FINAL = (EmailCampaign.Status.SENT, EmailCampaign.Status.CANCELLED)


# ---------------------------------------------------------------- audience
def clean_filters(raw) -> dict:
    """Validate the audience filters (a subset of the Leads-page filters) and drop empty ones. Raises ValueError."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("Audience filters must be an object.")
    out, bad = {}, []

    def lst(key, valid):
        v = raw.get(key)
        if v in (None, "", []):
            return
        v = v if isinstance(v, list) else [v]
        if not all(x in valid for x in v):
            bad.append(key)
        else:
            out[key] = v

    lst("status", {c for c, _ in LeadStatus.choices})
    lst("source", {c for c, _ in LeadSource.choices})
    for key, valid in (("treatment", TREATMENT_SLUGS), ("band", {"hot", "warm", "cold"}), ("priority", {"low", "medium", "high"})):
        v = raw.get(key)
        if v not in (None, ""):
            out[key] = v if v in valid else bad.append(key)
    for key in ("country", "tag"):
        v = (raw.get(key) or "").strip()
        if v:
            out[key] = v[:100]
    if raw.get("is_international") not in (None, ""):
        out["is_international"] = bool(raw["is_international"]) if isinstance(raw["is_international"], bool) else str(raw["is_international"]).lower() == "true"
    if raw.get("score_min") not in (None, ""):
        try:
            out["score_min"] = max(0, min(100, int(raw["score_min"])))
        except (TypeError, ValueError):
            bad.append("score_min")
    for key in ("created_from", "created_to"):
        if raw.get(key):
            try:
                out[key] = date.fromisoformat(str(raw[key])).isoformat()
            except ValueError:
                bad.append(key)
    a = raw.get("assigned_to")
    if a not in (None, ""):
        if a == "unassigned" or str(a).isdigit():
            out["assigned_to"] = str(a)
        else:
            bad.append("assigned_to")
    if bad:
        raise ValueError(f"Invalid audience filter(s): {', '.join(sorted(set(bad)))}")
    return out


def _filter_data(filters: dict) -> dict:
    data = {}
    for k, v in filters.items():
        data[k] = ",".join(v) if isinstance(v, list) else ("true" if v is True else "false" if v is False else str(v))
    return data


def audience(filters: dict, skip_recent_days: int = 3, skip_open_conversations: bool = True):
    """Returns (queryset of eligible leads, breakdown). The breakdown adds up: matching = eligible + everything excluded."""
    base = LeadFilter(_filter_data(filters), queryset=Lead.objects.alive()).qs
    out = {"matching": base.count()}
    qs = base
    for key, q in (("no_email", Q(email="")), ("invalid", Q(email_invalid=True)), ("opted_out", Q(email_opt_out=True))):
        out[key] = qs.filter(q).count()
        qs = qs.exclude(q)
    if skip_recent_days:
        cutoff = timezone.now() - timedelta(days=skip_recent_days)
        recent = (EmailMessage.objects.filter(direction="out", created_at__gte=cutoff).filter(Q(enrollment__isnull=False) | Q(campaign__isnull=False))
                  .exclude(status="failed").values("thread__lead_id"))
        out["recently_emailed"] = qs.filter(pk__in=recent).count()
        qs = qs.exclude(pk__in=recent)
    else:
        out["recently_emailed"] = 0
    if skip_open_conversations:
        waiting = EmailThread.objects.filter(needs_reply=True, lead__isnull=False).values("lead_id")
        out["open_conversation"] = qs.filter(pk__in=waiting).count()
        qs = qs.exclude(pk__in=waiting)
    else:
        out["open_conversation"] = 0
    out["eligible"] = qs.count()
    return qs, out


# ---------------------------------------------------------------- lifecycle
def sender_profile(campaign: EmailCampaign):
    return campaign.from_user or campaign.created_by


def launch(campaign: EmailCampaign, when=None) -> int:
    """Freeze the audience into recipients and schedule the campaign. Returns the number of recipients."""
    if campaign.status != EmailCampaign.Status.DRAFT:
        raise EmailError("Only a draft campaign can be sent.")
    if not settings.RESEND_API_KEY:
        raise EmailError("Email isn't configured on the server (RESEND_API_KEY).")
    if not campaign.subject.strip() or not campaign.body.strip():
        raise EmailError("The campaign needs a subject and a message.")
    now = timezone.now()
    when = when or now
    if when < now - timedelta(minutes=2):
        raise EmailError("The scheduled time is in the past.")
    qs, info = audience(campaign.audience_filters, campaign.skip_recent_days, campaign.skip_open_conversations)
    if info["eligible"] == 0:
        raise EmailError("Nobody in this audience can be emailed right now (see the exclusions above).")
    if info["eligible"] > settings.EMAIL_CAMPAIGN_MAX_RECIPIENTS:
        raise EmailError(f"This audience has {info['eligible']} people; the limit per campaign is {settings.EMAIL_CAMPAIGN_MAX_RECIPIENTS}. Narrow the filters.")
    with transaction.atomic():
        CampaignRecipient.objects.bulk_create([CampaignRecipient(campaign=campaign, lead_id=i) for i in qs.values_list("id", flat=True)], ignore_conflicts=True)
        campaign.status, campaign.scheduled_at = EmailCampaign.Status.SCHEDULED, when
        campaign.save(update_fields=["status", "scheduled_at"])
    from .tasks import kick_campaigns

    kick_campaigns()
    return info["eligible"]


def pause(campaign: EmailCampaign) -> None:
    if campaign.status not in (EmailCampaign.Status.SCHEDULED, EmailCampaign.Status.SENDING):
        raise EmailError("Only a scheduled or sending campaign can be paused.")
    campaign.status = EmailCampaign.Status.PAUSED
    campaign.save(update_fields=["status"])


def resume(campaign: EmailCampaign) -> None:
    if campaign.status != EmailCampaign.Status.PAUSED:
        raise EmailError("This campaign is not paused.")
    campaign.status = EmailCampaign.Status.SENDING if campaign.started_at else EmailCampaign.Status.SCHEDULED
    campaign.save(update_fields=["status"])
    from .tasks import kick_campaigns

    kick_campaigns()


def cancel(campaign: EmailCampaign) -> int:
    """Stop for good. Emails already sent stay sent; everyone still waiting is skipped. Returns how many were skipped."""
    if campaign.status in FINAL:
        raise EmailError("This campaign is already finished.")
    with transaction.atomic():
        n = campaign.recipients.filter(state=CampaignRecipient.State.PENDING).update(state=CampaignRecipient.State.SKIPPED, skip_reason="cancelled")
        campaign.status, campaign.finished_at = EmailCampaign.Status.CANCELLED, timezone.now()
        campaign.save(update_fields=["status", "finished_at"])
    return n


# ---------------------------------------------------------------- sending
def _skip(r: CampaignRecipient, reason: str) -> None:
    r.state, r.skip_reason = CampaignRecipient.State.SKIPPED, reason
    r.save(update_fields=["state", "skip_reason"])


def process_recipient(recipient_id: int, now=None) -> str:
    """Send to one recipient. Returns: sent | failed | skipped | stale."""
    now = now or timezone.now()
    with transaction.atomic():
        r = (CampaignRecipient.objects.select_for_update(skip_locked=True).select_related("campaign", "lead", "campaign__created_by", "campaign__from_user")
             .filter(pk=recipient_id, state=CampaignRecipient.State.PENDING, campaign__status=EmailCampaign.Status.SENDING).first())
        if r is None:
            return "stale"  # already handled, paused/cancelled meanwhile, or another worker has it
        lead, c = r.lead, r.campaign
        # re-check at the moment of sending: things change between scheduling and sending
        for bad, reason in ((lead.deleted_at is not None, "deleted"), (not lead.email, "no_email"), (lead.email_invalid, "bad_email"), (lead.email_opt_out, "unsubscribed")):
            if bad:
                _skip(r, reason)
                return "skipped"
        try:
            msg = emailing.send_to_lead(lead, subject=c.subject, body=c.body, by=sender_profile(c), automated=True, campaign=c,
                                        idempotency_key=f"camp-{c.id}-{r.id}-{r.attempts}")
        except EmailError as e:
            EmailMessage.objects.filter(campaign=c, thread__lead=lead, status="failed").delete()
            r.attempts += 1
            if r.attempts >= MAX_ATTEMPTS:
                r.state = CampaignRecipient.State.FAILED
            else:
                r.next_attempt_at = now + timedelta(minutes=15 * r.attempts)
            r.save(update_fields=["attempts", "state", "next_attempt_at"])
            log.warning("campaign %s send to lead %s failed: %s", c.id, lead.id, e)
            return "failed"
        r.state, r.message, r.sent_at = CampaignRecipient.State.SENT, msg, now
        r.save(update_fields=["state", "message", "sent_at"])
        return "sent"


def run_campaign_tick(batch: int = 40) -> dict:
    out = {"sent": 0, "failed": 0, "skipped": 0, "capped": False, "campaigns_finished": 0}
    if not settings.RESEND_API_KEY:
        out["skipped_all"] = "email not configured"
        return out
    now = timezone.now()
    if not emailing.in_send_window(now):
        out["waiting"] = "outside office hours"
        return out
    EmailCampaign.objects.filter(status=EmailCampaign.Status.SCHEDULED, scheduled_at__lte=now).update(status=EmailCampaign.Status.SENDING, started_at=now)
    cap = settings.EMAIL_DAILY_CAP
    remaining = max(0, min(cap - emailing.sent_today(), int(cap * settings.EMAIL_CAMPAIGN_SHARE) - emailing.campaign_sent_today()))
    for c in EmailCampaign.objects.filter(status=EmailCampaign.Status.SENDING).order_by("started_at"):
        due = (c.recipients.filter(state=CampaignRecipient.State.PENDING).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
               .order_by("id").values_list("id", flat=True)[:batch])
        for rid in list(due):
            if out["sent"] >= remaining:
                out["capped"] = True
                break
            result = process_recipient(rid, now)
            if result == "sent":
                out["sent"] += 1
                if settings.EMAIL_SEND_PAUSE:
                    time.sleep(settings.EMAIL_SEND_PAUSE)
            elif result in ("failed", "skipped"):
                out[result] += 1
        if not c.recipients.filter(state=CampaignRecipient.State.PENDING).exists():
            EmailCampaign.objects.filter(pk=c.pk, status=EmailCampaign.Status.SENDING).update(status=EmailCampaign.Status.SENT, finished_at=timezone.now())
            out["campaigns_finished"] += 1
        if out["capped"]:
            break
    return out


def send_test(campaign: EmailCampaign, by, to: str) -> str:
    """Send a preview to one address (no recipient row, no thread, no logging). Returns the recipient address."""
    if not settings.RESEND_API_KEY:
        raise EmailError("Email isn't configured on the server (RESEND_API_KEY).")
    if not to:
        raise EmailError("Choose an address to send the test to.")
    qs, _ = audience(campaign.audience_filters, 0, False)
    sample = qs.order_by("-created_at").first() or Lead(name="Sample Patient", email=to)
    sender = sender_profile(campaign) or by
    who = sender_identity(sender)
    values = merge_values(sample, who["name"])
    body = with_signature(render_merge(campaign.body, values), sender)
    footer = "--\nThis is a TEST. In a real send, an unsubscribe link and your clinic address appear here."
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({"from": who["formatted"], "to": [to], "subject": f"[TEST] {render_merge(campaign.subject, values)}",
                            "html": text_to_html(body + "\n\n" + footer), "text": f"{body}\n\n{footer}", "reply_to": who["address"]})
    except Exception as e:
        raise EmailError(f"Could not send the test: {e}") from e
    return to


# ---------------------------------------------------------------- report
def report(c: EmailCampaign) -> dict:
    recs = c.recipients.all()
    msgs = c.messages.filter(direction="out").exclude(status="failed")
    sent = msgs.count()
    replied_q = EmailMessage.objects.filter(thread=OuterRef("message__thread"), direction="in", created_at__gt=OuterRef("message__created_at"))
    sent_recs = recs.filter(state=CampaignRecipient.State.SENT)
    skipped = {}
    for reason in recs.filter(state=CampaignRecipient.State.SKIPPED).values_list("skip_reason", flat=True):
        skipped[reason or "other"] = skipped.get(reason or "other", 0) + 1
    return {
        "recipients": recs.count(), "pending": recs.filter(state="pending").count(), "sent": sent, "failed": recs.filter(state="failed").count(),
        "skipped": skipped, "skipped_total": sum(skipped.values()),
        "delivered": msgs.filter(Q(status="delivered") | Q(delivered_at__isnull=False)).count(),
        "opened": msgs.filter(opened_at__isnull=False).count(), "clicked": msgs.filter(clicked_at__isnull=False).count(),
        "bounced": msgs.filter(status="bounced").count(), "complained": msgs.filter(status="complained").count(),
        "replied": sent_recs.annotate(r=Exists(replied_q)).filter(r=True).count(),
        "unsubscribed": sent_recs.filter(lead__unsubscribed_at__gte=F("sent_at")).count(),
        "booked_after": sent_recs.annotate(b=Exists(Appointment.objects.filter(lead=OuterRef("lead"), created_at__gte=OuterRef("sent_at")))).filter(b=True).count(),
        "progress_pct": round(sent / max(1, recs.exclude(state="skipped").count()) * 100),
    }
