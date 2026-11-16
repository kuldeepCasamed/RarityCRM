from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from crm import meta_leads


class Command(BaseCommand):
    help = "Import Meta instant-form leads missed by the webhook (Meta keeps them ~90 days). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="YYYY-MM-DD (UTC)")

    def handle(self, *args, since, **opts):
        if not meta_leads.enabled() or not settings.META_LEAD_FORM_IDS:
            raise CommandError("Set META_SYSTEM_USER_TOKEN and META_LEAD_FORM_IDS first.")
        try:
            ts = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            raise CommandError("--since must be YYYY-MM-DD")
        result = meta_leads.backfill(ts)
        self.stdout.write(f"seen {result['seen']}, created {result['created']}")
