import json
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from crm import emailing, services
from crm.models import (
    Appointment, ContactLog, CRMUser, EmailMessage, EmailSequence, EmailSequenceStep, EmailTemplate, Lead, Note, Role, SequenceEnrollment,
)
from crm.tests.test_email import SECRET, sign

User = get_user_model()
CFG = dict(RESEND_API_KEY="re_test", EMAIL_REPLY_DOMAIN="reply.raritydental.com", RESEND_WEBHOOK_SECRET=SECRET,
           EMAIL_VERIFIED_DOMAINS=["raritydental.com"], EMAIL_DEFAULT_FROM="hello@raritydental.com", CLINIC_NAME="Rarity Dental",
           CLINIC_ADDRESS="1 Test Road, Gurugram", PUBLIC_BASE_URL="https://crm.example.com",
           EMAIL_DAILY_CAP=100, EMAIL_SEND_START_HOUR=0, EMAIL_SEND_END_HOUR=24)


def make_user(name, role=Role.REP):
    u = User.objects.create_user(name, f"{name}@raritydental.com", "pass12345", first_name=name.title(), last_name="Smith")
    return CRMUser.objects.create(user=u, role=role)


class ResendStub:
    """Stands in for resend.Emails.send: unique ids, remembers every call."""

    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    def __call__(self, params, options=None):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("resend is down")
        self.calls.append((params, options))
        return {"id": f"re_seq_{len(self.calls)}_{id(self)}"}


@override_settings(**CFG)
class SeqBase(APITestCase):
    def setUp(self):
        self.mgr = make_user("boss", Role.MANAGER)
        self.rep = make_user("rep1")
        self.tpls = [EmailTemplate.objects.create(name=f"T{i}", subject=f"Subject {i} {{{{first_name}}}}", body=f"Body {i} for {{{{first_name}}}}") for i in (1, 2, 3)]
        self.seq = EmailSequence.objects.create(name="Welcome", is_active=True, run_while_status=["new", "contacted"])
        for i, (t, d) in enumerate(zip(self.tpls, (0, 48, 72))):
            EmailSequenceStep.objects.create(sequence=self.seq, position=i, delay_hours=d, template=t)
        self.lead = Lead.objects.create(name="Asha Verma", email="asha@gmail.com", phone="+919810012345", assigned_to=self.rep)
        self.kick = mock.patch("crm.tasks.kick_sequences").start()
        self.addCleanup(mock.patch.stopall)

    def as_(self, p):
        c = APIClient()
        c.force_authenticate(p.user)
        return c

    def enroll(self, lead=None, seq=None):
        return emailing.enroll(lead or self.lead, seq or self.seq)

    def tick(self, stub=None):
        stub = stub or ResendStub()
        with mock.patch("resend.Emails.send", side_effect=stub):
            out = emailing.run_sequence_tick()
        return out, stub

    def make_due(self, enr):
        SequenceEnrollment.objects.filter(pk=enr.pk).update(next_send_at=timezone.now() - timedelta(minutes=1))


