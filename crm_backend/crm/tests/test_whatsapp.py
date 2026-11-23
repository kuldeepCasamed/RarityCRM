import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from crm import whatsapp
from crm.models import Lead, LeadSource, WhatsAppConnection, WhatsAppEvent, WhatsAppMessage
from crm.tasks import process_whatsapp_change

URL = "/api/meta/whatsapp-webhook/"
SETTINGS = dict(META_APP_SECRET="secret", META_WA_VERIFY_TOKEN="wverify", META_SYSTEM_USER_TOKEN="tok")
PNID = "PN1"


def sign(body):
    return "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()


def inbound(mid="wamid.1", wa="919876543210", text="hi", referral=None, pnid=PNID, ts="1763900000"):
    msg = {"from": wa, "id": mid, "timestamp": ts, "type": "text", "text": {"body": text}}
    if referral:
        msg["referral"] = referral
    return {"metadata": {"phone_number_id": pnid}, "contacts": [{"wa_id": wa, "profile": {"name": "Friend"}}], "messages": [msg]}


AD = {"source_type": "ad", "source_id": "AD1", "headline": "Smile Proof", "ctwa_clid": "CLID", "source_url": "https://fb.me/x"}


@override_settings(**SETTINGS)
class WebhookTests(TestCase):
    def post(self, payload, sig=True):
        body = json.dumps(payload).encode()
        return APIClient().generic("POST", URL, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body) if sig else "sha256=bad")

    def test_handshake(self):
        c = APIClient()
        self.assertEqual(c.get(URL, {"hub.mode": "subscribe", "hub.verify_token": "wverify", "hub.challenge": "42"}).content, b"42")
        self.assertEqual(c.get(URL, {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}).status_code, 403)

    def test_bad_signature(self):
        self.assertEqual(self.post({"object": "whatsapp_business_account"}, sig=False).status_code, 403)

    def test_queues_known_fields_only(self):
        payload = {"object": "whatsapp_business_account", "entry": [{"changes": [
            {"field": "messages", "value": inbound()}, {"field": "something_else", "value": {}}]}]}
        with mock.patch("crm.views.whatsapp.async_task") as q:
            self.assertEqual(self.post(payload).status_code, 200)
        q.assert_called_once()
        self.assertEqual(q.call_args.args[:2], ("crm.tasks.process_whatsapp_change", "messages"))


@override_settings(**SETTINGS)
class ProcessTests(TestCase):
    def setUp(self):
        WhatsAppConnection.objects.create(waba_id="W1", phone_number_id=PNID)
        p = mock.patch("crm.whatsapp.resolve_ad", return_value={"ad": "Ad one", "adset": "Set one", "campaign": "RD | 2 Consideration"})
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch("crm.tasks.async_task")
        self.alerts = q.start()
        self.addCleanup(q.stop)

    def run_change(self, field, value):
        return process_whatsapp_change(field, value)

    def test_ad_chat_creates_attributed_lead_and_alerts(self):
        self.run_change("messages", inbound(referral=AD))
        lead = Lead.objects.get()
        self.assertEqual((lead.source, lead.name, lead.phone), (LeadSource.META_WHATSAPP, "Friend", "+919876543210"))
        self.assertEqual((lead.meta_ad_id, lead.meta_ctwa_clid, lead.meta_ad_headline), ("AD1", "CLID", "Smile Proof"))
        self.assertEqual((lead.meta_ad_name, lead.meta_adset_name, lead.meta_campaign_name), ("Ad one", "Set one", "RD | 2 Consideration"))
        self.assertEqual((lead.utm_campaign, lead.utm_term, lead.utm_content), ("RD | 2 Consideration", "Set one", "Ad one"))
        self.assertEqual(lead.first_message, "hi")
        self.assertEqual(WhatsAppMessage.objects.get().lead, lead)
        self.alerts.assert_called_once()
        self.assertEqual(self.alerts.call_args.args[0], "crm.tasks.run_new_lead_alert")

    def test_organic_chat_source_whatsapp(self):
        self.run_change("messages", inbound())
        self.assertEqual(Lead.objects.get().source, LeadSource.WHATSAPP)

    def test_second_message_attaches_no_duplicate_no_alert(self):
        self.run_change("messages", inbound())
        self.alerts.reset_mock()
        self.run_change("messages", inbound(mid="wamid.2", text="again"))
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(WhatsAppMessage.objects.count(), 2)
        self.alerts.assert_not_called()
        self.assertIsNotNone(Lead.objects.get().last_whatsapp_at)

    def test_duplicate_delivery(self):
        self.run_change("messages", inbound())
        self.run_change("messages", inbound())
        self.assertEqual((Lead.objects.count(), WhatsAppMessage.objects.count()), (1, 1))

    def test_user_id_in_from_falls_back_to_contact_wa_id(self):
        v = inbound()
        v["messages"][0]["from"] = "US.13491208655302741918"
        self.run_change("messages", v)
        self.assertEqual(Lead.objects.get().phone, "+919876543210")

    def test_no_usable_phone_skipped(self):
        v = inbound()
        v["messages"][0]["from"] = "US.1349"
        v["contacts"] = [{"wa_id": "US.1349", "profile": {"name": "X"}}]
        self.run_change("messages", v)
        self.assertEqual((Lead.objects.count(), WhatsAppMessage.objects.count()), (0, 0))

    def test_other_number_ignored(self):
        self.run_change("messages", inbound(pnid="HAPTIK"))
        self.assertEqual(Lead.objects.count(), 0)

    def test_echo_sets_first_response(self):
        self.run_change("messages", inbound(ts="1763900000"))
        echo = {"metadata": {"phone_number_id": PNID}, "message_echoes": [{"from": "918796319927", "to": "919876543210", "id": "wamid.out1", "timestamp": "1763900300", "type": "text", "text": {"body": "hello"}}]}
        self.run_change("smb_message_echoes", echo)
        lead = Lead.objects.get()
        self.assertIsNotNone(lead.first_response_at)
        self.assertEqual(WhatsAppMessage.objects.get(wa_message_id="wamid.out1").direction, "out")
        self.run_change("smb_message_echoes", echo)  # duplicate
        self.assertEqual(WhatsAppMessage.objects.filter(direction="out").count(), 1)

    def test_history_creates_no_leads_or_alerts(self):
        hist = {"metadata": {"phone_number_id": PNID}, "history": [{"threads": [{"id": "919811111111", "messages": [
            {"from": "919811111111", "id": "h1", "timestamp": "1700000000", "type": "text", "text": {"body": "old"}},
            {"from": "918796319927", "id": "h2", "timestamp": "1700000100", "type": "text", "text": {"body": "reply"}}]}]}]}
        self.run_change("history", hist)
        self.assertEqual(Lead.objects.count(), 0)
        self.assertEqual(sorted(WhatsAppMessage.objects.values_list("direction", "is_history")), [("in", True), ("out", True)])
        self.alerts.assert_not_called()

    def test_events_logged_without_content(self):
        self.run_change("messages", inbound())
        self.assertEqual(WhatsAppEvent.objects.get().field, "messages")

    def test_staff_thread_endpoint(self):
        from crm.tests.test_api import make_user
        from crm.models import Role

        self.run_change("messages", inbound())
        lead = Lead.objects.get()
        c = APIClient()
        c.force_authenticate(make_user("boss", Role.MANAGER).user)
        res = c.get(f"/api/crm/leads/{lead.id}/whatsapp/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["messages"][0]["text"], "hi")
