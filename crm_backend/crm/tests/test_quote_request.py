from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from crm.models import Lead, Note, Task


@override_settings(CRM_INTAKE_API_KEY="k")
class QuoteRequestTests(TestCase):
    def setUp(self):
        cache.clear()

    def post(self, **over):
        data = {"firstName": "Quote", "lastName": "Tester", "contactNumber": "+919810055555", "email": "quote@x.com",
                "submissionUrl": "https://www.raritydental.com/request-a-quote", "quote_request": True,
                "treatment_interest": "invisalign", "message": "Cost for both arches?"}
        data.update(over)
        with mock.patch("crm.views.public.notify_new_lead") as new, mock.patch("crm.views.public.notify_meta_lead") as rep:
            r = APIClient().post("/api/crm/interest/", data, format="json", HTTP_X_RARITY_KEY="k")
        return r, new, rep

    def test_new_quote_request_creates_tagged_lead_note_and_task(self):
        r, new, rep = self.post()
        self.assertEqual(r.status_code, 201)
        lead = Lead.objects.get()
        self.assertEqual(lead.tags, ["quote-request"])
        self.assertEqual(lead.treatment_interests, ["invisalign"])
        self.assertEqual(lead.treatment_interest, "Invisalign / Aligners")
        self.assertIn("Cost for both arches?", lead.chief_complaint)
        self.assertIn("Quote request", Note.objects.get(lead=lead).body)
        t = Task.objects.get(lead=lead)
        self.assertEqual((t.title, t.priority), ("Send quote to Quote Tester", "high"))
        new.assert_called_once_with(lead.id)
        rep.assert_not_called()

    def test_unknown_treatment_becomes_other(self):
        self.post(treatment_interest="<script>")
        self.assertEqual(Lead.objects.get().treatment_interests, ["other"])

    def test_repeat_patient_still_gets_note_task_and_alert_without_losing_data(self):
        self.post(quote_request=False, treatment_interest="")
        lead = Lead.objects.get()
        lead.tags = ["vip"]
        lead.save()
        r, new, rep = self.post()
        self.assertEqual(Lead.objects.count(), 1)
        lead.refresh_from_db()
        self.assertEqual(lead.tags, ["quote-request", "vip"])
        self.assertEqual(Task.objects.filter(lead=lead).count(), 1)
        self.assertEqual(Note.objects.filter(lead=lead).count(), 1)
        rep.assert_called_once_with(lead.id, repeat=True)
        new.assert_not_called()

    def test_plain_form_is_unchanged(self):
        self.post(quote_request=False)
        lead = Lead.objects.get()
        self.assertEqual(lead.tags, [])
        self.assertEqual(Task.objects.count(), 0)

    def test_honeypot_creates_nothing(self):
        self.post(website="spam")
        self.assertEqual(Lead.objects.count(), 0)
