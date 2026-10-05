import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from crm import pricing, quotes as svc
from crm.models import ContactLog, CRMUser, Lead, Quote, QuoteSequence, Role, TreatmentCatalog

User = get_user_model()
D = Decimal


def make_user(name, role=Role.REP):
    u = User.objects.create_user(name, f"{name}@x.com", "pass12345", first_name=name)
    return CRMUser.objects.create(user=u, role=role)


class PricingTests(SimpleTestCase):
    def test_totals_percent_discount_then_tax(self):
        t = pricing.calculate([(1, "100000"), (2, "9000.50")], "percent", "10", "18")
        self.assertEqual(t.line_totals, [D("100000.00"), D("18001.00")])
        self.assertEqual(t.subtotal, D("118001.00"))
        self.assertEqual(t.discount_amount, D("11800.10"))
        self.assertEqual(t.tax_amount, D("19116.16"))   # 18% of 106200.90 = 19116.162
        self.assertEqual(t.total, D("125317.06"))

    def test_amount_discount_is_capped_and_rounding_is_half_up(self):
        self.assertEqual(pricing.calculate([(1, "500")], "amount", "9999").total, D("0.00"))
        self.assertEqual(pricing.money("2.675"), D("2.68"))  # half-up, not banker's
        self.assertEqual(pricing.calculate([(3, "0.335")], "percent", 0, 0).subtotal, D("1.01"))

    def test_format_money_indian_and_western_grouping(self):
        self.assertEqual(pricing.format_money("1234567.5"), "₹12,34,567.50")
        self.assertEqual(pricing.format_money("999"), "₹999.00")
        self.assertEqual(pricing.format_money("100000"), "₹1,00,000.00")
        self.assertEqual(pricing.format_money("1234567.5", "USD"), "$1,234,567.50")
        self.assertEqual(pricing.format_money("1500", "INR", symbol="Rs. "), "Rs. 1,500.00")
        self.assertEqual(pricing.format_money("-2500", "INR"), "-₹2,500.00")


