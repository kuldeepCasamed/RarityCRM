import json
from datetime import date

from django.core.cache import cache
from django.test import TestCase

from crm.models import ContactClick, Lead

URL = "/api/public/contact-click/"


class ContactClickTests(TestCase):
    def setUp(self):
        cache.clear()

    def post(self, data, ip="1.1.1.1", ctype="text/plain", **extra):
        body = data if isinstance(data, str) else json.dumps(data)
        return self.client.post(URL, body, content_type=ctype, REMOTE_ADDR=ip, **extra)

    def base(self, **kw):
        return {"channel": "whatsapp", "page_path": "/contact-us?x=1", "session_id": "s1", "button_location": "floating", **kw}

    def test_meta_click_is_stored_with_campaign(self):
        r = self.post(self.base(utm_source="meta", utm_campaign="TEST_CAMPAIGN", utm_term="TEST_ADSET", utm_content="TEST_AD", landing_page="/?utm_source=meta"))
        self.assertEqual(r.status_code, 204)
        c = ContactClick.objects.get()
        self.assertEqual((c.channel, c.source, c.utm_campaign, c.utm_term, c.utm_content), ("whatsapp", "meta_ads", "TEST_CAMPAIGN", "TEST_ADSET", "TEST_AD"))
        self.assertEqual(c.page_path, "/contact-us")  # query stripped
        self.assertEqual(c.landing_page, "/")
        self.assertEqual(c.button_location, "floating")

    def test_no_utm_is_website_and_json_content_type_works(self):
        self.post(self.base(channel="call"), ctype="application/json")
        c = ContactClick.objects.get()
        self.assertEqual((c.source, c.utm_campaign, c.channel), ("website", None, "call"))

    def test_google_source(self):
        self.post(self.base(utm_source="google"))
        self.assertEqual(ContactClick.objects.get().source, "google_ads")

    def test_double_tap_recorded_once_but_other_channel_counts(self):
        self.post(self.base())
        self.post(self.base())
        self.assertEqual(ContactClick.objects.count(), 1)
        self.post(self.base(channel="call"))
        self.assertEqual(ContactClick.objects.count(), 2)

    def test_invalid_payloads_still_204_and_store_nothing(self):
        for bad in ("not json", "[]", json.dumps({"channel": "sms"}), ""):
            self.assertEqual(self.post(bad).status_code, 204)
        self.assertEqual(ContactClick.objects.count(), 0)

    def test_truncation_unknown_fields_and_pii_dropped(self):
        self.post(self.base(utm_campaign="x" * 500, phone="+919418273650", email="a@b.com", utm_content="call me on +91 94182 73650", utm_term="a@b.com"))
        c = ContactClick.objects.get()
        self.assertEqual(len(c.utm_campaign), 200)
        self.assertIsNone(c.utm_content)
        self.assertIsNone(c.utm_term)
        self.assertFalse(hasattr(c, "phone"))
        self.assertFalse(hasattr(c, "ip"))

    def test_rate_limit_per_ip(self):
        for i in range(35):
            self.post(self.base(session_id=f"s{i}"), ip="9.9.9.9")
        self.assertEqual(ContactClick.objects.count(), 30)
        self.post(self.base(session_id="other"), ip="8.8.8.8")
        self.assertEqual(ContactClick.objects.count(), 31)

    def test_cors_only_for_site_origins(self):
        ok = self.post(self.base(), HTTP_ORIGIN="https://www.raritydental.com")
        self.assertEqual(ok["Access-Control-Allow-Origin"], "https://www.raritydental.com")
        bad = self.post(self.base(session_id="z"), HTTP_ORIGIN="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", bad)

    def test_get_is_204_and_stores_nothing(self):
        self.assertEqual(self.client.get(URL).status_code, 204)
        self.assertEqual(ContactClick.objects.count(), 0)

    def test_click_never_creates_lead(self):
        self.post(self.base())
        self.assertEqual(Lead.objects.count(), 0)

    def test_report_requires_login(self):
        self.assertIn(self.client.get("/api/crm/analytics/contact-clicks/").status_code, (401, 403))
