from django.core.management.base import BaseCommand

from crm.models import Lead
from crm.scoring import rescore


class Command(BaseCommand):
    help = "Recompute the score of every lead (including closed ones). Run once after deploying scoring."

    def handle(self, *args, **opts):
        n = 0
        for lead in Lead.objects.alive().prefetch_related("contacts", "appointments").iterator(chunk_size=200):
            rescore(lead)
            n += 1
        self.stdout.write(f"rescored {n} leads")
