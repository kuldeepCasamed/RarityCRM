from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from crm.models import CRMUser, Role, TeamInvite

User = get_user_model()


def make_user(name, role=Role.REP):
    u = User.objects.create_user(name, f"{name}@x.com", "pass12345", first_name=name)
    return CRMUser.objects.create(user=u, role=role)


@override_settings(Q_CLUSTER={"name": "t", "orm": "default", "sync": True})
class TeamTests(APITestCase):
    def setUp(self):
        self.admin = make_user("admin", Role.ADMIN)
        self.manager = make_user("mgr", Role.MANAGER)
        self.rep = make_user("rep")

    def as_(self, p):
        c = APIClient()
        c.force_authenticate(p.user)
        return c

    def test_invite_accept_flow(self):
        r = self.as_(self.manager).post("/api/crm/team/invite/", {"email": "new@x.com", "role": "rep"}, format="json")
        self.assertEqual(r.status_code, 201)
        token = r.json()["token"]
        anon = APIClient()
        self.assertEqual(anon.get(f"/api/crm/team/accept-invite/{token}/").json()["email"], "new@x.com")
        r = anon.post(f"/api/crm/team/accept-invite/{token}/", {"first_name": "New", "password": "longenough1"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(CRMUser.objects.filter(user__username="new@x.com", role="rep").exists())
        # single use
        self.assertEqual(anon.get(f"/api/crm/team/accept-invite/{token}/").status_code, 400)

    def test_expired_invite_rejected(self):
        inv = TeamInvite.objects.create(email="e@x.com", expires_at=timezone.now() - timezone.timedelta(days=1))
        self.assertEqual(APIClient().get(f"/api/crm/team/accept-invite/{inv.token}/").status_code, 400)

    def test_rep_cannot_invite_or_list_invites(self):
        c = self.as_(self.rep)
        self.assertEqual(c.post("/api/crm/team/invite/", {"email": "a@x.com", "role": "rep"}, format="json").status_code, 403)
        self.assertEqual(c.get("/api/crm/team/invites/").status_code, 403)

    def test_invite_resend_and_revoke(self):
        c = self.as_(self.manager)
        inv = TeamInvite.objects.create(email="r@x.com")
        self.assertEqual(c.post(f"/api/crm/team/invites/{inv.id}/").status_code, 200)
        self.assertEqual(len(c.get("/api/crm/team/invites/").json()), 1)
        self.assertEqual(c.delete(f"/api/crm/team/invites/{inv.id}/").status_code, 204)
        self.assertEqual(TeamInvite.objects.count(), 0)

    def test_cannot_demote_or_deactivate_self(self):
        c = self.as_(self.manager)
        self.assertEqual(c.patch(f"/api/crm/users/{self.manager.id}/", {"is_active": False}, format="json").status_code, 403)
        self.assertEqual(c.patch(f"/api/crm/users/{self.manager.id}/", {"role": "rep"}, format="json").status_code, 403)

    def test_only_admin_grants_admin(self):
        self.assertEqual(self.as_(self.manager).patch(f"/api/crm/users/{self.rep.id}/", {"role": "admin"}, format="json").status_code, 403)
        self.assertEqual(self.as_(self.admin).patch(f"/api/crm/users/{self.rep.id}/", {"role": "admin"}, format="json").status_code, 200)

    def test_rep_cannot_edit_users(self):
        self.assertEqual(self.as_(self.rep).patch(f"/api/crm/users/{self.manager.id}/", {"role": "rep"}, format="json").status_code, 403)

    def test_password_change_rotates_token(self):
        c = self.as_(self.rep)
        bad = c.post("/api/crm/me/password/", {"current_password": "nope", "new_password": "brandnew123"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = c.post("/api/crm/me/password/", {"current_password": "pass12345", "new_password": "brandnew123"}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("token", ok.json())
        weak = c.post("/api/crm/me/password/", {"current_password": "brandnew123", "new_password": "123"}, format="json")
        self.assertEqual(weak.status_code, 400)

    def test_recipients_and_notification_status_manager_only(self):
        self.assertEqual(self.as_(self.rep).get("/api/crm/notifications/recipients/").status_code, 403)
        c = self.as_(self.manager)
        self.assertEqual(c.post("/api/crm/notifications/recipients/", {"channel": "email", "value": "a@x.com"}, format="json").status_code, 201)
        self.assertEqual(c.get("/api/crm/notifications/test/").json()["recipients"], ["a@x.com"])
        self.assertIn("skipped", c.post("/api/crm/notifications/test/").json()["result"])

    def test_branch_doctor_crud_manager_only(self):
        self.assertEqual(self.as_(self.rep).post("/api/crm/branches/", {"name": "B"}, format="json").status_code, 403)
        c = self.as_(self.manager)
        b = c.post("/api/crm/branches/", {"name": "Gurugram"}, format="json").json()["id"]
        self.assertEqual(c.post("/api/crm/doctors/", {"name": "Dr X", "branch": b}, format="json").status_code, 201)
        self.assertEqual(self.as_(self.rep).get("/api/crm/doctors/").status_code, 200)


class BootstrapAdminTests(APITestCase):
    def run_cmd(self, **env):
        import os
        from io import StringIO
        from django.core.management import call_command
        out, err = StringIO(), StringIO()
        keys = ["CRM_BOOTSTRAP_ADMIN_USERNAME", "CRM_BOOTSTRAP_ADMIN_PASSWORD", "CRM_BOOTSTRAP_ADMIN_EMAIL"]
        old = {k: os.environ.pop(k, None) for k in keys}
        os.environ.update(env)
        try:
            call_command("bootstrap_admin", stdout=out, stderr=err)
        finally:
            for k in keys:
                os.environ.pop(k, None)
                if old[k] is not None:
                    os.environ[k] = old[k]
        return out.getvalue() + err.getvalue()

    def test_not_configured_is_a_noop(self):
        self.assertIn("skipping", self.run_cmd())
        self.assertEqual(CRMUser.objects.count(), 0)

    def test_weak_password_refused(self):
        self.run_cmd(CRM_BOOTSTRAP_ADMIN_USERNAME="boss", CRM_BOOTSTRAP_ADMIN_PASSWORD="short")
        self.assertEqual(User.objects.count(), 0)

    def test_creates_first_admin_who_can_log_in_then_never_again(self):
        self.run_cmd(CRM_BOOTSTRAP_ADMIN_USERNAME="boss", CRM_BOOTSTRAP_ADMIN_PASSWORD="a-long-password-1", CRM_BOOTSTRAP_ADMIN_EMAIL="b@x.com")
        r = APIClient().post("/api/crm/auth/login/", {"username": "boss", "password": "a-long-password-1"}, format="json")
        self.assertEqual((r.status_code, r.json()["user"]["role"]), (200, "admin"))
        # a later deploy with different values must not reset the password or add users
        self.run_cmd(CRM_BOOTSTRAP_ADMIN_USERNAME="boss", CRM_BOOTSTRAP_ADMIN_PASSWORD="different-password-2")
        self.run_cmd(CRM_BOOTSTRAP_ADMIN_USERNAME="intruder", CRM_BOOTSTRAP_ADMIN_PASSWORD="another-long-password-3")
        self.assertEqual(list(User.objects.values_list("username", flat=True)), ["boss"])
        self.assertTrue(User.objects.get(username="boss").check_password("a-long-password-1"))
