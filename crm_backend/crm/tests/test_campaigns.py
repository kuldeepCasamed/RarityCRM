import json
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from crm import campaigns, emailing
from crm.models import (
    Appointment, CampaignRecipient, ContactLog, CRMUser, EmailCampaign, EmailMessage, EmailThread, EmailTemplate, Lead, Role,
)
from crm.tests.test_sequences import CFG, ResendStub, make_user

User = get_user_model()


@override_settings(**{**CFG, "EMAIL_SEND_PAUSE": 0, "EMAIL_CAMPAIGN_SHARE": 0.7, "EMAIL_CAMPAIGN_MAX_RECIPIENTS": 2000})
class CampBase(APITestCase):
    def setUp(self):
        self.mgr = make_user("boss", Role.MANAGER)
        self.rep = make_user("rep1")
        self.kick = mock.patch("crm.tasks.kick_campaigns").start()
        mock.patch("crm.tasks.kick_sequences").start()
        self.addCleanup(mock.patch.stopall)
        self.n = 0
        self.leads = [self.lead(f"Lead{i}", source="website") for i in range(5)]

    def lead(self, name, **kw):
        self.n += 1
        kw.setdefault("email", f"{name.lower()}@example.com")
        return Lead.objects.create(name=name, phone=f"+91981000{self.n:04d}", assigned_to=self.rep, **kw)

    def as_(self, p):
        c = APIClient()
        c.force_authenticate(p.user)
        return c

    def campaign(self, **kw):
        defaults = dict(name="Diwali offer", subject="Hello {{first_name}}", body="Hi {{first_name}}, a special offer.\n\nRegards", created_by=self.mgr,
                        audience_filters={"source": ["website"]}, skip_recent_days=3, skip_open_conversations=True)
        defaults.update(kw)
        return EmailCampaign.objects.create(**defaults)

    def running(self, **kw):
        c = self.campaign(**kw)
        campaigns.launch(c)
        return EmailCampaign.objects.get(pk=c.pk)

    def tick(self, stub=None):
        stub = stub or ResendStub()
        with mock.patch("resend.Emails.send", side_effect=stub):
            out = campaigns.run_campaign_tick()
        return out, stub


class FilterAndAudienceTests(CampBase):
    def test_filters_are_validated_and_cleaned(self):
        ok = campaigns.clean_filters({"status": ["new"], "source": "website", "band": "hot", "country": "  India ", "is_international": "true",
                                      "score_min": "150", "created_from": "2026-01-01", "assigned_to": "unassigned", "junk": "x", "tag": ""})
        self.assertEqual(ok, {"status": ["new"], "source": ["website"], "band": "hot", "country": "India", "is_international": True,
                              "score_min": 100, "created_from": "2026-01-01", "assigned_to": "unassigned"})
        for bad in ({"status": ["bogus"]}, {"band": "lukewarm"}, {"created_from": "yesterday"}, {"assigned_to": "me"}, {"score_min": "x"}, "text"):
            with self.assertRaises(ValueError):
                campaigns.clean_filters(bad)

    def test_breakdown_adds_up_and_each_exclusion_is_explained(self):
        Lead.objects.filter(pk=self.leads[0].pk).update(email="")
        Lead.objects.filter(pk=self.leads[1].pk).update(email_invalid=True)
        Lead.objects.filter(pk=self.leads[2].pk).update(email_opt_out=True)
        prior = self.campaign(name="Earlier")
        EmailMessage.objects.create(thread=EmailThread.objects.create(lead=self.leads[3], subject="x"), direction="out", status="sent", campaign=prior)
        EmailThread.objects.create(lead=self.leads[4], subject="waiting", needs_reply=True)
        self.lead("Other", source="manual")                                                 # not in the audience at all
        qs, info = campaigns.audience({"source": ["website"]}, 3, True)
        self.assertEqual(info, {"matching": 5, "no_email": 1, "invalid": 1, "opted_out": 1, "recently_emailed": 1, "open_conversation": 1, "eligible": 0})
        self.assertEqual(qs.count(), 0)
        # turning the soft rules off brings those two back; the hard rules (no email / bounced / unsubscribed) can never be turned off
        _, info = campaigns.audience({"source": ["website"]}, 0, False)
        self.assertEqual((info["eligible"], info["opted_out"], info["invalid"], info["no_email"]), (2, 1, 1, 1))

    def test_archived_leads_are_never_matched(self):
        self.leads[0].soft_delete()
        self.assertEqual(campaigns.audience({"source": ["website"]})[1]["matching"], 4)

    def test_preview_endpoint(self):
        c = self.as_(self.mgr)
        r = c.post("/api/crm/campaigns/audience/", {"filters": {"source": ["website"]}, "skip_recent_days": 3}, format="json").json()
        self.assertEqual((r["matching"], r["eligible"], r["over_limit"], r["limit"]), (5, 5, False, 2000))
        self.assertEqual(len(r["sample"]), 5)
        self.assertEqual(c.post("/api/crm/campaigns/audience/", {"filters": {"status": ["bogus"]}}, format="json").status_code, 400)
        self.assertEqual(self.as_(self.rep).post("/api/crm/campaigns/audience/", {"filters": {}}, format="json").status_code, 403)
        with override_settings(EMAIL_CAMPAIGN_MAX_RECIPIENTS=3):
            self.assertTrue(c.post("/api/crm/campaigns/audience/", {"filters": {}}, format="json").json()["over_limit"])


