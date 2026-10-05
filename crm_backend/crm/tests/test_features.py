import io
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from crm.models import Appointment, Branch, CallSession, ContactLog, CRMUser, Doctor, Lead, Role

User = get_user_model()
SYNC = {"name": "t", "orm": "default", "sync": True}


def make_user(name, role=Role.REP):
    u = User.objects.create_user(name, f"{name}@x.com", "pass12345", first_name=name)
    return CRMUser.objects.create(user=u, role=role)


class Base(APITestCase):
    def setUp(self):
        self.manager = make_user("boss", Role.MANAGER)
        self.rep = make_user("rep1")
        self.lead = Lead.objects.create(name="Asha", phone="+919810012345", email="asha@x.com", assigned_to=self.rep)

    def as_(self, p):
        c = APIClient()
        c.force_authenticate(p.user)
        return c


# ---------------- lead edit ----------------
class LeadEditTests(Base):
    def test_phone_is_normalised_on_edit(self):
        r = self.as_(self.rep).patch(f"/api/crm/leads/{self.lead.id}/", {"phone": "98100 55555"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["phone"], "+919810055555")

    def test_local_number_is_read_in_the_leads_country(self):
        c = self.as_(self.rep)
        r = c.patch(f"/api/crm/leads/{self.lead.id}/", {"country": "United Kingdom", "phone": "07911 123456"}, format="json")
        self.assertEqual(r.json()["phone"], "+447911123456")
        # already-international numbers are never reinterpreted
        r = c.patch(f"/api/crm/leads/{self.lead.id}/", {"phone": "+919810012345"}, format="json")
        self.assertEqual(r.json()["phone"], "+919810012345")
        # new lead with a UK local number + country
        r = c.post("/api/crm/leads/", {"name": "Tom", "country": "UK", "phone": "07700 900123"}, format="json")
        self.assertEqual(Lead.objects.get(pk=r.json()["id"]).phone, "+447700900123")

    def test_invalid_phone_rejected(self):
        r = self.as_(self.rep).patch(f"/api/crm/leads/{self.lead.id}/", {"phone": "abc"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("phone", r.json())

    def test_edit_to_existing_phone_or_email_rejected(self):
        other = Lead.objects.create(name="Other", phone="+919811111111", email="o@x.com")
        c = self.as_(self.rep)
        self.assertEqual(c.patch(f"/api/crm/leads/{other.id}/", {"phone": "+919810012345"}, format="json").status_code, 400)
        self.assertEqual(c.patch(f"/api/crm/leads/{other.id}/", {"email": "ASHA@x.com"}, format="json").status_code, 400)
        # keeping your own values is fine
        self.assertEqual(c.patch(f"/api/crm/leads/{other.id}/", {"phone": "+919811111111", "name": "Other 2"}, format="json").status_code, 200)

    def test_travel_dates_and_profile_sections(self):
        c = self.as_(self.rep)
        bad = c.patch(f"/api/crm/leads/{self.lead.id}/", {"travel_from": "2026-11-10", "travel_to": "2026-11-01"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = c.patch(f"/api/crm/leads/{self.lead.id}/", {"is_international": True, "travel_from": "2026-11-01", "travel_to": "2026-11-10",
                     "companions": 2, "quote_amount": "150000.00", "treatment_interests": ["implants"], "consent_recording": True}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.consent_recording)
        self.assertEqual(self.lead.companions, 2)

    def test_unknown_treatment_rejected(self):
        r = self.as_(self.rep).patch(f"/api/crm/leads/{self.lead.id}/", {"treatment_interests": ["magic"]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_duplicates_endpoint(self):
        Lead.objects.create(name="Asha V", phone="+919810012345")
        Lead.objects.create(name="Unrelated", phone="+919822222222")
        r = self.as_(self.rep).get(f"/api/crm/leads/{self.lead.id}/duplicates/")
        self.assertEqual([d["name"] for d in r.json()], ["Asha V"])


# ---------------- CSV import ----------------
class ImportTests(Base):
    def upload(self, text, client=None):
        f = SimpleUploadedFile("l.csv", text.encode(), content_type="text/csv")
        return (client or self.as_(self.manager)).post("/api/crm/leads/import/", {"file": f}, format="multipart")

    def test_import_reports_created_duplicates_errors(self):
        csv = "Name,Phone,Email,Country\nNew One,98111 22222,n@x.com,India\nDup,+919810012345,,India\n,123,,\nBad Phone,zzz,,\n"
        r = self.upload(csv)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["created"], body["duplicates"], len(body["errors"])), (1, 1, 2))
        self.assertEqual(Lead.objects.get(name="New One").phone, "+919811122222")

    def test_import_reads_local_numbers_in_row_country(self):
        r = self.upload("name,phone,country\nTom,07911 123456,United Kingdom\n")
        self.assertEqual(r.json()["created"], 1)
        self.assertEqual(Lead.objects.get(name="Tom").phone, "+447911123456")

    def test_import_needs_valid_header_and_manager(self):
        self.assertEqual(self.upload("foo,bar\n1,2\n").status_code, 400)
        self.assertEqual(self.upload("name,phone\nA,+919811122222\n", self.as_(self.rep)).status_code, 403)

    def test_import_too_large(self):
        with mock.patch("crm.views.leads.MAX_IMPORT_BYTES", 10):
            self.assertEqual(self.upload("name,phone\nA,+919811122222\n").status_code, 400)


# ---------------- Google Calendar ----------------
@override_settings(Q_CLUSTER=SYNC)
class CalendarTests(Base):
    def setUp(self):
        super().setUp()
        # django-q reads Q_CLUSTER at import, so run task functions inline instead.
        from django.utils.module_loading import import_string

        for target, effect in (("crm.tasks.transaction.on_commit", lambda f: f()),
                               ("crm.tasks.async_task", lambda path, *a: import_string(path)(*a))):
            patcher = mock.patch(target, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.doc = Doctor.objects.create(name="Dr X", calendar_id="doc@group.calendar.google.com")
        self.start = timezone.now() + timedelta(days=2)

    def book(self, **extra):
        body = {"lead": self.lead.id, "doctor": self.doc.id, "start_at": self.start.isoformat(),
                "end_at": (self.start + timedelta(minutes=30)).isoformat(), **extra}
        return self.as_(self.rep).post("/api/crm/appointments/", body, format="json")

    @mock.patch("crm.utils.gcal.upsert_event", return_value=("doc@group.calendar.google.com", "evt1"))
    @mock.patch("crm.utils.gcal.is_configured", return_value=True)
    def test_create_syncs_event(self, _c, upsert):
        r = self.book()
        self.assertEqual(r.status_code, 201)
        appt = Appointment.objects.get()
        self.assertEqual((appt.gcal_event_id, appt.gcal_sync_error), ("evt1", ""))
        self.assertEqual(upsert.call_count, 1)

    @mock.patch("crm.utils.gcal.is_configured", return_value=False)
    def test_unconfigured_is_a_noop(self, _c):
        self.assertEqual(self.book().status_code, 201)
        self.assertEqual(Appointment.objects.get().gcal_event_id, "")

    @mock.patch("crm.utils.gcal.delete_event")
    @mock.patch("crm.utils.gcal.upsert_event", return_value=("c", "evt1"))
    @mock.patch("crm.utils.gcal.is_configured", return_value=True)
    def test_cancel_and_delete_remove_event(self, _c, _u, delete):
        appt_id = self.book().json()["id"]
        self.as_(self.rep).patch(f"/api/crm/appointments/{appt_id}/", {"status": "cancelled"}, format="json")
        delete.assert_called_once_with("c", "evt1")
        self.assertEqual(Appointment.objects.get().gcal_event_id, "")
        # re-sync then hard delete
        Appointment.objects.update(gcal_event_id="evt2", gcal_calendar_id="c", status="booked")
        self.as_(self.rep).delete(f"/api/crm/appointments/{appt_id}/")
        self.assertEqual(delete.call_count, 2)

    @mock.patch("crm.utils.gcal.upsert_event", side_effect=RuntimeError("quota exceeded"))
    @mock.patch("crm.utils.gcal.is_configured", return_value=True)
    def test_sync_failure_keeps_appointment_and_records_error(self, _c, _u):
        try:
            self.book()
        except RuntimeError:
            pass  # django-q raises in sync mode; in production the worker retries
        appt = Appointment.objects.get()
        self.assertIn("quota", appt.gcal_sync_error)

    @mock.patch("crm.utils.gcal.is_configured", return_value=True)
    def test_missing_calendar_id_is_reported(self, _c):
        self.doc.calendar_id = ""
        self.doc.save()
        self.book()
        self.assertIn("No calendar", Appointment.objects.get().gcal_sync_error)


# ---------------- Twilio calling ----------------
TW = dict(TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="tok123", TWILIO_API_KEY="SKtest", TWILIO_API_SECRET="sec",
          TWILIO_TWIML_APP_SID="APtest", TWILIO_FROM_NUMBER="+14155550100", PUBLIC_BASE_URL="https://crm.example.com",
          TWILIO_RECORD_CALLS=False)


def sign(path, params, token="tok123", base="https://crm.example.com"):
    from twilio.request_validator import RequestValidator

    return RequestValidator(token).compute_signature(base + path, params)


@override_settings(**TW)
class CallTests(Base):
    def webhook(self, path, params, signature=None):
        sig = sign(path, params) if signature is None else signature
        return APIClient().post(path, params, HTTP_X_TWILIO_SIGNATURE=sig)

    def session(self, **kw):
        return CallSession.objects.create(lead=self.lead, crm_user=self.rep, to_number=self.lead.phone, **kw)

    @override_settings(TWILIO_API_KEY="")
    def test_not_configured_returns_503(self):
        self.assertEqual(self.as_(self.rep).get("/api/crm/calls/token/").status_code, 503)

    def test_token_is_jwt_for_the_user(self):
        r = self.as_(self.rep).get("/api/crm/calls/token/")
        self.assertEqual(r.status_code, 200)
        import jwt
        claims = jwt.decode(r.json()["token"], options={"verify_signature": False})
        self.assertEqual(claims["grants"]["identity"], f"crm-{self.rep.id}")
        self.assertEqual(claims["grants"]["voice"]["outgoing"]["application_sid"], "APtest")

    def test_initiate_requires_phone(self):
        self.lead.phone = ""
        self.lead.save()
        self.assertEqual(self.as_(self.rep).post("/api/crm/calls/initiate/", {"lead_id": self.lead.id}, format="json").status_code, 400)

    def test_full_call_lifecycle(self):
        c = self.as_(self.rep)
        sid = c.post("/api/crm/calls/initiate/", {"lead_id": self.lead.id}, format="json").json()["id"]

        # Twilio asks for TwiML; the browser claims a DIFFERENT number — must be ignored.
        p = {"CallSid": "CAparent", "From": f"client:crm-{self.rep.id}", "To": "+19999999999", "SessionId": str(sid)}
        r = self.webhook("/api/crm/calls/voice-twiml/", p)
        self.assertEqual(r.status_code, 200)
        xml = r.content.decode()
        self.assertIn("+919810012345", xml)
        self.assertNotIn("9999999999", xml)
        self.assertIn('callerId="+14155550100"', xml)
        self.assertNotIn("record", xml)  # recording off by default
        self.assertEqual(CallSession.objects.get().call_sid, "CAparent")

        # answered, then completed on the child leg
        for status, extra in [("ringing", {}), ("answered", {}), ("completed", {"CallDuration": "74"})]:
            self.webhook("/api/crm/calls/status-callback/", {"ParentCallSid": "CAparent", "CallSid": "CAchild", "CallStatus": status, **extra})
        s = CallSession.objects.get()
        self.assertEqual((s.status, s.duration_sec), ("completed", 74))
        self.assertIsNotNone(s.started_at)
        self.assertEqual(c.get(f"/api/crm/calls/{sid}/").json()["status"], "completed")

        # outcome logging: once only, updates lead
        r = c.post(f"/api/crm/calls/{sid}/log/", {"outcome": "interested", "notes": "wants implants"}, format="json")
        self.assertEqual(r.status_code, 201)
        log = ContactLog.objects.get()
        self.assertEqual((log.method, log.duration_sec, log.outcome, log.call_session_id), ("call", 74, "interested", sid))
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.last_contacted_at)
        self.assertEqual(c.post(f"/api/crm/calls/{sid}/log/", {"outcome": "interested"}, format="json").status_code, 409)

    def test_bad_signature_rejected_on_every_webhook(self):
        for path in ("voice-twiml", "status-callback", "recording-callback"):
            r = self.webhook(f"/api/crm/calls/{path}/", {"CallSid": "x"}, signature="forged")
            self.assertEqual(r.status_code, 403, path)
        wrong_token = sign("/api/crm/calls/status-callback/", {"CallSid": "x"}, token="other")
        self.assertEqual(self.webhook("/api/crm/calls/status-callback/", {"CallSid": "x"}, signature=wrong_token).status_code, 403)

    def test_voice_twiml_refuses_other_users_stale_or_reused_sessions(self):
        other = make_user("rep2")
        s = self.session()
        # wrong identity
        r = self.webhook("/api/crm/calls/voice-twiml/", {"CallSid": "C1", "From": f"client:crm-{other.id}", "SessionId": str(s.id)})
        self.assertIn("could not be placed", r.content.decode())
        self.assertEqual(CallSession.objects.get().call_sid, "")
        # stale
        CallSession.objects.update(created_at=timezone.now() - timedelta(minutes=10))
        r = self.webhook("/api/crm/calls/voice-twiml/", {"CallSid": "C1", "From": f"client:crm-{self.rep.id}", "SessionId": str(s.id)})
        self.assertIn("could not be placed", r.content.decode())
        # reuse
        CallSession.objects.update(created_at=timezone.now(), call_sid="C0")
        r = self.webhook("/api/crm/calls/voice-twiml/", {"CallSid": "C2", "From": f"client:crm-{self.rep.id}", "SessionId": str(s.id)})
        self.assertIn("could not be placed", r.content.decode())
        # unknown / garbage session
        r = self.webhook("/api/crm/calls/voice-twiml/", {"CallSid": "C3", "From": "client:crm-1", "SessionId": "abc"})
        self.assertIn("could not be placed", r.content.decode())

    @override_settings(TWILIO_RECORD_CALLS=True)
    def test_recording_flow_and_playback_proxy(self):
        s = self.session()
        r = self.webhook("/api/crm/calls/voice-twiml/", {"CallSid": "CAp", "From": f"client:crm-{self.rep.id}", "SessionId": str(s.id)})
        self.assertIn('record="record-from-answer-dual"', r.content.decode())
        self.webhook("/api/crm/calls/recording-callback/", {"CallSid": "CAp", "RecordingSid": "RE1", "RecordingStatus": "completed",
                     "RecordingUrl": "https://api.twilio.com/2010-04-01/Accounts/ACtest/Recordings/RE1"})
        s.refresh_from_db()
        self.assertTrue(s.recording_url.endswith("RE1"))
        fake = mock.Mock(status_code=200)
        fake.iter_content.return_value = [b"ID3audio"]
        with mock.patch("crm.utils.twilio_service.open_recording", return_value=fake):
            r = self.as_(self.rep).get(f"/api/crm/calls/{s.id}/recording/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "audio/mpeg"))
        self.assertEqual(self.as_(self.rep).get(f"/api/crm/calls/{self.session().id}/recording/").status_code, 404)

    def test_non_crm_user_cannot_call(self):
        u = User.objects.create_user("outsider", password="x")
        c = APIClient()
        c.force_authenticate(u)
        self.assertEqual(c.get("/api/crm/calls/token/").status_code, 403)
        self.assertEqual(c.post("/api/crm/calls/initiate/", {"lead_id": self.lead.id}, format="json").status_code, 403)

    def test_call_history_filter_by_lead(self):
        self.session()
        other = Lead.objects.create(name="Z", phone="+919833333333")
        CallSession.objects.create(lead=other, crm_user=self.rep, to_number=other.phone)
        r = self.as_(self.rep).get(f"/api/crm/calls/?lead={self.lead.id}")
        self.assertEqual(r.json()["count"], 1)


@override_settings(GOOGLE_DEFAULT_CALENDAR_ID="default@group.calendar.google.com", CRM_BASE_URL="https://crm.example.com")
class GcalAdapterTests(Base):
    def make_appt(self, doc=None):
        branch = Branch.objects.create(name="Gurugram")
        start = timezone.now() + timedelta(days=1)
        return Appointment.objects.create(lead=self.lead, doctor=doc, branch=branch, start_at=start, end_at=start + timedelta(minutes=30))

    def fake_service(self):
        svc = mock.MagicMock()
        svc.events.return_value.insert.return_value.execute.return_value = {"id": "new1"}
        svc.events.return_value.update.return_value.execute.return_value = {"id": "evt9"}
        return svc

    def test_insert_event_body_and_default_calendar(self):
        from crm.utils import gcal

        appt = self.make_appt()
        svc = self.fake_service()
        with mock.patch.object(gcal, "_service", return_value=svc):
            cal, eid = gcal.upsert_event(appt)
        self.assertEqual((cal, eid), ("default@group.calendar.google.com", "new1"))
        body = svc.events.return_value.insert.call_args.kwargs["body"]
        self.assertIn("Asha", body["summary"])
        self.assertIn("https://crm.example.com/leads/", body["description"])
        self.assertEqual(body["location"], "Gurugram")
        self.assertIn("T", body["start"]["dateTime"])

    def test_update_in_place_and_move_between_calendars(self):
        from crm.utils import gcal

        doc = Doctor.objects.create(name="Dr Y", calendar_id="y@group.calendar.google.com")
        appt = self.make_appt(doc)
        appt.gcal_event_id, appt.gcal_calendar_id = "evt9", "y@group.calendar.google.com"
        svc = self.fake_service()
        with mock.patch.object(gcal, "_service", return_value=svc):
            self.assertEqual(gcal.upsert_event(appt), ("y@group.calendar.google.com", "evt9"))
            svc.events.return_value.update.assert_called_once()
            # doctor's calendar changes -> old event deleted, new one inserted
            appt.gcal_calendar_id = "old@group.calendar.google.com"
            with mock.patch.object(gcal, "delete_event") as delete:
                self.assertEqual(gcal.upsert_event(appt)[1], "new1")
                delete.assert_called_once_with("old@group.calendar.google.com", "evt9")

    def test_delete_ignores_already_gone(self):
        from googleapiclient.errors import HttpError
        from crm.utils import gcal

        svc = mock.MagicMock()
        svc.events.return_value.delete.return_value.execute.side_effect = HttpError(mock.Mock(status=404), b"gone")
        with mock.patch.object(gcal, "_service", return_value=svc):
            gcal.delete_event("c", "e")  # must not raise
        svc.events.return_value.delete.return_value.execute.side_effect = HttpError(mock.Mock(status=500), b"boom")
        with mock.patch.object(gcal, "_service", return_value=svc), self.assertRaises(HttpError):
            gcal.delete_event("c", "e")
