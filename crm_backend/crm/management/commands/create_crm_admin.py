import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from crm.models import CRMUser, Role


class Command(BaseCommand):
    help = "Create (or update) a CRM login with a CRM profile in one step."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--role", default=Role.ADMIN, choices=[r.value for r in Role])
        parser.add_argument("--password", help="Omit to be prompted (recommended)")

    def handle(self, *args, **o):
        password = o["password"] or getpass.getpass("Password: ")
        if len(password) < 8:
            raise CommandError("Password must be at least 8 characters.")
        User = get_user_model()
        user, created = User.objects.get_or_create(username=o["username"], defaults={"email": o["email"], "first_name": o["first_name"]})
        user.set_password(password)
        if o["email"]:
            user.email = o["email"]
        if o["first_name"]:
            user.first_name = o["first_name"]
        user.save()
        CRMUser.objects.update_or_create(user=user, defaults={"role": o["role"], "is_active": True})
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} {o['username']} as {o['role']}"))