class EnrolmentRulesTests(SeqBase):
    def test_enrol_schedules_the_first_step_after_its_delay(self):
        e = self.enroll()
        self.assertEqual((e.state, e.current_step), ("active", 0))
        self.assertLess(abs((e.next_send_at - timezone.now()).total_seconds()), 5)
        self.seq.steps.filter(position=0).update(delay_hours=6)
        self.lead2 = Lead.objects.create(name="B", email="b@x.com")
        e2 = self.enroll(self.lead2)
        self.assertAlmostEqual((e2.next_send_at - timezone.now()).total_seconds(), 6 * 3600, delta=5)

    def test_ineligible_leads_are_refused_with_a_reason(self):
        self.enroll()
        cases = {
            "already enrolled": {},
            "no email": {"email": ""},
            "bounced": {"email_invalid": True},
            "unsubscribed": {"email_opt_out": True},
            "outside this sequence's statuses": {"status": "lost"},
        }
        for reason, change in cases.items():
            lead = Lead.objects.create(name=f"L-{reason}", email="x@x.com") if reason != "already enrolled" else self.lead
            Lead.objects.filter(pk=lead.pk).update(**change)
            lead.refresh_from_db()
            with self.assertRaisesRegex(emailing.EmailError, reason):
                self.enroll(lead)
        self.seq.is_active = False
        self.seq.save()
        with self.assertRaisesRegex(emailing.EmailError, "not active"):
            self.enroll(Lead.objects.create(name="Z", email="z@x.com"))

    def test_a_finished_enrolment_allows_a_new_one_but_two_live_ones_are_impossible(self):
        e = self.enroll()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SequenceEnrollment.objects.create(lead=self.lead, sequence=self.seq, next_send_at=timezone.now())
        emailing.stop_enrollment(e, "manual")
        self.assertIsNotNone(self.enroll())

    def test_a_win_back_sequence_can_target_lost_leads(self):
        win = EmailSequence.objects.create(name="Win-back", is_active=True, run_while_status=["lost"])
        EmailSequenceStep.objects.create(sequence=win, position=0, delay_hours=0, template=self.tpls[0])
        Lead.objects.filter(pk=self.lead.pk).update(status="lost")
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.enroll(self.lead, win))
        out, stub = self.tick()
        self.assertEqual(out["sent"], 1)


