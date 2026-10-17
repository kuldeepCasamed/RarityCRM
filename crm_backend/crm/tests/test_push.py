from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from pywebpush import WebPushException
from rest_framework.test import APIClient, APITestCase

from crm import tasks
from crm.models import Appointment, CRMUser, Lead, PushSubscription, Role, Task

User = get_user_model()
VAPID = {"VAPID_PUBLIC_KEY": "pub", "VAPID_PRIVATE_KEY": "priv", "VAPID_SUBJECT": "mailto:t@x.com"}


def make_user(name, role=Role.REP):
    u = User.objects.create_user(name, f"{name}@x.com", "pass12345", first_name=name)
    return CRMUser.objects.create(user=u, role=role)


def subscribe(profile, n=1):
    return PushSubscription.objects.create(user=profile, endpoint=f"https://push.example/{profile.pk}/{n}", p256dh="k", auth="a")


@override_settings(Q_CLUSTER={"name": "t", "orm": "default", "sync": True}, **VAPID)
@mock.patch("pywebpush.webpush")
class PushTests(APITestCase):
    def setUp(self):
        self.manager = make_user("mgr", Role.MANAGER)
        self.rep = make_user("rep")
        self.other = make_user("other")

    def as_(self, p):
        c = APIClient()
        c.force_authenticate(p.user)
        return c

    def endpoints(self, webpush):
        return sorted(call.kwargs["subscription_info"]["endpoint"] for call in webpush.call_args_list)

    def test_subscribe_moves_endpoint_between_users_and_unsubscribe(self, webpush):
        body = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
        self.assertEqual(self.as_(self.rep).post("/api/crm/push/subscribe/", body, format="json").status_code, 201)
        self.assertEqual(self.as_(self.other).post("/api/crm/push/subscribe/", body, format="json").status_code, 201)
        self.assertEqual(PushSubscription.objects.get().user, self.other)  # same browser, new user signed in
        self.assertEqual(self.as_(self.other).get("/api/crm/push/config/").json(), {"configured": True, "public_key": "pub"})
        self.as_(self.other).delete("/api/crm/push/subscribe/", {"endpoint": body["endpoint"]}, format="json")
        self.assertFalse(PushSubscription.objects.exists())

    def test_rejects_bad_subscription(self, webpush):
        r = self.as_(self.rep).post("/api/crm/push/subscribe/", {"endpoint": "http://insecure", "keys": {}}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_new_lead_pushes_managers_and_assignee_only(self, webpush):
        subs = [subscribe(self.manager), subscribe(self.rep), subscribe(self.other)]
        lead = Lead.objects.create(name="Asha", phone="+919810012345", assigned_to=self.rep)
        tasks.run_new_lead_alert(lead.id)
        self.assertEqual(self.endpoints(webpush), sorted([subs[0].endpoint, subs[1].endpoint]))
        self.assertIn('"url": "/leads/%d"' % lead.id, webpush.call_args.kwargs["data"])

    def test_gone_subscription_is_pruned(self, webpush):
        sub = subscribe(self.rep)
        webpush.side_effect = WebPushException("gone", response=mock.Mock(status_code=410))
        tasks.run_assigned_alert(Lead.objects.create(name="A", phone="+919810012345", assigned_to=self.rep).id)
        self.assertFalse(PushSubscription.objects.filter(pk=sub.pk).exists())

    def test_reminders_push_once(self, webpush):
        subscribe(self.rep)
        lead = Lead.objects.create(name="Asha", phone="+919810012345", assigned_to=self.rep)
        now = timezone.now()
        Task.objects.create(lead=lead, assigned_to=self.rep, title="Call back", due_at=now + timedelta(minutes=5))
        Task.objects.create(lead=lead, assigned_to=self.rep, title="Later", due_at=now + timedelta(days=1))
        Appointment.objects.create(lead=lead, start_at=now + timedelta(minutes=30), end_at=now + timedelta(minutes=60))
        self.assertEqual(tasks.run_push_reminders(), "push reminders: 1 tasks, 1 appointments")
        self.assertEqual(webpush.call_count, 2)
        self.assertEqual(tasks.run_push_reminders(), "push reminders: 0 tasks, 0 appointments")
        self.assertEqual(webpush.call_count, 2)

    @override_settings(VAPID_PRIVATE_KEY="")
    def test_not_configured_skips(self, webpush):
        subscribe(self.rep)
        r = self.as_(self.rep).post("/api/crm/push/test/")
        self.assertEqual(r.json()["result"], "push skipped: VAPID keys not set")
        webpush.assert_not_called()
