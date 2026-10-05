"""Transparent additive lead score (0-100). Every point is explained in `score_breakdown`
so a rep can see *why* a lead is hot. Tune the constants below, not the logic."""
from datetime import timedelta

from django.utils import timezone

from .models import Lead, LeadStatus

HOT, WARM = 60, 30

HIGH_VALUE_TREATMENTS = {"implants", "full_mouth", "smile_design", "invisalign", "single_day"}
SOURCE_POINTS = {"referral": 10, "walk_in": 8, "phone": 8, "whatsapp": 8, "partner": 6, "google_ads": 6,
                 "website": 5, "meta_ads": 3}
STAGE_POINTS = {LeadStatus.CONTACTED: 5, LeadStatus.CONSULT_BOOKED: 15, LeadStatus.CONSULT_DONE: 22,
                LeadStatus.PLAN_SENT: 28, LeadStatus.NEGOTIATING: 33, LeadStatus.TREATMENT_BOOKED: 40}
CONNECTED = {"connected", "interested", "callback_requested"}
DEAD = {LeadStatus.LOST, LeadStatus.NOT_INTERESTED, LeadStatus.INVALID}


def band(score: int) -> str:
    return "hot" if score >= HOT else "warm" if score >= WARM else "cold"


def compute(lead: Lead, now=None) -> tuple[int, list[dict]]:
    now = now or timezone.now()
    if lead.status == LeadStatus.CONVERTED:
        return 100, [{"label": "Converted", "points": 100}]
    if lead.status in DEAD:
        return 0, [{"label": f"Closed ({lead.get_status_display()})", "points": 0}]

    parts: list[tuple[str, int]] = []

    def add(label, pts):
        if pts:
            parts.append((label, pts))

    # --- fit / profile ---
    add("Has phone number", 5 if lead.phone else 0)
    add("Has email", 3 if lead.email else 0)
    interests = set(lead.treatment_interests or [])
    if interests & HIGH_VALUE_TREATMENTS:
        add("High-value treatment interest", 10)
    elif interests:
        add("Treatment interest recorded", 5)
    add("Budget captured", 4 if lead.budget_band else 0)
    add("Quote given", 8 if lead.quote_amount else 0)
    add("International with travel dates", 8 if lead.is_international and lead.travel_from else 0)
    add("Urgent need", {"urgent": 10, "emergency": 10, "soon": 5}.get(lead.urgency, 0))
    add(f"Source: {lead.get_source_display()}", SOURCE_POINTS.get(lead.source, 0))

    # --- engagement (works with prefetch_related) ---
    contacts = list(lead.contacts.all())
    connected = sum(1 for c in contacts if c.outcome in CONNECTED)
    add(f"{min(connected, 2)} connected conversation(s)", min(connected, 2) * 5)
    add("Lead contacted us (inbound)", 5 if any(c.direction == "inbound" for c in contacts) else 0)
    last = max((c.contacted_at for c in contacts), default=None)
    age = now - lead.created_at
    if last and now - last <= timedelta(days=7):
        add("Recent contact (7 days)", 5)
    elif last and now - last > timedelta(days=14):
        add("No contact for 14+ days", -10)
    elif not last and age > timedelta(days=3):
        add("Never contacted (3+ days old)", -5)
    if len(contacts) >= 3 and not connected:
        add("3+ attempts, never connected", -5)

    # --- pipeline ---
    add(f"Stage: {lead.get_status_display()}", STAGE_POINTS.get(lead.status, 0))
    appts = list(lead.appointments.all())
    add("Attended an appointment", 10 if any(a.status == "attended" for a in appts) else 0)
    add("Upcoming appointment", 5 if any(a.status in ("booked", "confirmed") and a.start_at > now for a in appts) else 0)
    add("Missed an appointment", -5 if any(a.status == "no_show" for a in appts) else 0)

    total = max(0, min(100, sum(p for _, p in parts)))
    return total, [{"label": l, "points": p} for l, p in parts]


def rescore(lead_or_id) -> int | None:
    """Recompute and persist one lead's score without re-triggering signals (queryset.update)."""
    lead = lead_or_id if isinstance(lead_or_id, Lead) else Lead.objects.filter(pk=lead_or_id).first()
    if lead is None:
        return None
    score, breakdown = compute(lead)
    now = timezone.now()
    Lead.objects.filter(pk=lead.pk).update(score=score, score_breakdown=breakdown, scored_at=now)
    lead.score, lead.score_breakdown, lead.scored_at = score, breakdown, now
    return score


def refresh_all() -> int:
    """Nightly: rescore every open lead (decay for stale ones) with 2 queries of prefetch + one bulk_update."""
    now = timezone.now()
    leads = list(Lead.objects.alive().exclude(status__in=[*DEAD, LeadStatus.CONVERTED]).prefetch_related("contacts", "appointments"))
    changed = []
    for lead in leads:
        score, breakdown = compute(lead, now)
        if score != lead.score or breakdown != lead.score_breakdown:
            lead.score, lead.score_breakdown, lead.scored_at = score, breakdown, now
            changed.append(lead)
    Lead.objects.bulk_update(changed, ["score", "score_breakdown", "scored_at"], batch_size=500)
    return len(changed)
