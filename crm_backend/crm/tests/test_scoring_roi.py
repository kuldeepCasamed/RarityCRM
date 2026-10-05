from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from crm.models import Appointment, ContactLog, CRMUser, Lead, MarketingSpend, Role
from crm import scoring, services

User = get_user_model()
TODAY = timezone.localdate()


def make_user(name, role=Role.REP):
    u = User.objects.create_user(name, f"{name}@x.com", "pass12345", first_name=name)
    return CRMUser.objects.create(user=u, role=role)


def lead(**kw):
    kw.setdefault("name", "L")
    return Lead.objects.create(**kw)


def labels(l):
    return {b["label"]: b["points"] for b in l.score_breakdown}


class ScoringTests(APITestCase):
    def test_fresh_lead_is_cold_with_explained_points(self):
        l = lead(phone="+919810012345", email="a@x.com", source="website", treatment_interests=["implants"])
        l.refresh_from_db()
        self.assertEqual(l.score, 5 + 3 + 10 + 5)  # phone, email, high-value treatment, website
        self.assertEqual(scoring.band(l.score), "cold")
        self.assertEqual(labels(l)["High-value treatment interest"], 10)
        self.assertEqual(sum(b["points"] for b in l.score_breakdown), l.score)

    def test_score_rises_with_engagement_and_stage(self):
        l = lead(phone="+919810012345", source="referral", urgency="urgent", treatment_interests=["implants"], budget_band="1-2L")
        before = Lead.objects.get(pk=l.pk).score
        ContactLog.objects.create(lead=l, method="call", outcome="connected")
        ContactLog.objects.create(lead=l, method="call", outcome="interested")
        services.change_status(Lead.objects.get(pk=l.pk), "consult_booked")
        l.refresh_from_db()
        self.assertGreater(l.score, before)
        self.assertIn("Stage: Consult Booked", labels(l))
        self.assertEqual(labels(l)["2 connected conversation(s)"], 10)
        self.assertEqual(scoring.band(l.score), "hot")  # 5+10+4+10+10+10+5+15 = 69

    def test_appointments_move_score_and_signals_fire_on_delete(self):
        l = lead(phone="+919810012345")
        base = Lead.objects.get(pk=l.pk).score
        a = Appointment.objects.create(lead=l, start_at=timezone.now() + timedelta(days=1), end_at=timezone.now() + timedelta(days=1, minutes=30))
        self.assertEqual(Lead.objects.get(pk=l.pk).score, base + 5)
        a.status = "no_show"
        a.save()
        self.assertEqual(Lead.objects.get(pk=l.pk).score, base - 5)
        a.delete()
        self.assertEqual(Lead.objects.get(pk=l.pk).score, base)

    def test_terminal_statuses_and_clamping(self):
        l = lead(phone="+919810012345")
        services.change_status(Lead.objects.get(pk=l.pk), "converted")
        self.assertEqual(Lead.objects.get(pk=l.pk).score, 100)
        l2 = lead(phone="+919810012346")
        services.change_status(Lead.objects.get(pk=l2.pk), "lost")
        self.assertEqual(Lead.objects.get(pk=l2.pk).score, 0)
        # stacked bonuses never exceed 100; penalties never go below 0
        big = lead(phone="+919810012347", email="b@x.com", source="referral", urgency="urgent", status="treatment_booked",
                   treatment_interests=["implants"], budget_band="x", quote_amount=1, is_international=True, travel_from=TODAY)
        ContactLog.objects.create(lead=big, method="call", outcome="connected")
        ContactLog.objects.create(lead=big, method="call", outcome="connected")
        self.assertEqual(Lead.objects.get(pk=big.pk).score, 100)

    def test_neglected_lead_loses_points_and_nightly_refresh_decays(self):
        l = lead(phone="+919810012345", source="phone")
        ContactLog.objects.create(lead=l, method="call", outcome="connected", contacted_at=timezone.now() - timedelta(days=1))
        fresh = Lead.objects.get(pk=l.pk).score
        ContactLog.objects.filter(lead=l).update(contacted_at=timezone.now() - timedelta(days=20))
        self.assertEqual(scoring.refresh_all(), 1)
        stale = Lead.objects.get(pk=l.pk)
        self.assertEqual(stale.score, fresh - 5 - 10)  # lost "recent contact" bonus, gained "14+ days" penalty
        self.assertIn("No contact for 14+ days", labels(stale))
        self.assertEqual(scoring.refresh_all(), 0)  # idempotent: nothing changed second time

    def test_api_exposes_score_band_filter_and_ordering(self):
        mgr = make_user("boss", Role.MANAGER)
        hot = lead(name="Hot", phone="+919810000001", source="referral", urgency="urgent", status="negotiating", treatment_interests=["implants"])
        lead(name="Cold", phone="+919810000002")
        c = APIClient()
        c.force_authenticate(mgr.user)
        r = c.get("/api/crm/leads/?band=hot").json()
        self.assertEqual([x["name"] for x in r["results"]], ["Hot"])
        self.assertEqual(r["results"][0]["score_band"], "hot")
        self.assertEqual([x["name"] for x in c.get("/api/crm/leads/?ordering=-score").json()["results"]], ["Hot", "Cold"])
        self.assertEqual(c.get("/api/crm/leads/?score_min=50").json()["count"], 1)
        detail = c.get(f"/api/crm/leads/{hot.id}/").json()
        self.assertTrue(detail["score_breakdown"])
        # score can't be set by the client
        c.patch(f"/api/crm/leads/{hot.id}/", {"score": 1, "score_breakdown": []}, format="json")
        self.assertGreater(Lead.objects.get(pk=hot.pk).score, 50)
        self.assertEqual(c.get("/api/crm/analytics/dashboard/").json()["hot_leads"][0]["name"], "Hot")

    def test_merge_rescores_primary(self):
        a, b = lead(name="A", phone="+919810000001"), lead(name="B", email="b@x.com")
        ContactLog.objects.create(lead=b, method="call", outcome="connected")
        services.merge_leads(a, b)
        self.assertIn("1 connected conversation(s)", labels(Lead.objects.get(pk=a.pk)))


