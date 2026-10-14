import django_filters
from django.db.models import Q

from .models import EmailThread, Lead


class LeadFilter(django_filters.FilterSet):
    """Single source of truth for lead filtering — used by list AND export."""

    q = django_filters.CharFilter(method="search")
    assigned_to = django_filters.CharFilter(method="by_assignee")
    treatment = django_filters.CharFilter(method="by_treatment")
    tag = django_filters.CharFilter(method="by_tag")
    created_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    band = django_filters.CharFilter(method="by_band")
    needs_reply = django_filters.BooleanFilter(method="by_needs_reply")
    score_min = django_filters.NumberFilter(field_name="score", lookup_expr="gte")
    status = django_filters.BaseInFilter(field_name="status")
    source = django_filters.BaseInFilter(field_name="source")

    class Meta:
        model = Lead
        fields = ["priority", "is_international", "country"]

    def search(self, qs, name, value):
        return qs.filter(Q(name__icontains=value) | Q(email__icontains=value) | Q(phone__icontains=value))

    def by_assignee(self, qs, name, value):
        if value == "me":
            profile = getattr(self.request.user, "crm_profile", None)
            return qs.filter(assigned_to=profile)
        if value == "unassigned":
            return qs.filter(assigned_to__isnull=True)
        return qs.filter(assigned_to_id=value) if value.isdigit() else qs

    def by_needs_reply(self, qs, name, value):
        ids = EmailThread.objects.filter(needs_reply=True, lead__isnull=False).values("lead_id")
        return qs.filter(pk__in=ids) if value else qs.exclude(pk__in=ids)

    def by_band(self, qs, name, value):
        from .scoring import HOT, WARM

        return {"hot": qs.filter(score__gte=HOT), "warm": qs.filter(score__gte=WARM, score__lt=HOT),
                "cold": qs.filter(score__lt=WARM)}.get(value, qs)

    def by_tag(self, qs, name, value):
        return qs.filter(tags__contains=[value])

    def by_treatment(self, qs, name, value):
        return qs.filter(treatment_interests__contains=[value])


def filter_leads(request, qs=None):
    qs = Lead.objects.alive().select_related("assigned_to__user") if qs is None else qs
    return LeadFilter(request.query_params, queryset=qs, request=request).qs
