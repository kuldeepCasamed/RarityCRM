from urllib.parse import parse_qs, urlparse

from rest_framework.response import Response
from rest_framework.views import APIView

from .. import meta_capi, services
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import TREATMENT_SLUGS, Treatment, LeadSource, Note, Priority, Task
from ..permissions import HasIntakeKey
from ..serializers import PublicInterestSerializer
from ..tasks import notify_capi_event, notify_meta_lead, notify_new_lead
from ..throttles import PublicIntakeThrottle

META_FIELDS = ["meta_event_id", "meta_fbp", "meta_fbc", "meta_event_source_url", "meta_client_ip", "meta_client_user_agent"]
ATTRIBUTION = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"]


def attribution_from(d):
    """The website's campaign cookie (attr_*) wins, then explicit payload fields, then the form page's URL."""
    d = {**d, **{k: d[f"attr_{k}"] for k in ATTRIBUTION if d.get(f"attr_{k}")}}
    query = parse_qs(urlparse(d.get("submissionUrl", "")).query)
    out = {}
    for k in ATTRIBUTION:
        out[k] = (d.get(k) or (query.get(k) or [""])[0])[:255]
    return out


def infer_source(d):
    if d.get("gclid") or d.get("utm_source") == "google":
        return LeadSource.GOOGLE_ADS
    if d.get("fbclid") or d.get("utm_source") in ("facebook", "instagram", "meta"):
        return LeadSource.META_ADS
    return LeadSource.WEBSITE


STUDIO_TREATMENTS = {"veneers": "smile_design", "lumineers": "smile_design", "crowns": "smile_design", "aligners": "invisalign"}


def studio_request(d):
    """Lead fields + follow-up text for a Smile Studio consultation request (empty dict for plain forms)."""
    if not d.get("smile_studio_reference"):
        return {}, "", False
    interest = STUDIO_TREATMENTS.get(d.get("treatment_interest", ""))
    when = " ".join(str(x) for x in (d.get("preferred_date"), d.get("preferred_time")) if x)
    if when:
        request_line = f"Requested: {d.get('consultation_type') or 'clinic'} consultation, {when} (NOT confirmed)"
    else:
        request_line = "Unlocked their smile design (no consultation requested yet)"
    lines = [
        f"Smile Studio {d['smile_studio_reference']}",
        request_line,
        f"Design: {d.get('design_summary') or '-'}",
    ]
    if d.get("message"):
        lines.append(f"Patient notes: {d['message']}")
    fields = {
        "tags": ["smile-studio"],
        "treatment_interests": [interest] if interest else [],
        "preferred_days": [str(d["preferred_date"])] if d.get("preferred_date") else [],
        "preferred_time": (d.get("preferred_time") or "")[:50],
    }
    return fields, "\n".join(lines), bool(when)


def quote_request(d):
    """Lead fields + note text for the website's 'Request a quote' form (empty for every other form)."""
    if not d.get("quote_request"):
        return {}, ""
    slug = d.get("treatment_interest") if d.get("treatment_interest") in TREATMENT_SLUGS else Treatment.OTHER.value
    label = Treatment(slug).label
    text = f"Quote request: {label}"
    if d.get("message"):
        text += f"\nPatient notes: {d['message']}"
    return {"tags": ["quote-request"], "treatment_interests": [slug], "treatment_interest": label}, text


def landing_fields(d):
    """Landing page + first-seen time from the website cookie; a malformed value is dropped, never an error."""
    out = {}
    if d.get("attr_landing_page"):
        out["landing_page"] = str(d["attr_landing_page"])[:300]
    if d.get("attr_first_seen"):
        seen = parse_datetime(str(d["attr_first_seen"]))
        if seen:
            out["attribution_first_seen"] = seen if timezone.is_aware(seen) else timezone.make_aware(seen)
    return out


class InterestView(APIView):
    """POST /api/crm/interest/ — called server-to-server by raritydental.com."""

    permission_classes = [HasIntakeKey]
    authentication_classes = []
    throttle_classes = [PublicIntakeThrottle]

    GENERIC_OK = {"detail": "Thank you. We will be in touch shortly."}

    def post(self, request):
        ser = PublicInterestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = {k: ("" if v is None else v) for k, v in ser.validated_data.items()}  # null -> ""
        if d.get("website"):  # honeypot filled -> pretend success
            return Response(self.GENERIC_OK, status=201)

        attribution = attribution_from(d)
        country = d.get("country", "")
        studio_fields, studio_text, wants_slot = studio_request(d)
        quote_fields, quote_text = quote_request(d)
        lead, created = services.create_lead(
            name=f"{d['firstName']} {d.get('lastName', '')}".strip(),
            email=d.get("email", ""),
            phone=d["contactNumber"],
            country=country,
            is_international=bool(d.get("international")) or (bool(country) and country.lower() != "india"),
            source=infer_source(attribution),
            landing_url=d.get("submissionUrl", ""),
            referrer=d.get("referrer", "")[:1000],
            chief_complaint=studio_text or quote_text or d.get("message", ""),
            consent_marketing=d.get("consent", False),
            **studio_fields,
            **quote_fields,
            **attribution,
            **landing_fields(d),
        )
        if studio_text:  # also for repeat patients: the request must reach the front desk
            Note.objects.create(lead=lead, body=studio_text)
            Task.objects.create(
                lead=lead, assigned_to=lead.assigned_to, priority=Priority.HIGH,
                title="Confirm Smile Studio consultation request" if wants_slot else "Call Smile Studio lead (design unlocked)",
                due_at=timezone.now() + timedelta(hours=2 if wants_slot else 24),
            )
        if quote_text:  # also for repeat patients: a quote request must never be lost
            Note.objects.create(lead=lead, body=quote_text)
            Task.objects.create(
                lead=lead, assigned_to=lead.assigned_to, priority=Priority.HIGH,
                title=f"Send quote to {lead.name}", due_at=timezone.now() + timedelta(hours=4),
            )
            if not created:
                for k in ("tags", "treatment_interests"):  # keep the existing lead's data, add what's new
                    merged = sorted({*getattr(lead, k), *quote_fields[k]})
                    setattr(lead, k, merged)
                lead.save(update_fields=["tags", "treatment_interests", "updated_at"])
                notify_meta_lead(lead.id, repeat=True)
        meta = {k: str(d[k]) for k in META_FIELDS if d.get(k)}
        if meta:  # keep the latest browser/IP data so later events (Schedule, TreatmentBooked) still match
            for k, v in meta.items():
                setattr(lead, k, v)
            lead.save(update_fields=[*meta, "updated_at"])
        # Lead only when the browser sent an event_id (same id as its Pixel Lead, so Meta counts it once).
        # Smile Studio bookings send none on purpose: Schedule comes from the CRM status instead.
        if meta_capi.enabled() and meta.get("meta_event_id"):
            notify_capi_event(lead.id, "Lead", meta["meta_event_id"])
        if created:
            notify_new_lead(lead.id)
        # never reveal whether the lead already existed
        return Response(self.GENERIC_OK, status=201)
