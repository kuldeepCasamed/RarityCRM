import base64
import hashlib
import hmac
import json
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from crm import emailing
from crm.models import ContactLog, CRMUser, EmailMessage, EmailThread, EmailTemplate, Lead, Role, Task, WebhookEvent

User = get_user_model()
SECRET = "whsec_" + base64.b64encode(b"unit-test-signing-key-0123456789").decode()
CFG = dict(RESEND_API_KEY="re_test", EMAIL_REPLY_DOMAIN="reply.raritydental.com", RESEND_WEBHOOK_SECRET=SECRET,
           EMAIL_VERIFIED_DOMAINS=["raritydental.com"], EMAIL_DEFAULT_FROM="hello@raritydental.com", CLINIC_NAME="Rarity Dental",
           PUBLIC_BASE_URL="https://crm.example.com")


def make_user(name, role=Role.REP, email=None, **profile):
    u = User.objects.create_user(name, email or f"{name}@raritydental.com", "pass12345", first_name=name.title(), last_name="Smith")
    return CRMUser.objects.create(user=u, role=role, **profile)


def sign(body: bytes, msg_id="msg_1", ts=None, secret=SECRET):
    ts = str(ts or int(time.time()))
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"HTTP_SVIX_ID": msg_id, "HTTP_SVIX_TIMESTAMP": ts, "HTTP_SVIX_SIGNATURE": f"v1,{sig}"}


class PureFunctionTests(SimpleTestCase):
    def test_quoted_history_is_dropped(self):
        gmail = "Yes, Tuesday works.\nThanks\n\nOn Tue, 6 Oct 2026 at 10:00, Rarity Dental <hello@raritydental.com> wrote:\n> Hi Asha\n> Can you come in?"
        self.assertEqual(emailing.strip_quoted_reply(gmail), "Yes, Tuesday works.\nThanks")
        wrapped = "Sounds good\nOn Tue, 6 Oct 2026 at 10:00 AM, Rarity Dental Team\n<hello@raritydental.com> wrote:\n> old"
        self.assertEqual(emailing.strip_quoted_reply(wrapped), "Sounds good")
        outlook = "I'll be there\n\nFrom: Rarity Dental\nSent: Tuesday\nTo: Asha\nSubject: Appointment"
        self.assertEqual(emailing.strip_quoted_reply(outlook), "I'll be there")
        self.assertEqual(emailing.strip_quoted_reply("Hello\n-----Original Message-----\nold"), "Hello")
        # if the whole thing is quoted, keep it rather than showing an empty message
        self.assertEqual(emailing.strip_quoted_reply("> only quoted"), "> only quoted")
        self.assertEqual(emailing.strip_quoted_reply("A line that mentions On and wrote: inline."), "A line that mentions On and wrote: inline.")

    def test_html_to_text_removes_markup_and_scripts(self):
        t = emailing.html_to_text("<p>Hi <b>there</b></p><script>alert(1)</script><style>p{}</style><div>Second<br>line</div>")
        self.assertNotIn("<", t)
        self.assertNotIn("alert", t)
        self.assertIn("Hi there", t)
        self.assertIn("Second", t)

    def test_merge_fields(self):
        lead = Lead(name="Asha Verma", treatment_interests=["implants"])
        v = emailing.merge_values(lead, "Dr Kuldeep")
        self.assertEqual(emailing.render_merge("Hi {{first_name}}, re {{ treatment }} - {{nope}}.", v), "Hi Asha, re Dental Implants - .")

    def test_html_is_escaped(self):
        html = emailing.text_to_html("Hello <script>alert(1)</script>\n\nSecond & third")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Second &amp; third", html)
        self.assertEqual(html.count("<p "), 2)

    def test_signature_verification(self):
        body = b'{"type":"email.delivered"}'
        h = sign(body)
        headers = {"svix-id": h["HTTP_SVIX_ID"], "svix-timestamp": h["HTTP_SVIX_TIMESTAMP"], "svix-signature": h["HTTP_SVIX_SIGNATURE"]}
        self.assertTrue(emailing.verify_signature(SECRET, headers, body))
        self.assertFalse(emailing.verify_signature(SECRET, headers, body + b" "))                      # tampered body
        self.assertFalse(emailing.verify_signature("whsec_" + base64.b64encode(b"other-key-other-key-other-key!").decode(), headers, body))
        self.assertFalse(emailing.verify_signature("", headers, body))                                   # no secret configured
        self.assertFalse(emailing.verify_signature(SECRET, {k: v for k, v in headers.items() if k != "svix-id"}, body))
        old = sign(body, ts=int(time.time()) - 3600)
        self.assertFalse(emailing.verify_signature(SECRET, {"svix-id": "msg_1", "svix-timestamp": old["HTTP_SVIX_TIMESTAMP"], "svix-signature": old["HTTP_SVIX_SIGNATURE"]}, body))
        multi = dict(headers, **{"svix-signature": "v1,AAAA " + headers["svix-signature"]})            # rotated keys: any match wins
        self.assertTrue(emailing.verify_signature(SECRET, multi, body))