class LifecycleApiTests(CampBase):
    def body(self, **kw):
        b = {"name": "Offer", "subject": "Hi {{first_name}}", "body": "Text", "audience_filters": {"source": ["website"], "junk": 1}}
        b.update(kw)
        return b

    def test_crud_and_permissions(self):
        self.assertEqual(self.as_(self.rep).get("/api/crm/campaigns/").status_code, 403)
        self.assertEqual(self.as_(self.rep).post("/api/crm/campaigns/", self.body(), format="json").status_code, 403)
        r = self.as_(self.mgr).post("/api/crm/campaigns/", self.body(), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["status"], d["audience_filters"], d["created_by_name"]), ("draft", {"source": ["website"]}, "Boss Smith"))
        self.assertEqual(self.as_(self.mgr).post("/api/crm/campaigns/", self.body(audience_filters={"band": "tepid"}), format="json").status_code, 400)
        self.assertEqual(self.as_(self.mgr).patch(f"/api/crm/campaigns/{d['id']}/", {"name": "Renamed"}, format="json").json()["name"], "Renamed")

    def test_send_validation_and_freezing_the_audience(self):
        c = self.campaign()
        m = self.as_(self.mgr)
        with override_settings(RESEND_API_KEY=""):
            self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/send/", {}, format="json").status_code, 400)
        past = (timezone.now() - timedelta(days=1)).isoformat()
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/send/", {"scheduled_at": past}, format="json").status_code, 400)
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/send/", {"scheduled_at": "soon"}, format="json").status_code, 400)
        empty = self.campaign(name="Empty", audience_filters={"source": ["partner"]})
        r = m.post(f"/api/crm/campaigns/{empty.id}/send/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Nobody", r.json()["detail"])
        with override_settings(EMAIL_CAMPAIGN_MAX_RECIPIENTS=3):
            r = m.post(f"/api/crm/campaigns/{c.id}/send/", {}, format="json")
            self.assertIn("limit per campaign", r.json()["detail"])
        r = m.post(f"/api/crm/campaigns/{c.id}/send/", {}, format="json")
        self.assertEqual((r.status_code, r.json()["status"], r.json()["recipients_frozen"]), (200, "scheduled", 5))
        self.kick.assert_called()
        self.lead("LateArrival", source="website")                                           # created after sending was scheduled
        self.assertEqual(c.recipients.count(), 5)
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/send/", {}, format="json").status_code, 400)       # can't send twice
        self.assertEqual(m.patch(f"/api/crm/campaigns/{c.id}/", {"subject": "changed"}, format="json").status_code, 400)  # locked

    def test_delete_rules(self):
        m = self.as_(self.mgr)
        draft, live = self.campaign(name="A"), self.running(name="B")
        self.assertEqual(m.delete(f"/api/crm/campaigns/{draft.id}/").status_code, 204)
        self.assertEqual(m.delete(f"/api/crm/campaigns/{live.id}/").status_code, 400)
        m.post(f"/api/crm/campaigns/{live.id}/cancel/")
        self.assertEqual(m.delete(f"/api/crm/campaigns/{live.id}/").status_code, 204)


