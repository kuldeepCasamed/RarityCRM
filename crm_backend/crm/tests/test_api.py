from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from crm.models import CRMUser, Lead, LeadStatus, Role, StatusHistory
from crm.services import normalize_phone

User = get_user_model()
KEY = "test-key"


def make_user(name, role=Role.REP):
    u = User.objects.create_user(name, f"{name}@x.com", "pass12345", first_name=name)
    return CRMUser.objects.create(user=u, role=role)


@override_settings(CRM_INTAKE_API_KEY=KEY, Q_CLUSTER={"name": "t", "orm": "default", "sync": True})
class CRMTests(APITestCase):
    def setUp(self):
        self.manager = make_user("boss", Role.MANAGER)
        self.rep1 = make_user("rep1")
        self.rep2 = make_user("rep2")

    def auth(self, profile):
        c = APIClient()
        c.force_authenticate(profile.user)
        return c

    def intake(self, **over):
        data = {"firstName": "Asha", "lastName": "K", "email": "asha@x.com", "contactNumber": "+919810012345",
                "country": "India", "submissionUrl": "https://raritydental.com/invisalign"}
        data.update(over)
        return APIClient().post("/api/crm/interest/", data, format="json", HTTP_X_RARITY_KEY=KEY)

    def test_phone_normalisation(self):
        self.assertEqual(normalize_phone("98100 12345"), "+919810012345")
        self.assertEqual(normalize_phone("garbage"), "")

    def test_intake_requires_key(self):
        r = APIClient().post("/api/crm/interest/", {}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_intake_creates_lead_with_history_and_assignment(self):
        r = self.intake()
        self.assertEqual(r.status_code, 201)
        lead = Lead.objects.get()
        self.assertEqual(lead.phone, "+919810012345")
        self.assertIsNotNone(lead.assigned_to)
        self.assertEqual(StatusHistory.objects.filter(lead=lead).count(), 1)

    def test_intake_dedupes_and_hides_it(self):
        self.intake()
        r = self.intake(email="other@x.com", contactNumber="098100 12345")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Lead.objects.count(), 1)

    def test_attribution_is_read_from_landing_url_when_fields_missing(self):
        self.intake(submissionUrl="https://raritydental.com/invisalign?utm_source=google&utm_campaign=Invis-Q4&gclid=abc123")
        lead = Lead.objects.get()
        self.assertEqual((lead.utm_campaign, lead.gclid, lead.source), ("Invis-Q4", "abc123", "google_ads"))

    def test_explicit_fields_beat_url_params(self):
        self.intake(submissionUrl="https://raritydental.com/?utm_campaign=fromurl", utm_campaign="explicit")
        self.assertEqual(Lead.objects.get().utm_campaign, "explicit")

    def test_other_site_forms_send_nulls_for_missing_fields(self):
        """connect-form-2 / gallery forms have no email or country field: the lead must still be created."""
        r = APIClient().post("/api/crm/interest/", {"firstName": "Kiran", "lastName": "S", "contactNumber": "+919810055555", "email": None,
                             "country": None, "submissionUrl": "https://raritydental.com/blog/x"}, format="json", HTTP_X_RARITY_KEY=KEY)
        self.assertEqual(r.status_code, 201, r.content)
        lead = Lead.objects.get()
        self.assertEqual((lead.name, lead.email, lead.country, lead.source), ("Kiran S", "", "", "website"))

    def test_smile_studio_request_creates_note_task_and_tags(self):
        r = self.intake(smile_studio_reference="RD-SMILE-1234", treatment_interest="veneers", consultation_type="virtual",
                        preferred_date="2026-10-20", preferred_time="10:30", design_summary="Veneers · A1 · 8 teeth",
                        message="Wedding in Jan", international=True, country=None)
        self.assertEqual(r.status_code, 201, r.content)
        lead = Lead.objects.get()
        self.assertEqual(lead.tags, ["smile-studio"])
        self.assertEqual(lead.treatment_interests, ["smile_design"])
        self.assertTrue(lead.is_international)
        self.assertIn("RD-SMILE-1234", lead.notes.get().body)
        self.assertIn("NOT confirmed", lead.notes.get().body)
        self.assertEqual(lead.tasks.get().priority, "high")

    def test_smile_studio_request_on_existing_patient_still_makes_task(self):
        self.intake()
        self.intake(smile_studio_reference="RD-SMILE-9999", design_summary="Aligners")
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(Lead.objects.get().tasks.count(), 1)

    def test_smile_studio_unlock_without_slot(self):
        r = self.intake(smile_studio_reference="RD-SMILE-5555", treatment_interest="aligners", design_summary="Aligners")
        self.assertEqual(r.status_code, 201, r.content)
        lead = Lead.objects.get()
        self.assertIn("no consultation requested", lead.notes.get().body)
        self.assertNotIn("NOT confirmed", lead.notes.get().body)
        self.assertEqual(lead.tasks.get().title, "Call Smile Studio lead (design unlocked)")

    def test_honeypot(self):
        self.intake(website="spam")
        self.assertEqual(Lead.objects.count(), 0)

    def test_round_robin_spreads_load(self):
        self.intake(email="a@x.com", contactNumber="+919810000001")
        self.intake(email="b@x.com", contactNumber="+919810000002")
        assignees = set(Lead.objects.values_list("assigned_to", flat=True))
        self.assertEqual(len(assignees), 2)

    def test_status_change_records_history_and_conversion(self):
        self.intake()
        lead = Lead.objects.get()
        r = self.auth(self.manager).patch(f"/api/crm/leads/{lead.id}/pipeline/", {"status": "converted"}, format="json")
        self.assertEqual(r.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.CONVERTED)
        self.assertIsNotNone(lead.converted_at)
        self.assertEqual(lead.status_history.count(), 2)

    def test_profile_patch_cannot_change_status(self):
        self.intake()
        lead = Lead.objects.get()
        self.auth(self.rep1).patch(f"/api/crm/leads/{lead.id}/", {"status": "converted", "chief_complaint": "pain"}, format="json")
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)
        self.assertEqual(lead.chief_complaint, "pain")

    def test_rep_cannot_delete_manager_soft_deletes(self):
        self.intake()
        lead = Lead.objects.get()
        self.assertEqual(self.auth(self.rep1).delete(f"/api/crm/leads/{lead.id}/").status_code, 403)
        self.assertEqual(self.auth(self.manager).delete(f"/api/crm/leads/{lead.id}/").status_code, 204)
        self.assertTrue(Lead.objects.filter(pk=lead.pk, deleted_at__isnull=False).exists())

    def test_non_crm_user_blocked(self):
        u = User.objects.create_user("outsider", password="x")
        c = APIClient()
        c.force_authenticate(u)
        self.assertEqual(c.get("/api/crm/leads/").status_code, 403)

    def test_manual_create_conflict_on_duplicate(self):
        c = self.auth(self.rep1)
        body = {"name": "Ravi", "phone": "+919811111111"}
        self.assertEqual(c.post("/api/crm/leads/", body, format="json").status_code, 201)
        self.assertEqual(c.post("/api/crm/leads/", body, format="json").status_code, 409)

    def test_contact_log_updates_last_contacted_and_dashboard(self):
        self.intake()
        lead = Lead.objects.get()
        c = self.auth(self.rep1)
        r = c.post(f"/api/crm/leads/{lead.id}/contacts/", {"method": "call", "outcome": "connected"}, format="json")
        self.assertEqual(r.status_code, 201)
        lead.refresh_from_db()
        self.assertIsNotNone(lead.last_contacted_at)
        self.assertEqual(c.get("/api/crm/analytics/dashboard/").status_code, 200)
        self.assertEqual(c.get("/api/crm/analytics/response-time/").status_code, 200)

    def test_csv_export_and_search(self):
        self.intake()
        c = self.auth(self.manager)
        self.assertEqual(c.get("/api/crm/leads/?q=asha").json()["count"], 1)
        r = c.get("/api/crm/leads/export/?q=zzz")
        self.assertEqual(len(b"".join(r.streaming_content).decode().strip().splitlines()), 1)  # header only

    def test_merge(self):
        c = self.auth(self.manager)
        a = c.post("/api/crm/leads/", {"name": "A", "phone": "+919811111111"}, format="json").json()["id"]
        b = c.post("/api/crm/leads/", {"name": "B", "email": "b@x.com"}, format="json").json()["id"]
        r = c.post(f"/api/crm/leads/{a}/merge/", {"duplicate_id": b}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["email"], "b@x.com")
        self.assertEqual(Lead.objects.alive().count(), 1)