class ROITests(APITestCase):
    def setUp(self):
        self.mgr = make_user("boss", Role.MANAGER)
        self.rep = make_user("rep")
        self.c = APIClient()
        self.c.force_authenticate(self.mgr.user)
        # google_ads: 4 leads, 2 consult, 1 converted worth 100k, spend 20k
        for i in range(4):
            lead(name=f"G{i}", phone=f"+91981001000{i}", source="google_ads", utm_campaign="Implants-Q4" if i < 3 else "brand")
        g0 = Lead.objects.get(name="G0")
        Appointment.objects.create(lead=g0, start_at=timezone.now(), end_at=timezone.now() + timedelta(minutes=30))
        Appointment.objects.create(lead=Lead.objects.get(name="G1"), start_at=timezone.now(), end_at=timezone.now() + timedelta(minutes=30))
        g0.quote_amount = 100000
        g0.save()
        services.change_status(Lead.objects.get(pk=g0.pk), "converted")  # converted_value defaults to quote
        MarketingSpend.objects.create(date=TODAY, source="google_ads", campaign="implants-q4 ", amount=15000)
        MarketingSpend.objects.create(date=TODAY, source="google_ads", campaign="brand", amount=5000)
        # referral: free leads, revenue but zero spend
        r = lead(name="R0", phone="+919810099999", source="referral", status="new")
        services.change_status(Lead.objects.get(pk=r.pk), "converted")
        Lead.objects.filter(pk=r.pk).update(converted_value=50000)
        # meta: spend but no leads at all
        MarketingSpend.objects.create(date=TODAY, source="meta_ads", amount=3000)
        # outside range
        MarketingSpend.objects.create(date=TODAY - timedelta(days=90), source="google_ads", amount=999999)

    def rows(self, **q):
        data = self.c.get("/api/crm/analytics/source-roi/", q).json()
        return {r["source"]: r for r in data["rows"]}, data

    def test_converted_value_defaults_to_quote(self):
        self.assertEqual(float(Lead.objects.get(name="G0").converted_value), 100000.0)

    def test_source_metrics_math(self):
        rows, data = self.rows(days=30)
        g = rows["google_ads"]
        self.assertEqual((g["leads"], g["consults"], g["converted"], g["spend"], g["revenue"]), (4, 2, 1, 20000.0, 100000.0))
        self.assertEqual(g["cost_per_lead"], 5000.0)
        self.assertEqual(g["cost_per_consult"], 10000.0)
        self.assertEqual(g["cost_per_conversion"], 20000.0)
        self.assertEqual(g["conversion_rate"], 25.0)
        self.assertEqual(g["roas"], 5.0)
        self.assertEqual(g["roi_pct"], 400.0)  # (100k-20k)/20k
        self.assertEqual(data["rows"][0]["source"], "google_ads")  # biggest spend first

    def test_zero_denominators_are_null_not_errors(self):
        rows, _ = self.rows(days=30)
        ref, meta = rows["referral"], rows["meta_ads"]
        self.assertEqual((ref["spend"], ref["revenue"]), (0.0, 50000.0))
        self.assertIsNone(ref["roi_pct"])
        self.assertIsNone(ref["roas"])
        self.assertIsNone(ref["cost_per_lead"])
        self.assertEqual((meta["leads"], meta["spend"]), (0, 3000.0))  # spend with no leads still shows
        self.assertIsNone(meta["cost_per_lead"])
        self.assertIsNone(meta["conversion_rate"])

    def test_totals_and_date_range(self):
        rows, data = self.rows(days=30)
        t = data["total"]
        self.assertEqual((t["leads"], t["converted"], t["spend"], t["revenue"]), (5, 2, 23000.0, 150000.0))
        self.assertEqual(t["roi_pct"], round((150000 - 23000) / 23000 * 100, 1))
        self.assertNotIn(999999.0, [r["spend"] for r in data["rows"]])  # out-of-range spend excluded
        wide, _ = self.rows(**{"from": str(TODAY - timedelta(days=120)), "to": str(TODAY)})
        self.assertEqual(wide["google_ads"]["spend"], 20000.0 + 999999.0)

    def test_group_by_campaign_matches_case_and_whitespace(self):
        data = self.c.get("/api/crm/analytics/source-roi/?group_by=campaign").json()
        by = {(r["source"], r["campaign"].lower()): r for r in data["rows"]}
        imp = by[("google_ads", "implants-q4")]
        self.assertEqual((imp["leads"], imp["spend"]), (3, 15000.0))  # "Implants-Q4" leads + "implants-q4 " spend merged
        self.assertEqual(imp["cost_per_lead"], 5000.0)
        self.assertEqual(by[("google_ads", "brand")]["spend"], 5000.0)

    def test_bad_ranges_and_permissions(self):
        self.assertEqual(self.c.get("/api/crm/analytics/source-roi/?from=2026-02-01&to=2026-01-01").status_code, 400)
        self.assertEqual(self.c.get("/api/crm/analytics/source-roi/?from=nope&to=x").status_code, 400)
        rep = APIClient()
        rep.force_authenticate(self.rep.user)
        self.assertEqual(rep.get("/api/crm/analytics/source-roi/").status_code, 403)
        self.assertEqual(rep.get("/api/crm/marketing-spend/").status_code, 403)
        self.assertEqual(rep.post("/api/crm/marketing-spend/", {"date": str(TODAY), "source": "website", "amount": "5"}, format="json").status_code, 403)

    def test_spend_crud_and_validation(self):
        r = self.c.post("/api/crm/marketing-spend/", {"date": str(TODAY), "source": "website", "amount": "1200.50", "campaign": " Smile "}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["campaign"], "Smile")
        self.assertEqual(self.c.post("/api/crm/marketing-spend/", {"date": str(TODAY), "source": "website", "amount": "0"}, format="json").status_code, 400)
        self.assertEqual(self.c.post("/api/crm/marketing-spend/", {"date": str(TODAY), "source": "tiktok", "amount": "5"}, format="json").status_code, 400)
        sid = r.json()["id"]
        self.assertEqual(self.c.patch(f"/api/crm/marketing-spend/{sid}/", {"amount": "1300"}, format="json").status_code, 200)
        self.assertEqual(self.c.delete(f"/api/crm/marketing-spend/{sid}/").status_code, 204)

    def test_spend_csv_import(self):
        csv = ("Date,Source,Amount,Campaign\n"
               f"{TODAY},Google Ads,\"₹12,000\",Winter\n"
               f"{TODAY},meta ads,500,\n"
               "31/12/2026,google_ads,100,\n"
               f"{TODAY},tiktok,100,\n"
               f"{TODAY},website,abc,\n"
               f"{TODAY},website,-5,\n")
        f = SimpleUploadedFile("s.csv", csv.encode(), content_type="text/csv")
        r = self.c.post("/api/crm/marketing-spend/import/", {"file": f}, format="multipart").json()
        self.assertEqual(r["created"], 2)
        self.assertEqual([e["row"] for e in r["errors"]], [4, 5, 6, 7])
        self.assertIn("unknown source", r["errors"][1]["error"])
        self.assertEqual(float(MarketingSpend.objects.get(campaign="Winter").amount), 12000.0)
        bad = SimpleUploadedFile("b.csv", b"foo,bar\n1,2\n", content_type="text/csv")
        self.assertEqual(self.c.post("/api/crm/marketing-spend/import/", {"file": bad}, format="multipart").status_code, 400)
