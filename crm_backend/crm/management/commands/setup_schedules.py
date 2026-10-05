from django.core.management.base import BaseCommand
from django_q.models import Schedule

JOBS = [
    ("crm.tasks.run_daily_task_digest", "Daily task digest", Schedule.DAILY),
    ("crm.tasks.run_stale_leads_digest", "Stale leads digest", Schedule.DAILY),
    ("crm.tasks.run_appointment_reminders", "Appointment reminders", Schedule.HOURLY),
    ("crm.tasks.run_refresh_scores", "Refresh lead scores", Schedule.DAILY),
    ("crm.tasks.run_expire_quotes", "Expire overdue quotes", Schedule.DAILY),
]


class Command(BaseCommand):
    help = "Create/refresh django-q2 schedules for CRM digests."

    def handle(self, *args, **opts):
        for func, name, kind in JOBS:
            Schedule.objects.update_or_create(name=name, defaults={"func": func, "schedule_type": kind})
            self.stdout.write(f"scheduled: {name}")