class Base(APITestCase):
    def setUp(self):
        self.mgr = make_user("boss", Role.MANAGER)
        self.rep = make_user("rep1")
        self.other = make_user("rep2")
        self.lead = Lead.objects.create(name="Asha Verma", phone="+919810012345", email="asha@x.com", country="India", assigned_to=self.rep)

    def as_(self, p):
        c = APIClient()
        c.force_authenticate(p.user)
        return c

    def body(self, **kw):
        b = {"items": [{"description": "Dental implant", "area": "UR6", "quantity": 2, "unit_price": "45000"},
                       {"description": "Consultation", "quantity": 1, "unit_price": "1500"}],
             "discount_type": "percent", "discount_value": "10", "tax_percent": "0", "notes": "Hello"}
        b.update(kw)
        return b

    def make(self, **kw):
        r = self.as_(self.rep).post(f"/api/crm/leads/{self.lead.id}/quotes/", self.body(**kw), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()


class QuoteApiTests(Base):
    def test_create_computes_totals_and_defaults(self):
        q = self.make()
        self.assertEqual(q["number"], f"RQ-{timezone.localdate().year}-0001")
        self.assertEqual((q["subtotal"], q["discount_amount"], q["total"]), ("91500.00", "9150.00", "82350.00"))
        self.assertEqual(q["items"][0]["line_total"], "90000.00")
        self.assertEqual(q["status"], "draft")
        self.assertEqual(q["valid_until"], str(timezone.localdate() + timedelta(days=30)))
        self.assertIn("preliminary assessment", q["terms"])  # default terms applied
        self.assertEqual(q["created_by_name"], "rep1")

    def test_numbers_are_sequential_and_unique_per_year(self):
        a, b = self.make()["number"], self.make()["number"]
        self.assertEqual((a[-4:], b[-4:]), ("0001", "0002"))
        self.assertEqual(svc.next_number(2030), "RQ-2030-0001")
        self.assertEqual(svc.next_number(2030), "RQ-2030-0002")
        self.assertEqual(QuoteSequence.objects.get(year=2030).last, 2)

    def test_validation(self):
        c = self.as_(self.rep)
        url = f"/api/crm/leads/{self.lead.id}/quotes/"
        bad = [self.body(items=[]), self.body(discount_value="150"), self.body(tax_percent="101"),
               self.body(items=[{"description": "x", "quantity": 0, "unit_price": "10"}]),
               self.body(items=[{"description": "x", "quantity": 1, "unit_price": "-5"}]),
               self.body(discount_type="amount", discount_value="999999"), self.body(discount_value="-1"), self.body(currency="XYZ")]
        for b in bad:
            self.assertEqual(c.post(url, b, format="json").status_code, 400, b)
        self.assertEqual(Quote.objects.count(), 0)

    def test_client_cannot_set_totals_status_or_number(self):
        r = self.as_(self.rep).post(f"/api/crm/leads/{self.lead.id}/quotes/", self.body(total="1.00", status="accepted", number="HACK-1"), format="json")
        q = r.json()
        self.assertEqual((q["total"], q["status"]), ("82350.00", "draft"))
        self.assertTrue(q["number"].startswith("RQ-"))

    def test_edit_draft_recomputes_and_replaces_items(self):
        q = self.make()
        r = self.as_(self.rep).patch(f"/api/crm/quotes/{q['id']}/", {"items": [{"description": "Whitening", "quantity": 1, "unit_price": "12000"}], "discount_value": "0"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["total"], len(r.json()["items"])), ("12000.00", 1))
        # patching notes alone keeps items and totals
        r = self.as_(self.rep).patch(f"/api/crm/quotes/{q['id']}/", {"notes": "new"}, format="json")
        self.assertEqual((r.json()["total"], r.json()["notes"]), ("12000.00", "new"))

    def test_lifecycle_lead_sync_and_stage_moves(self):
        q = self.make()
        c = self.as_(self.rep)
        r = c.post(f"/api/crm/quotes/{q['id']}/status/", {"status": "sent"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.quote_amount, self.lead.status), (D("82350.00"), "treatment_plan_sent"))
        self.assertEqual(self.lead.quote_valid_until, timezone.localdate() + timedelta(days=30))
        c.post(f"/api/crm/quotes/{q['id']}/status/", {"status": "accepted"}, format="json")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "treatment_booked")
        self.assertIsNotNone(Quote.objects.get().decided_at)

    def test_invalid_transitions_and_edit_lock(self):
        q = self.make()
        c = self.as_(self.rep)
        st = lambda s: c.post(f"/api/crm/quotes/{q['id']}/status/", {"status": s}, format="json").status_code  # noqa: E731
        self.assertEqual(st("accepted"), 400)  # draft -> accepted not allowed
        self.assertEqual(st("nonsense"), 400)
        self.assertEqual(st("sent"), 200)
        self.assertEqual(c.patch(f"/api/crm/quotes/{q['id']}/", {"notes": "x"}, format="json").status_code, 400)  # locked once sent
        self.assertEqual(c.delete(f"/api/crm/quotes/{q['id']}/").status_code, 400)  # only drafts deletable
        self.assertEqual(st("rejected"), 200)
        self.assertEqual(st("sent"), 400)  # terminal
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "treatment_plan_sent")  # rejection doesn't move the lead

    def test_stage_never_goes_backwards_or_out_of_closed(self):
        Lead.objects.filter(pk=self.lead.pk).update(status="negotiating")
        q = self.make()
        self.as_(self.rep).post(f"/api/crm/quotes/{q['id']}/status/", {"status": "sent"}, format="json")
        self.assertEqual(Lead.objects.get(pk=self.lead.pk).status, "negotiating")
        Lead.objects.filter(pk=self.lead.pk).update(status="lost")
        q2 = self.make()
        self.as_(self.rep).post(f"/api/crm/quotes/{q2['id']}/status/", {"status": "sent"}, format="json")
        self.assertEqual(Lead.objects.get(pk=self.lead.pk).status, "lost")

    def test_non_inr_quote_does_not_touch_lead_quote_amount(self):
        q = self.make(currency="USD")
        self.as_(self.rep).post(f"/api/crm/quotes/{q['id']}/status/", {"status": "sent"}, format="json")
        self.assertIsNone(Lead.objects.get(pk=self.lead.pk).quote_amount)

    def test_cannot_send_expired_validity(self):
        q = self.make(valid_until=str(timezone.localdate() - timedelta(days=1)))
        r = self.as_(self.rep).post(f"/api/crm/quotes/{q['id']}/status/", {"status": "sent"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("expired", r.json()["detail"])

    def test_duplicate_makes_independent_draft_revision(self):
        q = self.make()
        c = self.as_(self.rep)
        c.post(f"/api/crm/quotes/{q['id']}/status/", {"status": "sent"}, format="json")
        r = c.post(f"/api/crm/quotes/{q['id']}/duplicate/")
        self.assertEqual(r.status_code, 201)
        d = r.json()
        self.assertEqual((d["status"], d["total"], d["number"] != q["number"], len(d["items"])), ("draft", q["total"], True, 2))
        c.patch(f"/api/crm/quotes/{d['id']}/", {"items": [{"description": "x", "quantity": 1, "unit_price": "1"}]}, format="json")
        self.assertEqual(Quote.objects.get(pk=q["id"]).total, D("82350.00"))  # original untouched

    def test_draft_delete_permissions(self):
        q = self.make()  # authored by rep1
        self.assertEqual(self.as_(self.other).delete(f"/api/crm/quotes/{q['id']}/").status_code, 403)
        self.assertEqual(self.as_(self.mgr).delete(f"/api/crm/quotes/{q['id']}/").status_code, 204)

    def test_listing_and_non_crm_user_blocked(self):
        self.make()
        self.assertEqual(len(self.as_(self.rep).get(f"/api/crm/leads/{self.lead.id}/quotes/").json()), 1)
        self.assertEqual(self.as_(self.rep).get("/api/crm/quotes/?status=sent").json()["count"], 0)
        c = APIClient()
        c.force_authenticate(User.objects.create_user("outsider", password="x"))
        self.assertEqual(c.get(f"/api/crm/leads/{self.lead.id}/quotes/").status_code, 403)

    def test_expire_overdue_job(self):
        q = Quote.objects.get(pk=self.make()["id"])
        Quote.objects.filter(pk=q.pk).update(status="sent", valid_until=timezone.localdate() - timedelta(days=1))
        Quote.objects.create(lead=self.lead, number="RQ-X-1", status="sent", valid_until=timezone.localdate() + timedelta(days=5))
        self.assertEqual(svc.expire_overdue(), 1)
        self.assertEqual(Quote.objects.get(pk=q.pk).status, "expired")

    def test_catalog_read_for_all_write_for_managers(self):
        self.assertEqual(self.as_(self.rep).post("/api/crm/treatment-catalog/", {"name": "Implant", "default_price": "45000"}, format="json").status_code, 403)
        r = self.as_(self.mgr).post("/api/crm/treatment-catalog/", {"name": "Implant", "category": "implants", "default_price": "45000"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.as_(self.mgr).post("/api/crm/treatment-catalog/", {"name": "Bad", "default_price": "-1"}, format="json").status_code, 400)
        self.assertEqual(len(self.as_(self.rep).get("/api/crm/treatment-catalog/").json()), 1)


@override_settings(CLINIC_NAME="Rarity Dental", CLINIC_PHONE="+91 124 000 0000", CLINIC_TAX_ID="06ABCDE1234F1Z5")
class PdfTests(Base):
    def pdf_text(self, content):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join(p.extract_text() for p in reader.pages), len(reader.pages)

    def test_pdf_contains_the_quote_details(self):
        q = self.make(tax_percent="5", notes="Please bring previous X-rays", currency="INR")
        r = self.as_(self.rep).get(f"/api/crm/quotes/{q['id']}/pdf/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn(f'inline; filename="{q["number"]}.pdf"', r["Content-Disposition"])
        text, _ = self.pdf_text(r.content)
        for expected in [q["number"], "ESTIMATE", "Asha Verma", "Dental implant", "UR6", "₹90,000.00", "Subtotal", "Discount (10%)",
                         "Tax (5%)", "TOTAL", "₹86,467.50", "Please bring previous X-rays", "GSTIN: 06ABCDE1234F1Z5", "TERMS"]:
            self.assertIn(expected, text, expected)

    def test_download_flag_and_long_quote_paginates_and_escapes_html(self):
        items = [{"description": f"Treatment <b>{i}</b> & more", "quantity": 1, "unit_price": "100"} for i in range(60)]
        q = self.make(items=items, notes="<script>alert(1)</script>")
        r = self.as_(self.rep).get(f"/api/crm/quotes/{q['id']}/pdf/?download=1")
        self.assertIn("attachment", r["Content-Disposition"])
        text, pages = self.pdf_text(r.content)
        self.assertGreater(pages, 1)
        self.assertIn("Treatment <b>0</b> & more", text)  # shown literally, not interpreted
        self.assertIn("<script>alert(1)</script>", text)

    def test_other_currencies_and_no_font_fallback(self):
        q = self.make(currency="USD")
        text, _ = self.pdf_text(self.as_(self.rep).get(f"/api/crm/quotes/{q['id']}/pdf/").content)
        self.assertIn("$82,350.00", text)
        q2 = self.make()
        with mock.patch("crm.utils.quote_pdf._fonts", return_value=("Helvetica", "Helvetica-Bold", False)):
            text, _ = self.pdf_text(self.as_(self.rep).get(f"/api/crm/quotes/{q2['id']}/pdf/").content)
        self.assertIn("Rs. 82,350.00", text)  # rupee sign replaced when the Unicode font is unavailable


@override_settings(RESEND_API_KEY="re_test", CLINIC_EMAIL="hello@raritydental.com")
class QuoteEmailTests(Base):
    def post(self, qid, **body):
        return self.as_(self.rep).post(f"/api/crm/quotes/{qid}/email/", body, format="json")

    @mock.patch("crm.views.quotes.send_email", return_value="sent to 1")
    def test_email_sends_pdf_marks_sent_and_logs_contact(self, send):
        q = self.make()
        r = self.post(q["id"], message="Here is your plan")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "sent")
        to, subject, html, attachments, reply_to = send.call_args.args
        self.assertEqual(to, ["asha@x.com"])
        self.assertIn(q["number"], subject)
        self.assertIn("Here is your plan", html)
        self.assertEqual(attachments[0]["filename"], f"{q['number']}.pdf")
        import base64
        self.assertTrue(base64.b64decode(attachments[0]["content"]).startswith(b"%PDF"))
        self.assertEqual(reply_to, "hello@raritydental.com")
        log = ContactLog.objects.get()
        self.assertEqual((log.method, log.direction), ("email", "outbound"))
        self.assertIn(q["number"], log.notes)
        self.assertIsNotNone(Lead.objects.get(pk=self.lead.pk).last_contacted_at)
        self.assertEqual(Lead.objects.get(pk=self.lead.pk).status, "treatment_plan_sent")

    @mock.patch("crm.views.quotes.send_email")
    def test_email_guards(self, send):
        q = self.make()
        Lead.objects.filter(pk=self.lead.pk).update(email="")
        self.assertEqual(self.post(q["id"]).status_code, 400)                       # no address
        self.assertEqual(self.post(q["id"], to="not-an-email").status_code, 400)
        self.assertEqual(Quote.objects.get().status, "draft")                       # nothing marked sent on failure
        Quote.objects.update(status="rejected")
        self.assertEqual(self.post(q["id"], to="a@b.com").status_code, 400)
        send.assert_not_called()

    @mock.patch("crm.views.quotes.send_email", side_effect=RuntimeError("provider down"))
    def test_provider_failure_is_reported_not_swallowed(self, _s):
        q = self.make()
        r = self.post(q["id"], to="a@b.com")
        self.assertEqual(r.status_code, 502)
        self.assertIn("provider down", r.json()["detail"])
        self.assertEqual(ContactLog.objects.count(), 0)

    @override_settings(RESEND_API_KEY="")
    def test_unconfigured_email_returns_503(self):
        self.assertEqual(self.post(self.make()["id"], to="a@b.com").status_code, 503)