class SendingEngineTests(SeqBase):
    def test_first_email_goes_out_immediately_with_unsubscribe_and_threading(self):
        e = self.enroll()
        out, stub = self.tick()
        self.assertEqual(out["sent"], 1)
        params, options = stub.calls[0]
        self.assertEqual(params["to"], ["asha@gmail.com"])
        self.assertEqual(params["subject"], "Subject 1 Asha")
        self.assertTrue(params["from"].startswith("Rep1 Smith <"))                         # the assigned rep's name
        self.thread_token = EmailMessage.objects.get().thread.token
        self.assertEqual(params["reply_to"], f"t-{self.thread_token}@reply.raritydental.com")
        url = re.search(r"https://crm\.example\.com/api/crm/email/unsubscribe/\S+/", params["text"]).group(0)
        self.assertIn(url, params["html"].replace("&amp;", "&"))
        self.assertEqual(params["headers"]["List-Unsubscribe"], f"<{url}>")
        self.assertEqual(params["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertIn("1 Test Road, Gurugram", params["text"])
        self.assertEqual(options, {"idempotency_key": f"seq-{e.id}-{self.seq.steps.first().id}-0"})
        e.refresh_from_db()
        self.assertEqual((e.current_step, e.state, e.attempts), (1, "active", 0))
        self.assertAlmostEqual((e.next_send_at - timezone.now()).total_seconds(), 48 * 3600, delta=10)
        m = EmailMessage.objects.get()
        self.assertEqual((m.enrollment_id, m.sent_by_id, m.status, e.thread_id), (e.id, None, "sent", m.thread_id))
        self.assertNotIn("unsubscribe", m.body_text.lower())                               # the stored body is the clean message

    def test_automated_mail_is_not_counted_as_human_contact(self):
        before = Lead.objects.get(pk=self.lead.pk).score
        self.enroll()
        self.tick()
        lead = Lead.objects.get(pk=self.lead.pk)
        self.assertIsNone(lead.last_contacted_at)
        self.assertFalse(ContactLog.objects.exists())
        self.assertEqual(lead.score, before)

    def test_full_run_uses_one_thread_and_completes(self):
        e = self.enroll()
        stub = ResendStub()
        for expected_step in (1, 2, 3):
            out, _ = self.tick(stub)
            self.assertEqual(out["sent"], 1)
            e.refresh_from_db()
            self.assertEqual(e.current_step, expected_step)
            if e.state == "active":
                self.make_due(e)
        e.refresh_from_db()
        self.assertEqual((e.state, e.next_send_at), ("completed", None))
        self.assertIsNotNone(e.finished_at)
        self.assertEqual(len(stub.calls), 3)
        self.assertEqual([c[0]["subject"] for c in stub.calls], ["Subject 1 Asha", "Subject 2 Asha", "Subject 3 Asha"])  # own subjects, no "Re:"
        self.assertEqual({c[0]["reply_to"] for c in stub.calls}, {c[0]["reply_to"] for c in stub.calls[:1]})            # same reply thread
        self.assertEqual(EmailMessage.objects.values("thread").distinct().count(), 1)
        self.assertEqual(self.tick(stub)[0]["sent"], 0)                                                                 # nothing left to send

    def test_nothing_is_sent_before_it_is_due(self):
        e = self.enroll()
        self.tick()
        out, stub = self.tick()                                                             # step 2 is 48h away
        self.assertEqual((out["sent"], len(stub.calls)), (0, 0))

    def test_daily_cap_limits_automated_sends(self):
        for i in range(3):
            self.enroll(Lead.objects.create(name=f"L{i}", email=f"l{i}@x.com"))
        with override_settings(EMAIL_DAILY_CAP=2):
            out, stub = self.tick()
            self.assertEqual((out["sent"], out["capped"], len(stub.calls)), (2, True, 2))
            out2, stub2 = self.tick()                                                       # same day: cap already used
            self.assertEqual((out2["sent"], len(stub2.calls)), (0, 0))
        with override_settings(EMAIL_DAILY_CAP=3):
            self.assertEqual(self.tick()[0]["sent"], 1)                                     # the remaining one goes out when the cap allows
        with override_settings(EMAIL_DAILY_CAP=0):
            self.enroll(Lead.objects.create(name="Late", email="late@x.com"))
            self.assertEqual(self.tick()[0]["sent"], 0)

    def test_follow_ups_wait_for_office_hours_but_the_first_email_does_not(self):
        with override_settings(EMAIL_SEND_START_HOUR=3, EMAIL_SEND_END_HOUR=3):             # an empty window = never
            e = self.enroll()
            self.assertEqual(self.tick()[0]["sent"], 1)                                      # first email: immediate
            self.make_due(e)
            out, stub = self.tick()
            self.assertEqual((out["sent"], len(stub.calls)), (0, 0))                        # follow-up waits
        self.assertEqual(self.tick()[0]["sent"], 1)                                          # window open again: it goes out

    def test_unconfigured_email_sends_nothing_and_does_not_burn_attempts(self):
        e = self.enroll()
        with override_settings(RESEND_API_KEY=""):
            out = emailing.run_sequence_tick()
        self.assertIn("skipped", out)
        e.refresh_from_db()
        self.assertEqual((e.attempts, e.current_step, e.state), (0, 0, "active"))

    def test_paused_sequence_keeps_its_place(self):
        e = self.enroll()
        EmailSequence.objects.filter(pk=self.seq.pk).update(is_active=False)
        self.assertEqual(self.tick()[0]["sent"], 0)
        e.refresh_from_db()
        self.assertEqual((e.state, e.current_step), ("active", 0))
        EmailSequence.objects.filter(pk=self.seq.pk).update(is_active=True)
        self.assertEqual(self.tick()[0]["sent"], 1)

    def test_editing_steps_under_a_running_enrolment_is_safe(self):
        e = self.enroll()
        SequenceEnrollment.objects.filter(pk=e.pk).update(current_step=3, next_send_at=timezone.now() - timedelta(minutes=1))
        out, stub = self.tick()
        e.refresh_from_db()
        self.assertEqual((out["completed"], len(stub.calls), e.state), (1, 0, "completed"))


class FailureAndDuplicateTests(SeqBase):
    def test_provider_failure_retries_later_then_gives_up(self):
        e = self.enroll()
        for attempt in (1, 2):
            out, _ = self.tick(ResendStub(fail=1))
            self.assertEqual(out["failed"], 1)
            e.refresh_from_db()
            self.assertEqual((e.attempts, e.current_step, e.state), (attempt, 0, "active"))
            self.assertGreater(e.next_send_at, timezone.now() + timedelta(minutes=10))
            self.assertFalse(EmailMessage.objects.exists())                                 # no half-sent row blocks the retry
            self.make_due(e)
        self.tick(ResendStub(fail=1))
        e.refresh_from_db()
        self.assertEqual((e.state, e.stop_reason), ("stopped", "send_failed"))

    def test_recovers_after_a_failure(self):
        e = self.enroll()
        self.tick(ResendStub(fail=1))
        self.make_due(e)
        out, stub = self.tick()
        e.refresh_from_db()
        self.assertEqual((out["sent"], e.current_step, e.attempts), (1, 1, 0))
        self.assertTrue(stub.calls[0][1]["idempotency_key"].endswith("-1"))                # new key after a failed attempt

    def test_two_overlapping_ticks_never_send_the_same_step_twice(self):
        e = self.enroll()
        stub = ResendStub()
        with mock.patch("resend.Emails.send", side_effect=stub):
            first = emailing.process_enrollment(e.id)
            second = emailing.process_enrollment(e.id)
            third = emailing.run_sequence_tick()
        self.assertEqual((first, second, third["sent"], len(stub.calls)), ("sent", "skipped", 0, 1))

    def test_the_database_itself_refuses_a_duplicate_step_message(self):
        e = self.enroll()
        self.tick()
        m = EmailMessage.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmailMessage.objects.create(thread=m.thread, direction="out", enrollment=e, step=m.step, subject="dup")


class StopRuleTests(SeqBase):
    def running(self):
        e = self.enroll()
        self.tick()
        e.refresh_from_db()
        self.assertEqual(e.state, "active")
        return e

    def assertStopped(self, e, reason):
        e.refresh_from_db()
        self.assertEqual((e.state, e.stop_reason, e.next_send_at), ("stopped", reason, None))
        self.make_due(e)
        self.assertEqual(self.tick()[0]["sent"], 0)                                          # and nothing more is ever sent

    def test_stops_when_the_lead_replies(self):
        e = self.running()
        thread = e.thread
        with mock.patch("crm.emailing.fetch_received", return_value={"from": "Asha <asha@gmail.com>", "to": [f"t-{thread.token}@reply.raritydental.com"], "subject": "Re: hi", "text": "Yes please", "headers": {}}), \
                mock.patch("crm.tasks.notify_email_reply"):
            body = json.dumps({"type": "email.received", "data": {"email_id": "in_9", "from": "Asha <asha@gmail.com>", "to": [f"t-{thread.token}@reply.raritydental.com"], "subject": "Re: hi"}}).encode()
            r = Client().post("/api/crm/email/webhook/", body, content_type="application/json", **sign(body, "m_in_9"))
        self.assertEqual(r.status_code, 200)
        self.assertStopped(e, "replied")

    def test_stops_when_the_lead_progresses(self):
        e = self.running()
        services.change_status(Lead.objects.get(pk=self.lead.pk), "consult_booked")
        self.assertStopped(e, "progressed")

    def test_stays_running_while_status_is_still_allowed(self):
        e = self.running()
        services.change_status(Lead.objects.get(pk=self.lead.pk), "contacted")               # new -> contacted is within "new, contacted"
        e.refresh_from_db()
        self.assertEqual(e.state, "active")

    def test_stops_when_an_appointment_is_booked(self):
        e = self.running()
        Appointment.objects.create(lead=self.lead, start_at=timezone.now() + timedelta(days=2), end_at=timezone.now() + timedelta(days=2, minutes=30))
        self.assertStopped(e, "booked")

    def test_stops_on_unsubscribe_bounce_and_complaint(self):
        for how, reason in (("optout", "unsubscribed"), ("invalid", "bad_email")):
            lead = Lead.objects.create(name=f"L-{how}", email=f"{how}@x.com")
            e = emailing.enroll(lead, self.seq)
            (emailing.mark_opt_out if how == "optout" else emailing.mark_invalid)(lead)
            self.assertStopped(e, reason)
        # a spam complaint arriving as a webhook
        lead = Lead.objects.create(name="Complainer", email="c@x.com")
        e = emailing.enroll(lead, self.seq)
        self.tick()
        m = EmailMessage.objects.get(enrollment=e)
        body = json.dumps({"type": "email.complained", "data": {"email_id": m.resend_id}}).encode()
        Client().post("/api/crm/email/webhook/", body, content_type="application/json", **sign(body, "m_cmp"))
        self.assertTrue(Lead.objects.get(pk=lead.pk).email_opt_out)
        self.assertStopped(e, "unsubscribed")

    def test_stops_when_the_lead_is_archived_or_loses_its_email(self):
        e = self.running()
        self.lead.soft_delete()
        self.make_due(e)
        self.assertEqual(self.tick()[0]["stopped"], 1)
        e.refresh_from_db()
        self.assertEqual(e.stop_reason, "deleted")
        other = Lead.objects.create(name="NoMail", email="n@x.com")
        e2 = emailing.enroll(other, self.seq)
        Lead.objects.filter(pk=other.pk).update(email="")
        self.tick()
        e2.refresh_from_db()
        self.assertEqual(e2.stop_reason, "no_email")

    def test_a_person_can_stop_it(self):
        e = self.running()
        r = self.as_(self.rep).post(f"/api/crm/enrollments/{e.id}/stop/")
        self.assertEqual(r.status_code, 200)
        self.assertStopped(e, "manual")
        self.assertEqual(self.as_(self.rep).post(f"/api/crm/enrollments/{e.id}/stop/").status_code, 400)

    def test_a_human_email_to_an_opted_out_lead_is_still_allowed_but_automation_is_not(self):
        Lead.objects.filter(pk=self.lead.pk).update(email_opt_out=True)
        self.lead.refresh_from_db()
        with mock.patch("resend.Emails.send", side_effect=ResendStub()) as m:
            emailing.send_to_lead(self.lead, subject="Personal note", body="Hi", by=self.rep)
            self.assertEqual(m.call_count, 1)
            with self.assertRaisesRegex(emailing.EmailError, "unsubscribed"):
                emailing.send_to_lead(self.lead, subject="Promo", body="Hi", by=self.rep, automated=True)
            params = m.call_args_list[0].args[0]
        self.assertNotIn("unsubscribe", params["text"].lower())                              # one-to-one mail carries no marketing footer


class AutoEnrolTests(SeqBase):
    def setUp(self):
        super().setUp()
        self.seq.auto_enroll, self.seq.auto_enroll_sources = True, ["website", "google_ads"]
        self.seq.save()

    def test_matching_new_leads_are_enrolled_and_sent_straight_away(self):
        lead, created = services.create_lead(name="New Person", email="new@x.com", phone="+919811100001", source="website")
        self.assertTrue(created)
        self.assertEqual(SequenceEnrollment.objects.get(lead=lead).sequence_id, self.seq.id)
        self.kick.assert_called_once()

    def test_non_matching_or_unreachable_leads_are_not(self):
        services.create_lead(name="Manual", email="m@x.com", phone="+919811100002", source="manual")
        services.create_lead(name="NoEmail", phone="+919811100003", source="website")
        self.seq.is_active = False
        self.seq.save()
        services.create_lead(name="Paused", email="p@x.com", phone="+919811100004", source="website")
        self.assertEqual(SequenceEnrollment.objects.count(), 0)
        self.kick.assert_not_called()

    def test_the_website_form_path_enrols_and_a_repeat_submission_does_not(self):
        with override_settings(CRM_INTAKE_API_KEY="k"):
            payload = {"firstName": "Web", "lastName": "Visitor", "email": "web@x.com", "contactNumber": "+919811100005", "country": "India",
                       "submissionUrl": "https://raritydental.com/contact-us"}
            for _ in range(2):
                r = APIClient().post("/api/crm/interest/", payload, format="json", HTTP_X_RARITY_KEY="k")
                self.assertEqual(r.status_code, 201)
        self.assertEqual(SequenceEnrollment.objects.filter(lead__email="web@x.com").count(), 1)

    def test_an_email_problem_can_never_block_lead_creation(self):
        with mock.patch("crm.emailing.auto_enroll", side_effect=RuntimeError("boom")):
            lead, created = services.create_lead(name="Safe", email="safe@x.com", phone="+919811100006", source="website")
        self.assertTrue(created and Lead.objects.filter(pk=lead.pk).exists())


class UnsubscribeTests(SeqBase):
    def sent_unsubscribe_url(self):
        self.enroll()
        _, stub = self.tick()
        return re.search(r"https://crm\.example\.com(/api/crm/email/unsubscribe/\S+/)", stub.calls[0][0]["text"]).group(1)

    def test_the_link_in_a_real_email_works_end_to_end(self):
        path = self.sent_unsubscribe_url()
        c = Client()
        page = c.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Yes, unsubscribe me", page.content)
        self.assertFalse(Lead.objects.get(pk=self.lead.pk).email_opt_out)                    # opening the link (a scanner!) changes nothing
        self.assertEqual(c.head(path).status_code, 200)
        r = c.post(path, "List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded")   # what mail apps send
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"You have been unsubscribed", r.content)
        lead = Lead.objects.get(pk=self.lead.pk)
        self.assertTrue(lead.email_opt_out)
        self.assertIsNotNone(lead.unsubscribed_at)
        self.assertEqual(SequenceEnrollment.objects.get().stop_reason, "unsubscribed")
        self.assertEqual(Note.objects.filter(lead=lead, body__startswith="Unsubscribed").count(), 1)
        c.post(path)                                                                          # clicking twice is harmless
        self.assertEqual(Note.objects.filter(lead=lead).count(), 1)

    def test_bad_or_tampered_links_are_rejected(self):
        path = self.sent_unsubscribe_url()
        for bad in (path[:-3] + "xx/", "/api/crm/email/unsubscribe/garbage/", path.replace("unsubscribe/", "unsubscribe/x")):
            self.assertEqual(Client().post(bad).status_code, 400, bad)
        self.assertFalse(Lead.objects.get(pk=self.lead.pk).email_opt_out)
        self.assertEqual(Client().put(path).status_code, 405)

    def test_an_unsubscribed_lead_never_gets_another_automated_email(self):
        path = self.sent_unsubscribe_url()
        Client().post(path)
        other_seq = EmailSequence.objects.create(name="Other", is_active=True, run_while_status=["new", "contacted"])
        EmailSequenceStep.objects.create(sequence=other_seq, position=0, delay_hours=0, template=self.tpls[0])
        with self.assertRaisesRegex(emailing.EmailError, "unsubscribed"):
            emailing.enroll(Lead.objects.get(pk=self.lead.pk), other_seq)


class ApiTests(SeqBase):
    def body(self, **kw):
        b = {"name": "Follow-up", "description": "d", "is_active": True, "auto_enroll": False, "auto_enroll_sources": [], "run_while_status": [],
             "steps": [{"delay_hours": 0, "template": self.tpls[0].id}, {"delay_hours": 24, "template": self.tpls[1].id}]}
        b.update(kw)
        return b

    def test_crud_permissions_and_step_ordering(self):
        self.assertEqual(self.as_(self.rep).post("/api/crm/sequences/", self.body(), format="json").status_code, 403)
        r = self.as_(self.mgr).post("/api/crm/sequences/", self.body(), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual([(s["position"], s["delay_hours"], s["template_name"]) for s in d["steps"]], [(0, 0, "T1"), (1, 24, "T2")])
        self.assertEqual(d["run_while_status"], ["new", "contacted"])                       # sensible default
        self.assertEqual(d["stats"]["active"], 0)
        r = self.as_(self.mgr).patch(f"/api/crm/sequences/{d['id']}/", {"steps": [{"delay_hours": 5, "template": self.tpls[2].id}]}, format="json")
        self.assertEqual([(s["position"], s["template_name"]) for s in r.json()["steps"]], [(0, "T3")])
        self.assertEqual(len(self.as_(self.rep).get("/api/crm/sequences/").json()), 2)       # everyone can read

    def test_validation(self):
        c = self.as_(self.mgr)
        EmailTemplate.objects.filter(pk=self.tpls[2].id).update(is_active=False)
        for bad in (self.body(steps=[]), self.body(auto_enroll=True, auto_enroll_sources=[]), self.body(auto_enroll_sources=["tiktok"]),
                    self.body(run_while_status=["nonsense"]), self.body(steps=[{"delay_hours": 24 * 400, "template": self.tpls[0].id}]),
                    self.body(steps=[{"delay_hours": 1, "template": self.tpls[2].id}]), self.body(name="Welcome")):
            self.assertEqual(c.post("/api/crm/sequences/", bad, format="json").status_code, 400, bad)

    def test_stats_reflect_what_happened(self):
        e = self.enroll()
        self.tick()
        emailing.stop_sequences(self.lead, "replied")
        s = self.as_(self.rep).get(f"/api/crm/sequences/{self.seq.id}/").json()["stats"]
        self.assertEqual((s["active"], s["stopped"], s["replied"], s["emails_sent"]), (0, 1, 1, 1))

    def test_delete_rules(self):
        e = self.enroll()
        c = self.as_(self.mgr)
        self.assertEqual(c.delete(f"/api/crm/sequences/{self.seq.id}/").status_code, 400)   # leads are mid-sequence
        emailing.stop_enrollment(e, "manual")
        self.assertEqual(c.delete(f"/api/crm/sequences/{self.seq.id}/").status_code, 204)
        r = c.delete(f"/api/crm/email-templates/{self.tpls[0].id}/")                          # now free of the sequence
        self.assertEqual(r.status_code, 204)

    def test_a_template_used_by_a_sequence_cannot_be_deleted(self):
        r = self.as_(self.mgr).delete(f"/api/crm/email-templates/{self.tpls[0].id}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("used by an email sequence", json.dumps(r.json()))
        self.assertTrue(EmailTemplate.objects.filter(pk=self.tpls[0].id).exists())

    def test_bulk_enrol_reports_who_was_skipped_and_why(self):
        ok = Lead.objects.create(name="Ok", email="ok@x.com")
        noemail = Lead.objects.create(name="NoMail")
        r = self.as_(self.mgr).post(f"/api/crm/sequences/{self.seq.id}/enroll/", {"lead_ids": [ok.id, noemail.id, self.lead.id, 99999]}, format="json").json()
        self.assertEqual(r["enrolled"], 2)
        self.assertEqual([(x["lead"], x["reason"]) for x in r["skipped"]], [("NoMail", "no email address")])
        self.assertEqual(self.as_(self.rep).post(f"/api/crm/sequences/{self.seq.id}/enroll/", {"lead_ids": [ok.id]}, format="json").status_code, 403)
        rows = self.as_(self.rep).get(f"/api/crm/sequences/{self.seq.id}/enrollments/?state=active").json()
        self.assertEqual(len(rows), 2)
        self.assertEqual({"state", "lead_name", "current_step", "total_steps", "next_send_at"} <= set(rows[0]), True)

    def test_a_rep_can_enrol_and_list_one_lead(self):
        c = self.as_(self.rep)
        r = c.post(f"/api/crm/leads/{self.lead.id}/enroll/", {"sequence_id": self.seq.id}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(c.post(f"/api/crm/leads/{self.lead.id}/enroll/", {"sequence_id": self.seq.id}, format="json").status_code, 400)
        rows = c.get(f"/api/crm/leads/{self.lead.id}/enrollments/").json()
        self.assertEqual((len(rows), rows[0]["sequence_name"], rows[0]["total_steps"]), (1, "Welcome", 3))

    def test_status_endpoint_reports_cap_and_usage_and_schedule_exists(self):
        self.enroll()
        self.tick()
        s = self.as_(self.rep).get("/api/crm/email/status/").json()
        self.assertEqual((s["daily_cap"], s["sent_today"], s["send_window"]), (100, 1, "00:00-24:00"))
        call_command("setup_schedules", stdout=mock.MagicMock())
        from django_q.models import Schedule
        sch = Schedule.objects.get(name="Email sequences")
        self.assertEqual((sch.func, sch.minutes), ("crm.tasks.run_sequence_tick", 5))
