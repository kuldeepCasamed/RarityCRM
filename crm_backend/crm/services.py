"""Lead business logic: phone normalisation, dedupe, assignment, status changes."""
import phonenumbers
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import CRMUser, Lead, LeadStatus, Role, StatusHistory


COUNTRY_REGION = {
    "india": "IN", "united kingdom": "GB", "uk": "GB", "england": "GB", "united arab emirates": "AE", "uae": "AE",
    "united states": "US", "usa": "US", "canada": "CA", "australia": "AU", "singapore": "SG", "germany": "DE",
    "nigeria": "NG", "kenya": "KE", "south africa": "ZA", "bangladesh": "BD", "nepal": "NP", "sri lanka": "LK",
    "iraq": "IQ", "oman": "OM", "qatar": "QA", "saudi arabia": "SA", "kuwait": "KW", "bahrain": "BH",
    "new zealand": "NZ", "ireland": "IE", "france": "FR", "tanzania": "TZ", "uganda": "UG", "ghana": "GH",
    "afghanistan": "AF", "iran": "IR", "maldives": "MV", "bhutan": "BT", "myanmar": "MM", "malaysia": "MY",
}


def region_for_country(country: str = "") -> str | None:
    c = (country or "").strip()
    if c.lower() in COUNTRY_REGION:
        return COUNTRY_REGION[c.lower()]
    if len(c) == 2 and c.isalpha() and c.upper() in phonenumbers.SUPPORTED_REGIONS:
        return c.upper()
    return None


def normalize_phone(raw: str, region: str | None = None, country: str = "") -> str:
    """Return E.164 or '' if it can't be parsed. Numbers without a '+' are read in the lead's
    country when known, else the default region (India)."""
    if not raw:
        return ""
    try:
        num = phonenumbers.parse(raw, region or region_for_country(country) or settings.CRM_DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_possible_number(num):
        return ""
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def find_duplicate(email: str = "", phone: str = "", exclude_id=None):
    q = Q()
    if email:
        q |= Q(email__iexact=email)
    if phone:
        q |= Q(phone=phone)
    if not q:
        return None
    qs = Lead.objects.alive().filter(q)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.first()


def pick_assignee():
    """Load-based round robin: active auto-assign reps with fewest open leads,
    respecting daily_lead_limit."""
    today = timezone.localdate()
    reps = (
        CRMUser.objects.filter(is_active=True, receives_auto_assign=True, role__in=[Role.REP, Role.FRONT_DESK])
        .annotate(
            open_leads=Count(
                "leads",
                filter=Q(leads__deleted_at__isnull=True)
                & ~Q(leads__status__in=[LeadStatus.CONVERTED, LeadStatus.LOST, LeadStatus.NOT_INTERESTED, LeadStatus.INVALID]),
            ),
            today_leads=Count("leads", filter=Q(leads__created_at__date=today)),
        )
        .order_by("open_leads", "id")
    )
    for rep in reps:
        if rep.daily_lead_limit and rep.today_leads >= rep.daily_lead_limit:
            continue
        return rep
    return None


@transaction.atomic
def create_lead(*, auto_assign=True, changed_by=None, **fields) -> tuple[Lead, bool]:
    """Create a lead unless a duplicate (phone/email) exists.
    Returns (lead, created)."""
    fields["phone"] = normalize_phone(fields.get("phone", ""), country=fields.get("country", ""))
    fields["email"] = (fields.get("email") or "").strip().lower()
    existing = find_duplicate(fields["email"], fields["phone"])
    if existing:
        return existing, False
    lead = Lead.objects.create(**fields)
    StatusHistory.objects.create(lead=lead, from_status="", to_status=lead.status, changed_by=changed_by)
    if auto_assign and not lead.assigned_to:
        rep = pick_assignee()
        if rep:
            lead.assigned_to = rep
            lead.save(update_fields=["assigned_to", "updated_at"])
    return lead, True


@transaction.atomic
def change_status(lead: Lead, new_status: str, by: CRMUser | None = None) -> Lead:
    if new_status == lead.status:
        return lead
    old = lead.status
    lead.status = new_status
    fields = ["status", "updated_at"]
    if new_status == LeadStatus.CONVERTED:
        if not lead.converted_at:
            lead.converted_at = timezone.now()
            fields.append("converted_at")
        if lead.converted_value is None and lead.quote_amount:
            lead.converted_value = lead.quote_amount  # revenue defaults to the quote
            fields.append("converted_value")
    lead.save(update_fields=fields)
    StatusHistory.objects.create(lead=lead, from_status=old, to_status=new_status, changed_by=by)
    return lead


@transaction.atomic
def merge_leads(primary: Lead, duplicate: Lead) -> Lead:
    """Move all child records from duplicate into primary, fill blanks, archive duplicate."""
    for rel in ("contacts", "notes", "tasks", "appointments", "status_history"):
        getattr(duplicate, rel).update(lead=primary)
    for f in ("email", "phone", "country", "chief_complaint", "budget_band", "insurance"):
        if not getattr(primary, f) and getattr(duplicate, f):
            setattr(primary, f, getattr(duplicate, f))
    primary.tags = sorted(set(primary.tags) | set(duplicate.tags))
    primary.treatment_interests = sorted(set(primary.treatment_interests) | set(duplicate.treatment_interests))
    primary.save()
    duplicate.soft_delete()
    from .scoring import rescore

    rescore(primary)
    return primary
