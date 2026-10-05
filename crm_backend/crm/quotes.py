"""Quote workflow: numbering, create/update, status transitions and lead sync."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import pricing, services
from .models import Lead, LeadStatus, Quote, QuoteItem, QuoteSequence, QuoteStatus

TRANSITIONS = {
    QuoteStatus.DRAFT: {QuoteStatus.SENT},
    QuoteStatus.SENT: {QuoteStatus.ACCEPTED, QuoteStatus.REJECTED, QuoteStatus.EXPIRED},
    QuoteStatus.ACCEPTED: set(), QuoteStatus.REJECTED: set(), QuoteStatus.EXPIRED: set(),
}
STAGE_ORDER = [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.CONSULT_BOOKED, LeadStatus.CONSULT_DONE,
               LeadStatus.PLAN_SENT, LeadStatus.NEGOTIATING, LeadStatus.TREATMENT_BOOKED]


class QuoteError(Exception):
    pass


def next_number(year: int | None = None) -> str:
    """RQ-2026-0001, race-safe: the year's sequence row is locked while incrementing (Postgres)."""
    year = year or timezone.localdate().year
    with transaction.atomic():
        seq, _ = QuoteSequence.objects.select_for_update().get_or_create(year=year)
        seq.last += 1
        seq.save(update_fields=["last"])
    return f"RQ-{year}-{seq.last:04d}"


def _replace_items(quote, items_data):
    quote.items.all().delete()
    items = [QuoteItem(quote=quote, position=i, description=d["description"], area=d.get("area", ""),
                       quantity=d["quantity"], unit_price=d["unit_price"]) for i, d in enumerate(items_data)]
    pricing.apply(quote, items)
    QuoteItem.objects.bulk_create(items)
    quote.save(update_fields=["subtotal", "discount_amount", "tax_amount", "total", "updated_at"])


@transaction.atomic
def create_quote(lead: Lead, data: dict, created_by=None) -> Quote:
    data = dict(data)
    items = data.pop("items")
    data.setdefault("valid_until", timezone.localdate() + timedelta(days=settings.QUOTE_VALID_DAYS))
    if not data.get("terms"):
        data["terms"] = settings.QUOTE_DEFAULT_TERMS
    quote = Quote.objects.create(lead=lead, number=next_number(), created_by=created_by, **data)
    _replace_items(quote, items)
    return quote


@transaction.atomic
def update_quote(quote: Quote, data: dict) -> Quote:
    data = dict(data)
    items = data.pop("items", None)
    for k, v in data.items():
        setattr(quote, k, v)
    quote.save()
    _replace_items(quote, items if items is not None else [
        {"description": i.description, "area": i.area, "quantity": i.quantity, "unit_price": i.unit_price} for i in quote.items.all()
    ])
    return quote


@transaction.atomic
def duplicate(quote: Quote, created_by=None) -> Quote:
    return create_quote(quote.lead, {
        "currency": quote.currency, "discount_type": quote.discount_type, "discount_value": quote.discount_value,
        "tax_percent": quote.tax_percent, "notes": quote.notes, "terms": quote.terms,
        "items": [{"description": i.description, "area": i.area, "quantity": i.quantity, "unit_price": i.unit_price} for i in quote.items.all()],
    }, created_by)


def _advance(lead: Lead, target, by=None):
    """Move the lead forward in the pipeline, never backwards and never out of a closed state."""
    if lead.status in STAGE_ORDER and STAGE_ORDER.index(lead.status) < STAGE_ORDER.index(target):
        services.change_status(lead, target, by)


@transaction.atomic
def set_status(quote: Quote, new: str, by=None) -> Quote:
    if new not in TRANSITIONS[quote.status]:
        raise QuoteError(f"A {quote.get_status_display().lower()} quote cannot become {new}.")
    now = timezone.now()
    if new == QuoteStatus.SENT:
        if quote.valid_until < timezone.localdate():
            raise QuoteError("This quote has already expired - set a new validity date before sending.")
        quote.sent_at = now
    if new in (QuoteStatus.ACCEPTED, QuoteStatus.REJECTED, QuoteStatus.EXPIRED):
        quote.decided_at = now
    quote.status = new
    quote.save(update_fields=["status", "sent_at", "decided_at", "updated_at"])

    lead = Lead.objects.get(pk=quote.lead_id)
    if new == QuoteStatus.SENT:
        if quote.currency == "INR":  # lead.quote_amount is INR (it feeds ROI), so only INR quotes sync
            lead.quote_amount, lead.quote_valid_until = quote.total, quote.valid_until
            lead.save(update_fields=["quote_amount", "quote_valid_until", "updated_at"])
        _advance(lead, LeadStatus.PLAN_SENT, by)
    elif new == QuoteStatus.ACCEPTED:
        _advance(lead, LeadStatus.TREATMENT_BOOKED, by)
    return quote


def expire_overdue() -> int:
    return Quote.objects.filter(status=QuoteStatus.SENT, valid_until__lt=timezone.localdate()).update(
        status=QuoteStatus.EXPIRED, decided_at=timezone.now())