class SendingTests(CampBase):
    def test_everyone_eligible_is_emailed_once_with_the_safety_features(self):
        c = self.running()
        out, stub = self.tick()
        self.assertEqual((out["sent"], out["campaigns_finished"], len(stub.calls)), (5, 1, 5))
        c.refresh_from_db()
        self.assertEqual(c.status, "sent")
        self.assertIsNotNone(c.finished_at)
        p = stub.calls[0][0]
        self.assertTrue(p["from"].startswith("Boss Smith <"))                               # the manager who made it, not the lead's rep
        self.assertIn("Hi Lead", p["text"])
        self.assertRegex(p["text"], r"https://crm\.example\.com/api/crm/email/unsubscribe/\S+/")
        self.assertEqual(p["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertIn("1 Test Road, Gurugram", p["text"])
        self.assertEqual(len({call[0]["reply_to"] for call in stub.calls}), 5)             # a separate reply thread per person
        self.assertEqual(EmailThread.objects.filter(messages__campaign=c).distinct().count(), 5)
        self.assertEqual(len({call[1]["idempotency_key"] for call in stub.calls}), 5)
        self.assertFalse(ContactLog.objects.exists())                                       # bulk mail is not "human contact"
        self.assertEqual(set(c.recipients.values_list("state", flat=True)), {"sent"})
        self.assertEqual(self.tick()[0]["sent"], 0)                                         # nothing is ever sent twice

    def test_a_future_campaign_waits_for_its_time(self):
        c = self.campaign()
        campaigns.launch(c, when=timezone.now() + timedelta(hours=3))
        out, stub = self.tick()
        self.assertEqual((out["sent"], len(stub.calls)), (0, 0))
        EmailCampaign.objects.filter(pk=c.pk).update(scheduled_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.tick()[0]["sent"], 5)

    def test_it_only_sends_in_office_hours(self):
        self.running()
        with override_settings(EMAIL_SEND_START_HOUR=3, EMAIL_SEND_END_HOUR=3):
            out, stub = self.tick()
        self.assertEqual((out["sent"], "waiting" in out, len(stub.calls)), (0, True, 0))
        self.assertEqual(self.tick()[0]["sent"], 5)

    def test_the_cap_is_shared_with_sequences_and_campaigns_cannot_starve_them(self):
        self.running()
        with override_settings(EMAIL_DAILY_CAP=10, EMAIL_CAMPAIGN_SHARE=0.4):              # campaigns may use 4 of 10
            out, stub = self.tick()
            self.assertEqual((out["sent"], out["capped"]), (4, True))
            self.assertEqual(self.tick()[0]["sent"], 0)                                     # the other 6 are reserved for sequences
        # sequence mail already sent today also counts against the overall cap
        EmailCampaign.objects.all().delete()
        other = self.running(name="Second", audience_filters={"source": ["website"]}, skip_recent_days=0)
        seq_thread = EmailThread.objects.create(lead=self.leads[0], subject="s")
        from crm.models import EmailSequence, SequenceEnrollment
        seq = EmailSequence.objects.create(name="S")
        enr = SequenceEnrollment.objects.create(lead=self.leads[0], sequence=seq, next_send_at=timezone.now())
        for _ in range(8):
            EmailMessage.objects.create(thread=seq_thread, direction="out", status="sent", enrollment=enr)
        with override_settings(EMAIL_DAILY_CAP=10, EMAIL_CAMPAIGN_SHARE=1.0):
            self.assertEqual(self.tick()[0]["sent"], 2)                                     # 10 - 8 already used

    def test_it_continues_the_next_day_when_the_cap_ran_out(self):
        c = self.running()
        with override_settings(EMAIL_DAILY_CAP=10, EMAIL_CAMPAIGN_SHARE=0.3):
            self.assertEqual(self.tick()[0]["sent"], 3)
            c.refresh_from_db()
            self.assertEqual(c.status, "sending")
            EmailMessage.objects.update(created_at=timezone.now() - timedelta(days=1))      # "tomorrow"
            self.assertEqual(self.tick()[0]["sent"], 2)
        c.refresh_from_db()
        self.assertEqual(c.status, "sent")

    def test_rechecks_each_person_at_the_moment_of_sending(self):
        c = self.running()
        Lead.objects.filter(pk=self.leads[0].pk).update(email_opt_out=True)                # unsubscribed after scheduling
        Lead.objects.filter(pk=self.leads[1].pk).update(email_invalid=True)
        self.leads[2].soft_delete()
        Lead.objects.filter(pk=self.leads[3].pk).update(email="")
        out, stub = self.tick()
        self.assertEqual((out["sent"], out["skipped"], len(stub.calls)), (1, 4, 1))
        self.assertEqual(stub.calls[0][0]["to"], [self.leads[4].email])
        self.assertEqual(sorted(c.recipients.filter(state="skipped").values_list("skip_reason", flat=True)), ["bad_email", "deleted", "no_email", "unsubscribed"])
        c.refresh_from_db()
        self.assertEqual(c.status, "sent")

    def test_failures_retry_later_then_give_up_without_blocking_others(self):
        c = self.running()
        out, stub = self.tick(ResendStub(fail=1))
        self.assertEqual((out["sent"], out["failed"]), (4, 1))
        r = c.recipients.get(attempts=1)
        self.assertEqual((r.state, EmailMessage.objects.filter(status="failed").count()), ("pending", 0))
        self.assertGreater(r.next_attempt_at, timezone.now() + timedelta(minutes=10))
        self.assertEqual(self.tick()[0]["sent"], 0)                                         # not retried before it's due
        for _ in range(2):
            CampaignRecipient.objects.filter(pk=r.pk).update(next_attempt_at=timezone.now() - timedelta(minutes=1))
            self.tick(ResendStub(fail=1))
        r.refresh_from_db()
        self.assertEqual((r.state, r.attempts), ("failed", 3))
        c.refresh_from_db()
        self.assertEqual(c.status, "sent")                                                  # finished: one failed, four sent

    def test_two_overlapping_workers_cannot_double_send(self):
        c = self.running()
        EmailCampaign.objects.filter(pk=c.pk).update(status="sending", started_at=timezone.now())
        rid = c.recipients.first().id
        stub = ResendStub()
        with mock.patch("resend.Emails.send", side_effect=stub):
            self.assertEqual(campaigns.process_recipient(rid), "sent")
            self.assertEqual(campaigns.process_recipient(rid), "stale")
        self.assertEqual(len(stub.calls), 1)

    def test_pause_resume_and_cancel(self):
        c = self.running()
        m = self.as_(self.mgr)
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/pause/").json()["status"], "paused")
        self.assertEqual(self.tick()[0]["sent"], 0)                                         # paused: nothing goes out
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/resume/").json()["status"], "scheduled")   # hadn't started yet
        with override_settings(EMAIL_DAILY_CAP=10, EMAIL_CAMPAIGN_SHARE=0.2):
            self.assertEqual(self.tick()[0]["sent"], 2)
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/pause/").json()["status"], "paused")
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/resume/").json()["status"], "sending")     # already started
        r = m.post(f"/api/crm/campaigns/{c.id}/cancel/").json()
        self.assertEqual((r["status"], r["skipped"]), ("cancelled", 3))
        self.assertEqual(self.tick()[0]["sent"], 0)
        self.assertEqual(c.recipients.filter(state="sent").count(), 2)                      # what went out stays sent
        self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/cancel/").status_code, 400)
        self.assertEqual(m.post(f"/api/crm/campaigns/{self.campaign().id}/pause/").status_code, 400)

    def test_unconfigured_email_sends_nothing(self):
        self.running()
        with override_settings(RESEND_API_KEY=""):
            self.assertIn("skipped_all", campaigns.run_campaign_tick())


class TestEmailAndReportTests(CampBase):
    def test_a_test_email_goes_only_to_the_chosen_address_and_leaves_no_trace(self):
        c = self.campaign()
        m = self.as_(self.mgr)
        stub = ResendStub()
        with mock.patch("resend.Emails.send", side_effect=stub):
            r = m.post(f"/api/crm/campaigns/{c.id}/test/", {"to": "me@raritydental.com"}, format="json")
        self.assertEqual((r.status_code, r.json()["sent_to"]), (200, "me@raritydental.com"))
        p = stub.calls[0][0]
        self.assertEqual((p["to"], p["subject"].startswith("[TEST] Hello Lead")), (["me@raritydental.com"], True))
        self.assertIn("This is a TEST", p["text"])
        self.assertNotIn("unsubscribe/", p["text"])
        self.assertEqual((EmailMessage.objects.count(), CampaignRecipient.objects.count(), EmailCampaign.objects.get().status), (0, 0, "draft"))
        with override_settings(RESEND_API_KEY=""):
            self.assertEqual(m.post(f"/api/crm/campaigns/{c.id}/test/", {}, format="json").status_code, 400)

    def test_report_numbers(self):
        c = self.running()
        self.tick()
        msgs = {m.thread.lead_id: m for m in EmailMessage.objects.filter(campaign=c).select_related("thread")}
        ev = lambda t, lead, **d: emailing.handle_delivery_event(t, {"email_id": msgs[lead.id].resend_id, **d})  # noqa: E731
        a, b, d, e, f = self.leads
        for lead in (a, b, d, e):
            ev("email.delivered", lead)
        ev("email.opened", a); ev("email.opened", b); ev("email.clicked", a)
        ev("email.bounced", f, bounce={"type": "Permanent", "message": "no such user"})
        # b replies, d unsubscribes, e books an appointment
        EmailMessage.objects.create(thread=msgs[b.id].thread, direction="in", status="received", subject="Re:", body_text="yes", created_at=timezone.now() + timedelta(minutes=5))
        emailing.mark_opt_out(Lead.objects.get(pk=d.pk))
        Appointment.objects.create(lead=e, start_at=timezone.now() + timedelta(days=2), end_at=timezone.now() + timedelta(days=2, minutes=30))
        r = self.as_(self.mgr).get(f"/api/crm/campaigns/{c.id}/report/").json()
        self.assertEqual({k: r[k] for k in ("recipients", "sent", "delivered", "opened", "clicked", "bounced", "replied", "unsubscribed", "booked_after", "pending", "progress_pct")},
                         {"recipients": 5, "sent": 5, "delivered": 4, "opened": 2, "clicked": 1, "bounced": 1, "replied": 1, "unsubscribed": 1, "booked_after": 1, "pending": 0, "progress_pct": 100})
        rows = self.as_(self.mgr).get(f"/api/crm/campaigns/{c.id}/recipients/").json()
        self.assertEqual((len(rows), {x["state"] for x in rows}), (5, {"sent"}))
        self.assertEqual(self.as_(self.mgr).get(f"/api/crm/campaigns/{c.id}/recipients/?state=pending").json(), [])

    def test_a_reply_to_a_campaign_email_reaches_the_inbox_like_any_other_reply(self):
        c = self.running()
        self.tick()
        m = EmailMessage.objects.filter(campaign=c).select_related("thread").first()
        lead = m.thread.lead
        full = {"from": f"{lead.name} <{lead.email}>", "to": [f"t-{m.thread.token}@reply.raritydental.com"], "subject": "Re: Hello", "text": "Interested!", "headers": {}}
        data = {"email_id": "in_c1", "from": full["from"], "to": full["to"], "subject": "Re: Hello"}
        with mock.patch("crm.emailing.fetch_received", return_value=full), mock.patch("crm.tasks.notify_email_reply"):
            emailing.handle_received(data)
        self.assertTrue(EmailThread.objects.get(pk=m.thread_id).needs_reply)
        self.assertEqual(self.as_(self.rep).get("/api/crm/inbox/count/").json()["needs_reply"], 1)

    def test_schedule_is_registered(self):
        call_command("setup_schedules", stdout=mock.MagicMock())
        from django_q.models import Schedule
        s = Schedule.objects.get(name="Email campaigns")
        self.assertEqual((s.func, s.minutes), ("crm.tasks.run_campaign_tick", 5))
