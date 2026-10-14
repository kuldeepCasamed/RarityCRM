import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from crm.models import CRMUser, PasswordResetCode, Role

User = get_user_model()
REQ = "/api/crm/auth/password-reset/request/"
CONFIRM = "/api/crm/auth/password-reset/confirm/"
SEND = "crm.views.password_reset.send_email"


def make_user(name="priya", email=None, active=True):
    u = User.objects.create_user(name, email if email is not None else f"{name}@x.com", "oldpass12345", first_name=name.title())
    CRMUser.objects.create(user=u, role=Role.REP, is_active=active)
    return u


class PasswordResetTests(APITestCase):
    def setUp(self):
        cache.clear()  # throttle counters live in the cache
        self.user = make_user()
        self.c = APIClient()

    def request_code(self, identifier="priya"):
        """Ask for a code and return it, read from the email that would have been sent."""
        with mock.patch(SEND) as send:
            r = self.c.post(REQ, {"identifier": identifier}, format="json")
        self.assertEqual(r.status_code, 200)
        if not send.called:
            return None, send
        body = send.call_args.args[2]
        return re.search(r">(\d{6})<", body).group(1), send

    def test_full_flow_new_password_works_for_login_and_old_one_does_not(self):
        code, send = self.request_code()
        self.assertEqual(send.call_args.args[0], ["priya@x.com"])
        r = self.c.post(CONFIRM, {"identifier": "priya", "code": code, "new_password": "brandNew-pass-77"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.post("/api/crm/auth/login/", {"username": "priya", "password": "brandNew-pass-77"}, format="json").status_code, 200)
        self.assertEqual(self.c.post("/api/crm/auth/login/", {"username": "priya", "password": "oldpass12345"}, format="json").status_code, 401)

    def test_identifier_can_be_email_and_is_case_insensitive(self):
        code, _ = self.request_code("PRIYA@X.COM")
        self.assertIsNotNone(code)
        r = self.c.post(CONFIRM, {"identifier": "Priya@x.com", "code": code, "new_password": "brandNew-pass-77"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_unknown_account_gets_same_answer_and_no_email(self):
        with mock.patch(SEND) as send:
            known = self.c.post(REQ, {"identifier": "priya"}, format="json")
            cache.clear()
            unknown = self.c.post(REQ, {"identifier": "nobody"}, format="json")
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(send.call_count, 1)

    def test_inactive_crm_user_or_no_email_gets_nothing(self):
        make_user("gone", active=False)
        make_user("noemail", email="")
        for who in ("gone", "noemail"):
            code, send = self.request_code(who)
            self.assertIsNone(code)
            self.assertFalse(send.called)
        self.assertEqual(PasswordResetCode.objects.count(), 0)

    def test_code_is_stored_hashed_not_plain(self):
        code, _ = self.request_code()
        row = PasswordResetCode.objects.get()
        self.assertNotIn(code, row.code_hash)
        self.assertEqual(len(row.code_hash), 64)

    def test_wrong_code_then_lockout_after_five_attempts_even_with_right_code(self):
        code, _ = self.request_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            r = self.c.post(CONFIRM, {"identifier": "priya", "code": wrong, "new_password": "brandNew-pass-77"}, format="json")
            self.assertEqual(r.status_code, 400)
        r = self.c.post(CONFIRM, {"identifier": "priya", "code": code, "new_password": "brandNew-pass-77"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.user.__class__.objects.get(pk=self.user.pk).check_password("oldpass12345"))

    def test_expired_code_rejected(self):
        code, _ = self.request_code()
        PasswordResetCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        r = self.c.post(CONFIRM, {"identifier": "priya", "code": code, "new_password": "brandNew-pass-77"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_code_is_single_use(self):
        code, _ = self.request_code()
        body = {"identifier": "priya", "code": code, "new_password": "brandNew-pass-77"}
        self.assertEqual(self.c.post(CONFIRM, body, format="json").status_code, 200)
        self.assertEqual(self.c.post(CONFIRM, {**body, "new_password": "another-pass-88"}, format="json").status_code, 400)

    def test_weak_password_is_rejected_without_burning_the_code(self):
        code, _ = self.request_code()
        r = self.c.post(CONFIRM, {"identifier": "priya", "code": code, "new_password": "short"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("new_password", r.json())
        r = self.c.post(CONFIRM, {"identifier": "priya", "code": code, "new_password": "brandNew-pass-77"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_new_request_invalidates_the_previous_code(self):
        first, _ = self.request_code()
        PasswordResetCode.objects.update(created_at=timezone.now() - timedelta(minutes=5))  # past the resend cooldown
        second, _ = self.request_code()
        if first != second:
            r = self.c.post(CONFIRM, {"identifier": "priya", "code": first, "new_password": "brandNew-pass-77"}, format="json")
            self.assertEqual(r.status_code, 400)
        r = self.c.post(CONFIRM, {"identifier": "priya", "code": second, "new_password": "brandNew-pass-77"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_resend_cooldown_sends_only_one_email(self):
        with mock.patch(SEND) as send:
            for _ in range(3):
                self.assertEqual(self.c.post(REQ, {"identifier": "priya"}, format="json").status_code, 200)
        self.assertEqual(send.call_count, 1)

    def test_hourly_cap_per_account(self):
        with mock.patch(SEND) as send:
            for _ in range(8):
                self.c.post(REQ, {"identifier": "priya"}, format="json")
                PasswordResetCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.assertEqual(send.call_count, 5)

    def test_reset_signs_out_existing_sessions(self):
        Token.objects.create(user=self.user)
        code, _ = self.request_code()
        self.c.post(CONFIRM, {"identifier": "priya", "code": code, "new_password": "brandNew-pass-77"}, format="json")
        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_missing_fields(self):
        self.assertEqual(self.c.post(CONFIRM, {}, format="json").status_code, 400)
        self.assertEqual(self.c.post(REQ, {}, format="json").status_code, 200)

    def test_email_failure_does_not_break_or_leak(self):
        with mock.patch(SEND, side_effect=RuntimeError("resend down")):
            r = self.c.post(REQ, {"identifier": "priya"}, format="json")
        self.assertEqual(r.status_code, 200)
