from django.conf import settings
from django.core.management.base import BaseCommand

from crm.utils.email_service import manager_emails, render, send_email


class Command(BaseCommand):
    help = "Send a test email to the configured CRM recipients to verify the channel."

    def handle(self, *args, **opts):
        to = manager_emails()
        self.stdout.write(f"RESEND_API_KEY set: {bool(settings.RESEND_API_KEY)}; recipients: {to}")
        self.stdout.write(send_email(to, "Rarity CRM test", render("Notification test", [("Status", "OK")])))