@override_settings(**CFG)
class Base(APITestCase):
    def setUp(self):
        self.mgr = make_user("boss", Role.MANAGER)
        self.rep = make_user("rep1")
        self.lead = Lead.objects.create(name="Asha Verma", email="asha@gmail.com", phone="+919810012345", assigned_to=self.rep)

    def as_(self, p):
        c = APIClient()
        c.force_authenticate(p.user)
        return c

    def hook(self, payload, msg_id="msg_1", **kw):
        body = json.dumps(payload).encode()
        return Client().post("/api/crm/email/webhook/", body, content_type="application/json", **sign(body, msg_id, **kw))


class SenderTests(Base):
    def test_verified_domain_address_is_used_others_fall_back_with_name_kept(self):
        a = make_user("anita", email="anita@raritydental.com")
        i = emailing.sender_identity(a)
        self.assertEqual((i["address"], i["used_fallback"], i["formatted"]), ("anita@raritydental.com", False, "Anita Smith <anita@raritydental.com>"))
        b = make_user("kuldeep", email="kuldeep@casamed.in")
        i = emailing.sender_identity(b)
        self.assertEqual((i["address"], i["used_fallback"]), ("hello@raritydental.com", True))
        self.assertTrue(i["formatted"].startswith("Kuldeep Smith <"))
        b.email_from = "kuldeep@raritydental.com"
        self.assertEqual(emailing.sender_identity(b)["address"], "kuldeep@raritydental.com")  # explicit sender wins when verified


