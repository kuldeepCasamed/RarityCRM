from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Min, Q
from django.db.models.functions import TruncWeek
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Appointment, Lead, LeadStatus, Task
from ..permissions import IsCRMUser, crm_profile
from ..scoring import DEAD, HOT


def _range(request):
    days = int(request.query_params.get("days", 30))
    return timezone.now() - timedelta(days=days)


class DashboardView(APIView):
    """KPIs + funnel + sources + today's work in one call."""

    def get(self, request):
        since = _range(request)
        leads = Lead.objects.alive()
        recent = leads.filter(created_at__gte=since)
        profile = crm_profile(request)
        now = timezone.now()
        end_today = timezone.localtime(now).replace(hour=23, minute=59, second=59)

        total = recent.count()
        converted = recent.filter(status=LeadStatus.CONVERTED).count()
        return Response({
            "kpis": {
                "new_leads": total,
                "converted": converted,
                "conversion_rate": round(converted / total * 100, 1) if total else 0,
                "unassigned": leads.filter(assigned_to__isnull=True, status=LeadStatus.NEW).count(),
                "my_open_tasks": Task.objects.filter(assigned_to=profile, is_done=False).count(),
                "my_overdue_tasks": Task.objects.filter(assigned_to=profile, is_done=False, due_at__lt=now).count(),
            },
            "funnel": list(recent.values("status").annotate(count=Count("id")).order_by("-count")),
            "sources": list(recent.values("source").annotate(count=Count("id")).order_by("-count")),
            "weekly": list(
                recent.annotate(week=TruncWeek("created_at")).values("week")
                .annotate(leads=Count("id"), converted=Count("id", filter=Q(status=LeadStatus.CONVERTED)))
                .order_by("week")
            ),
            "hot_leads": [
                {"id": l.id, "name": l.name, "score": l.score, "status": l.status,
                 "assigned_to_name": l.assigned_to.user.get_full_name() if l.assigned_to else None}
                for l in leads.filter(score__gte=HOT).exclude(status__in=[LeadStatus.CONVERTED, *DEAD])
                .select_related("assigned_to__user").order_by("-score")[:8]
            ],
            "today_appointments": Appointment.objects.filter(start_at__gte=now.replace(hour=0, minute=0), start_at__lte=end_today)
                .exclude(status="cancelled").count(),
        })


class RepPerformanceView(APIView):
    def get(self, request):
        since = _range(request)
        rows = (
            Lead.objects.alive().filter(created_at__gte=since, assigned_to__isnull=False)
            .values("assigned_to", "assigned_to__user__first_name", "assigned_to__user__last_name")
            .annotate(
                leads=Count("id"),
                converted=Count("id", filter=Q(status=LeadStatus.CONVERTED)),
                contacts=Count("contacts"),
            )
            .order_by("-converted")
        )
        return Response(list(rows))


class ResponseTimeView(APIView):
    """Average time from lead creation to first logged contact."""

    def get(self, request):
        since = _range(request)
        qs = (
            Lead.objects.alive().filter(created_at__gte=since, contacts__isnull=False)
            .annotate(first=Min("contacts__contacted_at"))
            .annotate(delta=ExpressionWrapper(F("first") - F("created_at"), output_field=DurationField()))
        )
        avg = qs.aggregate(avg=Avg("delta"))["avg"]
        return Response({
            "contacted_leads": qs.count(),
            "avg_response_minutes": round(avg.total_seconds() / 60, 1) if avg else None,
        })
