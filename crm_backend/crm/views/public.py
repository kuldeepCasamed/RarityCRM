from urllib.parse import parse_qs, urlparse

from rest_framework.response import Response
from rest_framework.views import APIView

from .. import services
from datetime import timedelta

from django.utils import timezone

from ..models import LeadSource, Note, Priority, Task
from ..permissions import HasIntakeKey
from ..serializers import PublicInterestSerializer
from ..tasks import notify_new_lead
from ..throttles import PublicIntakeThrottle

ATTRIBUTION = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"]


def attribution_from(d):
    """Explicit payload fields win; otherwise read UTM / click ids from the landing-page URL."""
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
        lead, created = services.create_lead(
            name=f"{d['firstName']} {d.get('lastName', '')}".strip(),
            email=d.get("email", ""),
            phone=d["contactNumber"],
            country=country,
            is_international=bool(d.get("international")) or (bool(country) and country.lower() != "india"),
            source=infer_source(attribution),
            landing_url=d.get("submissionUrl", ""),
            referrer=d.get("referrer", "")[:1000],
            chief_complaint=studio_text or d.get("message", ""),
            consent_marketing=d.get("consent", False),
            **studio_fields,
            **attribution,
        )
        if studio_text:  # also for repeat patients: the request must reach the front desk
            Note.objects.create(lead=lead, body=studio_text)
            Task.objects.create(
                lead=lead, assigned_to=lead.assigned_to, priority=Priority.HIGH,
                title="Confirm Smile Studio consultation request" if wants_slot else "Call Smile Studio lead (design unlocked)",
                due_at=timezone.now() + timedelta(hours=2 if wants_slot else 24),
            )
        if created:
            notify_new_lead(lead.id)
        # never reveal whether the lead already existed
        return Response(self.GENERIC_OK, status=201)