class SendingTests(Base):
    _n = 0

    def send(self, **kw):
        SendingTests._n += 1
        with mock.patch("resend.Emails.send", return_value={"id": f"re_out_{SendingTests._n}"}) as m:
            msg = emailing.send_to_lead(self.lead, subject=kw.pop("subject", "Your visit"), body=kw.pop("body", "Hello {{first_name}}"), by=self.rep, **kw)
        return msg, m.call_args.args[0]

    def test_first_email_creates_thread_and_routes_replies_to_the_crm(self):
        self.rep.email_signature = "Rep One\nRarity Dental"
        self.rep.save()
        msg, p = self.send()
        thread = msg.thread
        self.assertEqual(p["to"], ["asha@gmail.com"])
        self.assertEqual(p["reply_to"], f"t-{thread.token}@reply.raritydental.com")
        self.assertEqual(p["from"], "Rep1 Smith <rep1@raritydental.com>")
        self.assertIn("Hello Asha", p["text"])
        self.assertTrue(p["text"].endswith("Rep One\nRarity Dental"))
        self.assertNotIn("headers", p)                                  # first message: nothing to thread onto
        self.assertEqual((msg.status, msg.resend_id.startswith("re_out_"), thread.lead_id, thread.needs_reply), ("sent", True, self.lead.id, False))
        self.assertTrue(ContactLog.objects.filter(lead=self.lead, method="email", direction="outbound").exists())
        self.assertIsNotNone(Lead.objects.get(pk=self.lead.pk).last_contacted_at)

    def test_follow_up_is_threaded_with_reply_headers(self):
        first, _ = self.send()
        EmailMessage.objects.create(thread=first.thread, direction="in", status="received", subject="Re: Your visit", rfc_message_id="<abc@mail.gmail.com>", body_text="ok")
        msg, p = self.send(thread=first.thread, subject="Your visit", body="See you")
        self.assertEqual(p["subject"], "Re: Your visit")
        self.assertEqual(p["headers"]["In-Reply-To"], "<abc@mail.gmail.com>")
        self.assertEqual(p["headers"]["References"], "<abc@mail.gmail.com>")
        self.assertEqual(msg.thread_id, first.thread_id)

    def test_html_in_a_leads_name_cannot_inject_markup(self):
        self.lead.name = '<img src=x onerror=alert(1)> Asha'
        msg, p = self.send(body="Hi {{name}}")
        self.assertNotIn("<img", p["html"])
        self.assertIn("&lt;img", p["html"])

    def test_copy_to_sender_uses_bcc(self):
        _, p = self.send(copy_to_sender=True)
        self.assertEqual(p["bcc"], ["rep1@raritydental.com"])

    def test_guards(self):
        for kw, text in [(dict(email=""), "no email"), (dict(email_invalid=True), "bounced")]:
            Lead.objects.filter(pk=self.lead.pk).update(**{**dict(email="asha@gmail.com", email_invalid=False), **kw})
            self.lead.refresh_from_db()
            with self.assertRaisesRegex(emailing.EmailError, text):
                emailing.send_to_lead(self.lead, subject="s", body="b", by=self.rep)
        Lead.objects.filter(pk=self.lead.pk).update(email="asha@gmail.com", email_invalid=False)
        self.lead.refresh_from_db()
        with self.assertRaisesRegex(emailing.EmailError, "required"):
            emailing.send_to_lead(self.lead, subject="", body="b", by=self.rep)
        other = Lead.objects.create(name="Other", email="o@x.com")
        t = EmailThread.objects.create(lead=other, subject="x")
        with self.assertRaisesRegex(emailing.EmailError, "different lead"):
            emailing.send_to_lead(self.lead, subject="s", body="b", by=self.rep, thread=t)
        with override_settings(RESEND_API_KEY=""), self.assertRaisesRegex(emailing.EmailError, "isn't configured"):
            emailing.send_to_lead(self.lead, subject="s", body="b", by=self.rep)

    def test_provider_failure_is_recorded_and_reported(self):
        with mock.patch("resend.Emails.send", side_effect=RuntimeError("domain not verified")):
            with self.assertRaisesRegex(emailing.EmailError, "domain not verified"):
                emailing.send_to_lead(self.lead, subject="s", body="b", by=self.rep)
        m = EmailMessage.objects.get()
        self.assertEqual(m.status, "failed")
        self.assertIn("domain not verified", m.error)
        self.assertFalse(ContactLog.objects.filter(method="email").exists())  # a failed send is not logged as contact

    def test_api_send_list_preview_and_permissions(self):
        c = self.as_(self.rep)
        url = f"/api/crm/leads/{self.lead.id}/emails/"
        pv = c.post(url + "preview/", {"subject": "Hi {{first_name}}", "body": "About {{treatment}} - {{sender_name}}"}, format="json").json()
        self.assertEqual((pv["subject"], pv["from"]), ("Hi Asha", "Rep1 Smith <rep1@raritydental.com>"))
        with mock.patch("resend.Emails.send", return_value={"id": "re_9"}):
            r = c.post(url, {"subject": "Hello", "body": "Body text"}, format="json")
        self.assertEqual(r.status_code, 201)
        data = c.get(url).json()
        self.assertEqual(len(data["threads"]), 1)
        self.assertEqual(data["threads"][0]["messages"][0]["direction"], "out")
        self.assertTrue(data["can_email"] and data["reply_capture"])
        bad = c.post(url, {"subject": "", "body": ""}, format="json")
        self.assertEqual(bad.status_code, 400)
        outsider = APIClient()
        outsider.force_authenticate(User.objects.create_user("outsider", password="x"))
        self.assertEqual(outsider.get(url).status_code, 403)


