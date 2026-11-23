from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from crm.models import ContactClick, Lead, clean_utm


class UtmDecodeTests(TestCase):
    def test_clean_utm(self):
        self.assertEqual(clean_utm("RD+%7C+2+Consideration"), "RD | 2 Consideration")
        self.assertEqual(clean_utm("RD%2520x"), "RD x")  # double-encoded
        self.assertEqual(clean_utm("plain"), "plain")
        self.assertEqual(clean_utm(None), None)

    def test_lead_and_click_decoded_on_save(self):
        lead = Lead.objects.create(name="A", phone="+919000000001", utm_campaign="RD+%7C+2")
        click = ContactClick.objects.create(channel="call", utm_campaign="RD+%7C+2")
        lead.refresh_from_db()
        click.refresh_from_db()
        self.assertEqual(lead.utm_campaign, "RD | 2")
        self.assertEqual(click.utm_campaign, "RD | 2")


class CheckCommandTests(TestCase):
    def test_requires_config(self):
        with override_settings(META_APP_SECRET="", META_SYSTEM_USER_TOKEN=""):
            with self.assertRaises(CommandError):
                call_command("meta_leads_check")

    @override_settings(META_APP_ID="1", META_APP_SECRET="s", META_SYSTEM_USER_TOKEN="tok", META_LEADS_VERIFY_TOKEN="v", META_PAGE_ID="P")
    def test_runs_without_printing_secrets(self):
        def fake_get(self_, path, params=None, token=None):
            if path == "debug_token":
                return True, {"data": {"is_valid": True, "type": "SYSTEM_USER", "expires_at": 0, "scopes": ["leads_retrieval"], "application": "App"}}
            if path == "P":
                return True, {"name": "Rarity", "access_token": "PAGETOKEN"}
            if path.endswith("leadgen_forms"):
                return True, {"data": [{"id": "F1", "name": "Form", "status": "ACTIVE"}]}
            return True, {"data": [{"id": "1", "subscribed_fields": ["leadgen"]}]}
        out = StringIO()
        with mock.patch("crm.management.commands.meta_leads_check.Command._get", fake_get):
            call_command("meta_leads_check", stdout=out)
        text = out.getvalue()
        self.assertIn("F1", text)
        self.assertIn("subscribed to leadgen: True", text)
        self.assertNotIn("PAGETOKEN", text)
        self.assertNotIn("tok", text.replace("token", ""))
