from django.core.management.base import BaseCommand
from django_q.models import Schedule

JOBS = [
    ("crm.tasks.run_daily_task_digest", "Daily task digest", Schedule.DAILY),
    ("crm.tasks.run_stale_leads_digest", "Stale leads digest", Schedule.DAILY),
    ("crm.tasks.run_appointment_reminders", "Appointment reminders", Schedule.HOURLY),
    ("crm.tasks.run_refresh_scores", "Refresh lead scores", Schedule.DAILY),
    ("crm.tasks.run_expire_quotes", "Expire overdue quotes", Schedule.DAILY),
    ("crm.tasks.run_sequence_tick", "Email sequences", Schedule.MINUTES),
    ("crm.tasks.run_campaign_tick", "Email campaigns", Schedule.MINUTES),
    ("crm.tasks.run_push_reminders", "Push reminders", Schedule.MINUTES),
]
MINUTES = {"Email sequences": 5, "Email campaigns": 5, "Push reminders": 5}


class Command(BaseCommand):
    help = "Create/refresh django-q2 schedules for CRM digests."

    def handle(self, *args, **opts):
        for func, name, kind in JOBS:
            defaults = {"func": func, "schedule_type": kind}
            if name in MINUTES:
                defaults["minutes"] = MINUTES[name]
            Schedule.objects.update_or_create(name=name, defaults=defaults)
            self.stdout.write(f"scheduled: {name}")