class WebhookTests(Base):
    def received(self, email_id="in_1", to=None, from_="Asha Verma <asha@gmail.com>", subject="Re: Your visit", text="Yes, I can come on Tuesday.\n\nOn Mon, Rarity <hello@raritydental.com> wrote:\n> Hello", html="", message_id="<m1@mail.gmail.com>", attachments=None, **extra):
        thread = EmailThread.objects.filter(lead=self.lead).first() or EmailThread.objects.create(lead=self.lead, subject="Your visit")
        to = to if to is not None else [f"t-{thread.token}@reply.raritydental.com"]
        data = {"email_id": email_id, "from": from_, "to": to, "received_for": to, "subject": subject, "message_id": message_id}
        full = {"from": from_, "to": to, "subject": subject, "text": text, "html": html, "message_id": message_id,
                "headers": {"In-Reply-To": "<out@resend>"}, "attachments": attachments or [], **extra}
        return {"type": "email.received", "data": data}, full

    def post_received(self, payload, full, msg_id="msg_r1"):
        with mock.patch("crm.emailing.fetch_received", return_value=full), mock.patch("crm.tasks.notify_email_reply") as notify:
            r = self.hook(payload, msg_id)
        return r, notify

    # ---- security
    def test_unsigned_forged_and_unconfigured_are_rejected(self):
        body = json.dumps({"type": "email.delivered", "data": {}}).encode()
        self.assertEqual(Client().post("/api/crm/email/webhook/", body, content_type="application/json").status_code, 403)
        self.assertEqual(Client().post("/api/crm/email/webhook/", body, content_type="application/json", HTTP_SVIX_ID="x", HTTP_SVIX_TIMESTAMP=str(int(time.time())), HTTP_SVIX_SIGNATURE="v1,forged").status_code, 403)
        with override_settings(RESEND_WEBHOOK_SECRET=""):
            self.assertEqual(Client().post("/api/crm/email/webhook/", body, content_type="application/json", **sign(body)).status_code, 403)
        self.assertEqual(Client().get("/api/crm/email/webhook/").status_code, 405)

    def test_bad_json_with_valid_signature(self):
        body = b"not json"
        self.assertEqual(Client().post("/api/crm/email/webhook/", body, content_type="application/json", **sign(body)).status_code, 400)

    # ---- inbound replies
    def test_reply_is_stored_matched_and_triggers_the_automation(self):
        payload, full = self.received()
        r, notify = self.post_received(payload, full)
        self.assertEqual(r.status_code, 200)
        m = EmailMessage.objects.get(direction="in")
        self.assertEqual(m.body_text, "Yes, I can come on Tuesday.")
        self.assertIn("wrote:", m.body_full)
        self.assertEqual((m.thread.lead_id, m.thread.needs_reply, m.in_reply_to), (self.lead.id, True, "<out@resend>"))
        lead = Lead.objects.get(pk=self.lead.pk)
        self.assertEqual(lead.status, "contacted")                                       # new -> contacted
        task = Task.objects.get(lead=lead)
        self.assertEqual((task.priority, task.assigned_to_id, task.is_done), ("high", self.rep.id, False))
        self.assertTrue(ContactLog.objects.filter(lead=lead, method="email", direction="inbound").exists())
        self.assertIsNotNone(lead.last_contacted_at)
        notify.assert_called_once_with(lead.id, m.id)

    def test_retry_of_the_same_event_or_email_is_applied_once(self):
        payload, full = self.received()
        self.post_received(payload, full, "msg_r1")
        r, notify = self.post_received(payload, full, "msg_r1")                          # same svix-id
        self.assertEqual(json.loads(r.content)["status"], "duplicate")
        r2, _ = self.post_received(payload, full, "msg_r2")                              # new svix-id, same email
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(EmailMessage.objects.filter(direction="in").count(), 1)
        self.assertEqual(Task.objects.filter(lead=self.lead).count(), 1)
        notify.assert_not_called()

    def test_failed_processing_returns_500_and_a_retry_succeeds(self):
        payload, full = self.received()
        with mock.patch("crm.emailing.fetch_received", side_effect=emailing.EmailError("resend down")):
            self.assertEqual(self.hook(payload, "msg_r9").status_code, 500)
        self.assertFalse(WebhookEvent.objects.filter(event_id="msg_r9").exists())        # not marked done, so the retry is processed
        r, _ = self.post_received(payload, full, "msg_r9")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(EmailMessage.objects.filter(direction="in").count(), 1)

    def test_status_moves_forward_only(self):
        for status, expected in [("negotiating", "negotiating"), ("lost", "lost"), ("converted", "converted")]:
            Lead.objects.filter(pk=self.lead.pk).update(status=status)
            EmailMessage.objects.all().delete()
            payload, full = self.received(email_id=f"in_{status}")
            self.post_received(payload, full, f"m_{status}")
            self.assertEqual(Lead.objects.get(pk=self.lead.pk).status, expected, status)

    def test_without_the_token_it_matches_by_the_senders_address(self):
        EmailThread.objects.create(lead=self.lead, subject="Earlier chat")
        payload, full = self.received(to=["someone@raritydental.com"], from_="ASHA <Asha@Gmail.com>")
        self.post_received(payload, full)
        self.assertEqual(EmailMessage.objects.get().thread.lead_id, self.lead.id)
        self.assertEqual(EmailThread.objects.filter(lead=self.lead).count(), 1)         # reused the existing conversation

    def test_unknown_sender_goes_to_unmatched_and_can_be_assigned(self):
        payload, full = self.received(to=["hello@raritydental.com"], from_="Stranger <who@example.org>", subject="Price?")
        self.post_received(payload, full)
        t = EmailThread.objects.get(lead__isnull=True)
        self.assertEqual(self.as_(self.rep).get("/api/crm/inbox/?kind=unmatched").json()["count"], 1)
        self.assertEqual(self.as_(self.rep).get("/api/crm/inbox/count/").json()["unmatched"], 1)
        self.assertEqual(Task.objects.count(), 0)                                        # nothing automated until a human assigns it
        r = self.as_(self.rep).post(f"/api/crm/email-threads/{t.id}/assign/", {"lead_id": self.lead.id}, format="json")
        self.assertEqual(r.status_code, 200)
        t.refresh_from_db()
        self.assertEqual((t.lead_id, t.needs_reply), (self.lead.id, True))
        self.assertTrue(Task.objects.filter(lead=self.lead).exists())

    def test_html_only_reply_is_reduced_to_text_and_attachments_are_listed(self):
        payload, full = self.received(text="", html="<p>See photo</p><script>alert(1)</script><img src=x onerror=alert(2)>",
                                      attachments=[{"filename": "xray.jpg", "content_type": "image/jpeg", "size": 1234}])
        self.post_received(payload, full)
        m = EmailMessage.objects.get()
        self.assertEqual(m.body_text, "See photo")
        self.assertEqual((m.body_html, m.attachments[0]["filename"]), ("", "xray.jpg"))

    def test_stop_reply_marks_the_lead_opted_out(self):
        payload, full = self.received(text="STOP\n\nOn Mon, X wrote:\n> promo")
        self.post_received(payload, full)
        lead = Lead.objects.get(pk=self.lead.pk)
        self.assertTrue(lead.email_opt_out)
        self.assertIsNotNone(lead.unsubscribed_at)

    # ---- delivery events
    def sent_message(self):
        t = EmailThread.objects.create(lead=self.lead, subject="s")
        return EmailMessage.objects.create(thread=t, direction="out", status="sent", resend_id="out_1", subject="s")

    def event(self, etype, email_id="out_1", msg_id=None, **data):
        return self.hook({"type": etype, "data": {"email_id": email_id, **data}}, msg_id or f"{etype}-{email_id}-{len(data)}")

    def test_delivery_lifecycle_and_no_downgrade(self):
        m = self.sent_message()
        self.assertEqual(self.event("email.delivered").status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.status, "delivered")
        self.assertIsNotNone(m.delivered_at)
        self.event("email.delivery_delayed", msg_id="late")                             # an out-of-order older event
        m.refresh_from_db()
        self.assertEqual(m.status, "delivered")
        self.event("email.opened", msg_id="o1")
        self.event("email.clicked", msg_id="c1")
        m.refresh_from_db()
        self.assertTrue(m.opened_at and m.clicked_at)

    def test_hard_bounce_flags_the_address_but_a_soft_one_does_not(self):
        m = self.sent_message()
        self.event("email.bounced", bounce={"type": "Transient", "message": "mailbox full"}, msg_id="b1")
        self.assertFalse(Lead.objects.get(pk=self.lead.pk).email_invalid)
        self.event("email.bounced", bounce={"type": "Permanent", "message": "no such user"}, msg_id="b2")
        self.assertTrue(Lead.objects.get(pk=self.lead.pk).email_invalid)
        m.refresh_from_db()
        self.assertEqual((m.status, m.error), ("bounced", "no such user"))
        r = self.as_(self.rep).post(f"/api/crm/leads/{self.lead.id}/emails/", {"subject": "s", "body": "b"}, format="json")
        self.assertEqual(r.status_code, 400)                                             # refuses to email a dead address

    def test_spam_complaint_opts_the_lead_out(self):
        self.sent_message()
        self.event("email.complained")
        self.assertTrue(Lead.objects.get(pk=self.lead.pk).email_opt_out)

    def test_events_for_unknown_emails_are_ignored(self):
        self.assertEqual(self.event("email.delivered", email_id="nope").status_code, 200)


