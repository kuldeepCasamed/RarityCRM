"""Create the FIRST admin from environment variables, for hosts with no shell (e.g. Render free plan).

Runs during every build, but is deliberately inert unless ALL of these hold:
  * CRM_BOOTSTRAP_ADMIN_USERNAME and CRM_BOOTSTRAP_ADMIN_PASSWORD are set, and
  * no active CRM admin exists yet.
It never changes an existing user's password. After the first login, delete the password variable."""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from crm.models import CRMUser, Role


class Command(BaseCommand):
    help = "Create the first CRM admin from CRM_BOOTSTRAP_ADMIN_* env vars (no-op if an admin already exists)."

    def handle(self, *args, **opts):
        username = os.environ.get("CRM_BOOTSTRAP_ADMIN_USERNAME", "").strip()
        password = os.environ.get("CRM_BOOTSTRAP_ADMIN_PASSWORD", "")
        if not username or not password:
            self.stdout.write("bootstrap_admin: not configured, skipping")
            return
        if CRMUser.objects.filter(role=Role.ADMIN, is_active=True).exists():
            self.stdout.write("bootstrap_admin: an admin already exists, nothing to do "
                              "(you can now remove CRM_BOOTSTRAP_ADMIN_PASSWORD)")
            return
        if len(password) < 12:
            self.stderr.write("bootstrap_admin: password must be at least 12 characters, skipping")
            return
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": os.environ.get("CRM_BOOTSTRAP_ADMIN_EMAIL", ""),
                                         "first_name": os.environ.get("CRM_BOOTSTRAP_ADMIN_NAME", "")})
        if created:
            user.set_password(password)
            user.save()
        # an existing non-CRM user keeps their password; they just gain the admin profile
        CRMUser.objects.update_or_create(user=user, defaults={"role": Role.ADMIN, "is_active": True})
        self.stdout.write(self.style.SUCCESS(f"bootstrap_admin: admin '{username}' ready. Remove CRM_BOOTSTRAP_ADMIN_PASSWORD now."))
