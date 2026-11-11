import hashlib
from unittest import mock

from django.utils.module_loading import import_string
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from crm import services
from crm.meta_capi import build_user_data, send_capi_event
from crm.models import Lead, LeadStatus

CAPI = {"META_PIXEL_ID": "111", "META_CAPI_TOKEN": "tok", "META_TEST_EVENT_CODE": "TEST1"}
SYNC = {"name": "t", "orm": "default", "sync": True}


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


class UserDataTests(TestCase):
    def test_hashes_contact_keys_and_sends_browser_keys_raw(self):
        lead = Lead.objects.create(
            name="  Asha  Verma ", email=" Asha@X.com ", phone="+91 98100-12345",
            meta_fbp="fb.1.x", meta_client_ip="1.2.3.4", meta_client_user_agent="UA",
        )
        u = build_user_data(lead)
        self.assertEqual(u["em"], [sha("asha@x.com")])
        self.assertEqual(u["ph"], [sha("919810012345")])
        self.assertEqual(u["fn"], [sha("asha")])
        self.assertEqual(u["external_id"], [sha(str(lead.id))])
        self.assertEqual((u["fbp"], u["client_ip_address"], u["client_user_agent"]), ("fb.1.x", "1.2.3.4", "UA"))
        self.assertNotIn("fbc", u)


@override_settings(**CAPI, Q_CLUSTER=SYNC)
class CapiFlowTests(TransactionTestCase):  # tasks enqueue on commit
    def setUp(self):  # run queued tasks inline
        p = mock.patch("crm.tasks.async_task", lambda path, *a: import_string(path)(*a))
        p.start()
        self.addCleanup(p.stop)
    def test_payload_has_no_health_data_and_uses_test_code(self):
        lead = Lead.objects.create(name="A B", phone="+919810012345", chief_complaint="root canal", treatment_interests=["implants"])
        with mock.patch("crm.meta_capi.requests.post") as post:
            post.return_value.ok = True
            send_capi_event(lead, "Lead", "evt-1")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["test_event_code"], "TEST1")
        self.assertEqual(body["data"][0]["event_id"], "evt-1")
        self.assertNotIn("custom_data", body["data"][0])
        self.assertNotIn("root canal", str(body))
        self.assertNotIn("tok", post.call_args.args[0])  # token not in the URL

    def test_status_events_sent_once_per_lead(self):
        lead = Lead.objects.create(name="A B", phone="+919810012345")
        with mock.patch("crm.meta_capi.requests.post") as post:
            post.return_value.ok = True
            services.change_status(lead, LeadStatus.CONSULT_BOOKED)
            services.change_status(lead, LeadStatus.CONTACTED)
            services.change_status(lead, LeadStatus.CONSULT_BOOKED)
            services.change_status(lead, LeadStatus.TREATMENT_BOOKED)
            services.change_status(lead, LeadStatus.NEGOTIATING)
            services.change_status(lead, LeadStatus.TREATMENT_BOOKED)
        names = [c.kwargs["json"]["data"][0]["event_name"] for c in post.call_args_list]
        self.assertEqual(names, ["Schedule", "TreatmentBooked"])

    @override_settings(CRM_INTAKE_API_KEY="k")
    def test_intake_stores_meta_fields_and_sends_lead_with_browser_event_id(self):
        with mock.patch("crm.meta_capi.requests.post") as post, mock.patch("crm.views.public.notify_new_lead"):
            post.return_value.ok = True
            r = APIClient().post("/api/crm/interest/", {
                "firstName": "Kiran", "contactNumber": "+919810055555", "email": "k@x.com",
                "meta_event_id": "browser-evt", "meta_fbp": "fb.1.a", "meta_client_ip": "9.9.9.9",
                "meta_client_user_agent": "UA",
            }, format="json", HTTP_X_RARITY_KEY="k")
        self.assertEqual(r.status_code, 201)
        lead = Lead.objects.get(email="k@x.com")
        self.assertEqual((lead.meta_fbp, lead.meta_client_ip), ("fb.1.a", "9.9.9.9"))
        ev = post.call_args.kwargs["json"]["data"][0]
        self.assertEqual((ev["event_name"], ev["event_id"]), ("Lead", "browser-evt"))

    @override_settings(CRM_INTAKE_API_KEY="k")
    def test_intake_without_event_id_sends_no_lead(self):  # Smile Studio booking
        with mock.patch("crm.meta_capi.requests.post") as post, mock.patch("crm.views.public.notify_new_lead"):
            r = APIClient().post("/api/crm/interest/", {
                "firstName": "Kiran", "contactNumber": "+919810055555", "email": "k@x.com", "meta_fbp": "fb.1.a",
            }, format="json", HTTP_X_RARITY_KEY="k")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Lead.objects.get(email="k@x.com").meta_fbp, "fb.1.a")
        post.assert_not_called()

    @override_settings(META_CAPI_TOKEN="")
    def test_inert_without_token(self):
        lead = Lead.objects.create(name="A B", phone="+919810012345")
        with mock.patch("crm.meta_capi.requests.post") as post:
            services.change_status(lead, LeadStatus.CONSULT_BOOKED)
        post.assert_not_called()