class InboxAndSettingsTests(Base):
    def setUp(self):
        super().setUp()
        self.t = EmailThread.objects.create(lead=self.lead, subject="Question", needs_reply=True)
        EmailMessage.objects.create(thread=self.t, direction="in", status="received", from_address="asha@gmail.com", subject="Question", body_text="Is parking available?")

    def test_inbox_lists_threads_needing_a_reply_and_resolve_clears_them(self):
        c = self.as_(self.rep)
        rows = c.get("/api/crm/inbox/").json()["results"]
        self.assertEqual((len(rows), rows[0]["lead_name"], rows[0]["preview"]), (1, "Asha Verma", "Is parking available?"))
        self.assertEqual(c.get("/api/crm/inbox/count/").json()["needs_reply"], 1)
        self.assertEqual(c.get("/api/crm/inbox/?mine=1").json()["count"], 1)
        self.assertEqual(self.as_(self.mgr).get("/api/crm/inbox/?mine=1").json()["count"], 0)
        self.assertEqual(c.post(f"/api/crm/email-threads/{self.t.id}/resolve/").status_code, 200)
        self.assertEqual(c.get("/api/crm/inbox/").json()["count"], 0)

    def test_lead_list_shows_and_filters_needs_reply(self):
        Lead.objects.create(name="Quiet Lead", email="q@x.com")
        c = self.as_(self.rep)
        rows = {l["name"]: l["needs_reply"] for l in c.get("/api/crm/leads/").json()["results"]}
        self.assertEqual(rows, {"Asha Verma": True, "Quiet Lead": False})
        self.assertEqual([l["name"] for l in c.get("/api/crm/leads/?needs_reply=true").json()["results"]], ["Asha Verma"])

    def test_deleted_leads_conversations_disappear_from_the_inbox(self):
        self.lead.soft_delete()
        self.assertEqual(self.as_(self.rep).get("/api/crm/inbox/count/").json()["needs_reply"], 0)

    def test_templates_crud_is_manager_only_for_writes(self):
        body = {"name": "Welcome", "subject": "Hi {{first_name}}", "body": "Thanks for your enquiry, {{first_name}}."}
        self.assertEqual(self.as_(self.rep).post("/api/crm/email-templates/", body, format="json").status_code, 403)
        self.assertEqual(self.as_(self.mgr).post("/api/crm/email-templates/", body, format="json").status_code, 201)
        self.assertEqual(self.as_(self.mgr).post("/api/crm/email-templates/", body, format="json").status_code, 400)  # unique name
        self.assertEqual(len(self.as_(self.rep).get("/api/crm/email-templates/").json()), 1)
        tid = EmailTemplate.objects.get().id
        self.assertEqual(self.as_(self.rep).delete(f"/api/crm/email-templates/{tid}/").status_code, 403)

    def test_status_checklist_and_own_sender_settings(self):
        c = self.as_(self.rep)
        s = c.get("/api/crm/email/status/").json()
        self.assertEqual((s["resend_key"], s["webhook_secret"], s["reply_domain"]), (True, True, "reply.raritydental.com"))
        self.assertEqual(s["webhook_url"], "https://crm.example.com/api/crm/email/webhook/")
        r = c.patch("/api/crm/me/", {"email_from": "rep1@raritydental.com", "email_signature": "Best,\nRep"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CRMUser.objects.get(pk=self.rep.pk).email_signature, "Best,\nRep")
