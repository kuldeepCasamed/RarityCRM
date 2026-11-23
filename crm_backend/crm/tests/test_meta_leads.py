import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase, override_settings
from django_q.models import Schedule
from rest_framework.test import APIClient

from crm import meta_leads
from crm.models import Lead, LeadSource, Note
from crm.tasks import fetch_meta_lead

SETTINGS = dict(META_APP_SECRET="secret", META_LEADS_VERIFY_TOKEN="verify", META_SYSTEM_USER_TOKEN="tok", META_LEAD_FORM_IDS=["F1"])
URL = "/api/meta/leadgen-webhook/"


def graph_lead(lid="L1", phone="+918796319927"):
    return {
        "id": lid, "created_time": "2026-11-16T10:00:00+0000", "form_id": "F1", "ad_id": "A1", "ad_name": "Ad one",
        "adset_id": "S1", "adset_name": "Set one", "campaign_id": "C1", "campaign_name": "Campaign 3", "platform": "ig", "is_organic": False,
        "field_data": [
            {"name": "full_name", "values": ["Asha Verma"]}, {"name": "email", "values": ["Asha@x.com"]},
            {"name": "phone_number", "values": [phone]},
            {"name": "q_treat", "values": ["Invisalign"]}, {"name": "q_when", "values": ["within_a_month"]},
        ],
    }


def graph(path, params=None):
    if path == "F1":
        return {"questions": [{"key": "q_treat", "label": "Which treatment are you interested in?"}, {"key": "q_when", "label": "When would you like to start?"}]}
    return graph_lead(path)


def sign(body):
    return "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()


@override_settings(**SETTINGS)
class WebhookTests(TestCase):
    def setUp(self):
        meta_leads._label_cache.clear()
        self.c = APIClient()

    def test_handshake(self):
        ok = self.c.get(URL, {"hub.mode": "subscribe", "hub.verify_token": "verify", "hub.challenge": "123"})
        self.assertEqual((ok.status_code, ok.content), (200, b"123"))
        self.assertEqual(self.c.get(URL, {"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "1"}).status_code, 403)

    def post(self, payload, sig=None):
        body = json.dumps(payload).encode()
        return self.c.post(URL, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig or sign(body))

    def test_bad_signature_rejected(self):
        with mock.patch("crm.views.meta_leads.enqueue_meta_lead") as q:
            self.assertEqual(self.post({"entry": []}, sig="sha256=bad").status_code, 403)
            q.assert_not_called()

    def test_enqueues_each_leadgen(self):
        payload = {"entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": "L1"}}, {"field": "other", "value": {"leadgen_id": "X"}}]}]}
        with mock.patch("crm.views.meta_leads.enqueue_meta_lead") as q:
            self.assertEqual(self.post(payload).status_code, 200)
            q.assert_called_once_with("L1")


@override_settings(**SETTINGS)
class FetchTests(TestCase):
    def setUp(self):
        meta_leads._label_cache.clear()
        for p in (mock.patch("crm.meta_leads.graph_get", side_effect=graph), mock.patch("crm.tasks.safe_push"), mock.patch("crm.tasks.send_email")):
            self.addCleanup(p.stop)
            setattr(self, p.attribute.replace("_", "") if False else p.attribute, p.start())

    def test_creates_lead_with_answers_and_alerts_once(self):
        with mock.patch("crm.tasks.async_task") as q:
            self.assertEqual(fetch_meta_lead("L1"), mock.ANY)
            self.assertEqual(fetch_meta_lead("L1"), "already imported")  # redelivery
        q.assert_called_once()  # exactly one alert task queued
        self.assertEqual(q.call_args.args[0], "crm.tasks.run_new_lead_alert")
        lead = Lead.objects.get(meta_lead_id="L1")
        self.assertEqual((lead.source, lead.name, lead.email, lead.phone), (LeadSource.META_INSTANT_FORM, "Asha Verma", "asha@x.com", "+918796319927"))
        self.assertEqual((lead.treatment_interest, lead.start_timeframe), ("Invisalign", "within a month"))
        self.assertEqual((lead.meta_campaign_name, lead.meta_adset_name, lead.meta_ad_name, lead.meta_platform), ("Campaign 3", "Set one", "Ad one", "ig"))
        self.assertEqual(Lead.objects.filter(meta_lead_id="L1").count(), 1)
        from crm.tasks import run_new_lead_alert
        run_new_lead_alert(lead.id, False)  # what the queued task does
        self.assertEqual(self.safe_push.call_count, 1)
        self.assertTrue(self.send_email.called)

    def test_repeat_phone_is_kept_and_flagged(self):
        fetch_meta_lead("L1")
        fetch_meta_lead("L2")
        second = Lead.objects.get(meta_lead_id="L2")
        self.assertIn("repeat-enquiry", second.tags)
        self.assertTrue(Note.objects.filter(lead=second, body__startswith="Repeat enquiry").exists())

    def test_graph_error_schedules_retry_then_alerts(self):
        with mock.patch("crm.meta_leads.graph_get", side_effect=RuntimeError("boom")):
            self.assertIn("retry scheduled", fetch_meta_lead("L9"))
            self.assertEqual(Schedule.objects.filter(func="crm.tasks.fetch_meta_lead").count(), 1)
            with self.assertRaises(RuntimeError):
                fetch_meta_lead("L9", attempt=5)
        self.assertEqual(Lead.objects.count(), 0)

    def test_backfill_imports_missing_and_is_idempotent(self):
        with mock.patch("crm.meta_leads.iter_form_leads", return_value=[graph_lead("L7", "+919810012345")]):
            self.assertEqual(meta_leads.backfill(0), {"seen": 1, "created": 1})
            self.assertEqual(meta_leads.backfill(0), {"seen": 1, "created": 0})
        self.assertEqual(Lead.objects.filter(meta_lead_id="L7").count(), 1)


class StaffApiTests(TestCase):
    def test_meta_raw_hidden_and_whatsapp_source_accepted(self):
        from django.contrib.auth import get_user_model
        from crm.models import CRMUser, Role

        u = get_user_model().objects.create_user("fd", password="x")
        CRMUser.objects.create(user=u, role=Role.FRONT_DESK)
        c = APIClient()
        c.force_authenticate(u)
        r = c.post("/api/crm/leads/", {"name": "W Patient", "phone": "+919810012345", "source": "meta_whatsapp"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["source"], "meta_whatsapp")
        self.assertNotIn("meta_raw", r.json())
