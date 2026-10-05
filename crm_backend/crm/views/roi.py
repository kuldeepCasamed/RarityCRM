import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Lead, LeadSource, LeadStatus, MarketingSpend
from ..permissions import IsCRMManager, crm_profile
from ..scoring import HOT
from ..serializers import MarketingSpendSerializer

SOURCES = {v for v, _ in LeadSource.choices}
LABELS = dict(LeadSource.choices)


def _num(x):
    return float(x) if x is not None else None


def _div(a, b):
    return round(a / b, 2) if b else None


def parse_range(params):
    try:
        if params.get("from") and params.get("to"):
            start, end = date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
        else:
            end = timezone.localdate()
            start = end - timedelta(days=int(params.get("days", 30)) - 1)
    except ValueError:
        raise ValidationError("Use ?days=N or ?from=YYYY-MM-DD&to=YYYY-MM-DD")
    if start > end:
        raise ValidationError("'from' must not be after 'to'")
    return start, end


def metrics(row):
    """Derive cost + return metrics. Anything undefined is None (never 0/inf): cost metrics need recorded
    spend (no spend entered != free), ratios need a non-zero denominator."""
    spend, revenue = row["spend"], row["revenue"]
    has_spend = spend > 0
    row["cost_per_lead"] = _div(spend, row["leads"]) if has_spend else None
    row["cost_per_consult"] = _div(spend, row["consults"]) if has_spend else None
    row["cost_per_conversion"] = _div(spend, row["converted"]) if has_spend else None
    row["conversion_rate"] = round(row["converted"] / row["leads"] * 100, 1) if row["leads"] else None
    row["roas"] = _div(revenue, spend)
    row["roi_pct"] = round((revenue - spend) / spend * 100, 1) if spend else None
    return row


class SourceROIView(APIView):
    """Cohort view: leads *created* in the range, their conversions/revenue, vs spend *dated* in the range.
    Recent leads haven't had time to convert, so short ranges understate ROI."""

    permission_classes = [IsCRMManager]

    def get(self, request):
        start, end = parse_range(request.query_params)
        by_campaign = request.query_params.get("group_by") == "campaign"

        leads = Lead.objects.alive().filter(created_at__date__range=(start, end))
        lead_keys = ["source", "utm_campaign"] if by_campaign else ["source"]
        agg = (
            leads.values(*lead_keys)
            .annotate(
                leads=Count("id"),
                contacted=Count("id", filter=Q(last_contacted_at__isnull=False)),
                consults=Count("id", filter=Q(appointments__isnull=False), distinct=True),
                converted=Count("id", filter=Q(status=LeadStatus.CONVERTED)),
                revenue=Sum("converted_value", filter=Q(status=LeadStatus.CONVERTED)),
                avg_score=Avg("score"),
                hot=Count("id", filter=Q(score__gte=HOT)),
            )
        )

        def key(source, campaign=""):
            return (source, (campaign or "").strip().lower()) if by_campaign else (source,)

        rows: dict[tuple, dict] = {}

        def blank(source, campaign):
            return {"source": source, "source_label": LABELS.get(source, source), "campaign": (campaign or "").strip(),
                    "leads": 0, "contacted": 0, "consults": 0, "converted": 0, "revenue": 0.0, "avg_score": None,
                    "hot": 0, "spend": 0.0}

        for a in agg:
            r = rows.setdefault(key(a["source"], a.get("utm_campaign")), blank(a["source"], a.get("utm_campaign")))
            r.update(leads=a["leads"], contacted=a["contacted"], consults=a["consults"], converted=a["converted"],
                     revenue=_num(a["revenue"]) or 0.0, hot=a["hot"],
                     avg_score=round(a["avg_score"], 1) if a["avg_score"] is not None else None)

        spend_keys = ["source", "campaign"] if by_campaign else ["source"]
        for s in MarketingSpend.objects.filter(date__range=(start, end)).values(*spend_keys).annotate(total=Sum("amount")):
            r = rows.setdefault(key(s["source"], s.get("campaign")), blank(s["source"], s.get("campaign")))
            r["spend"] += _num(s["total"])
            if by_campaign and not r["campaign"]:
                r["campaign"] = (s.get("campaign") or "").strip()

        out = [metrics(r) for r in rows.values()]
        out.sort(key=lambda r: (-r["spend"], -r["leads"]))

        total = {"source": "total", "source_label": "Total", "campaign": "", "avg_score": None, "spend": 0.0, "revenue": 0.0,
                 "leads": 0, "contacted": 0, "consults": 0, "converted": 0, "hot": 0}
        for r in out:
            for k in ("spend", "revenue", "leads", "contacted", "consults", "converted", "hot"):
                total[k] += r[k]
        return Response({
            "from": start, "to": end, "group_by": "campaign" if by_campaign else "source",
            "rows": out, "total": metrics(total),
            "untracked_spend_note": None if not by_campaign else "Spend rows without a campaign appear under a blank campaign.",
        })


class MarketingSpendViewSet(viewsets.ModelViewSet):
    serializer_class = MarketingSpendSerializer
    permission_classes = [IsCRMManager]

    def get_queryset(self):
        qs = MarketingSpend.objects.all()
        p = self.request.query_params
        if p.get("from"):
            qs = qs.filter(date__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(date__lte=p["to"])
        if p.get("source"):
            qs = qs.filter(source=p["source"])
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=crm_profile(self.request))


class SpendImportView(APIView):
    """CSV columns: date (YYYY-MM-DD), source, amount, campaign (optional), notes (optional)."""

    permission_classes = [IsCRMManager]

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)
        if f.size > 1024 * 1024:
            return Response({"detail": "File too large (max 1 MB)."}, status=400)
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return Response({"detail": "File must be UTF-8 encoded CSV."}, status=400)
        reader = csv.DictReader(text.splitlines())
        reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        if not {"date", "source", "amount"} <= set(reader.fieldnames):
            return Response({"detail": "CSV needs date, source and amount columns."}, status=400)

        profile, good, errors = crm_profile(request), [], []
        for i, row in enumerate(reader, start=2):
            error = None
            raw_date, raw_source = (row.get("date") or "").strip(), (row.get("source") or "").strip()
            source = raw_source.lower().replace(" ", "_")
            try:
                d = date.fromisoformat(raw_date)
            except ValueError:
                d, error = None, f"invalid date '{raw_date}' (use YYYY-MM-DD)"
            if not error and source not in SOURCES:
                error = f"unknown source '{raw_source}'"
            if not error:
                try:
                    amount = Decimal((row.get("amount") or "").replace(",", "").replace("₹", "").strip())
                    if amount <= 0:
                        error = "amount must be greater than 0"
                except InvalidOperation:
                    error = f"invalid amount '{row.get('amount')}'"
            if error:
                errors.append({"row": i, "error": error})
                continue
            good.append(MarketingSpend(date=d, source=source, amount=amount, campaign=(row.get("campaign") or "").strip(),
                                       notes=(row.get("notes") or "").strip()[:255], created_by=profile))
        MarketingSpend.objects.bulk_create(good)  # valid rows are kept; bad rows are reported back
        return Response({"created": len(good), "errors": errors})
