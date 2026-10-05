"""Async + scheduled jobs (django-q2). Public `notify_*` helpers enqueue after commit;
`run_*` functions are the actual workers and return a status string for logs."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from .utils.email_service import manager_emails, render, send_email


def _enqueue(func_name: str, *args):
    transaction.on_commit(lambda: async_task(f"crm.tasks.{func_name}", *args))


def _lead_link(lead_id):
    return f"{settings.CRM_BASE_URL}/leads/{lead_id}"


# ---- enqueue helpers (called from views/services) ----
def notify_new_lead(lead_id): _enqueue("run_new_lead_alert", lead_id)
def notify_assigned(lead_id): _enqueue("run_assigned_alert", lead_id)
def send_invite_email(invite_id): _enqueue("run_invite_email", invite_id)
def sync_appointment(appt_id): _enqueue("run_calendar_sync", appt_id)
def delete_calendar_event(calendar_id, event_id): _enqueue("run_calendar_delete", calendar_id, event_id)


# ---- workers ----
def run_new_lead_alert(lead_id):
    from .models import Lead

    lead = Lead.objects.select_related("assigned_to__user").get(pk=lead_id)
    to = manager_emails()
    if lead.assigned_to and lead.assigned_to.user.email:
        to.append(lead.assigned_to.user.email)
    html = render(
        "New Lead", [("Name", lead.name), ("Phone", lead.phone), ("Email", lead.email),
                     ("Country", lead.country), ("Source", lead.get_source_display()),
                     ("Page", lead.landing_url), ("Message", lead.chief_complaint)],
        _lead_link(lead.id),
    )
    return send_email(sorted(set(to)), f"New lead: {lead.name}", html)


def run_assigned_alert(lead_id):
    from .models import Lead

    lead = Lead.objects.select_related("assigned_to__user").get(pk=lead_id)
    if not lead.assigned_to or not lead.assigned_to.user.email:
        return "skipped: no assignee email"
    html = render("Lead assigned to you", [("Name", lead.name), ("Phone", lead.phone)], _lead_link(lead.id))
    return send_email([lead.assigned_to.user.email], f"Lead assigned: {lead.name}", html)


def run_invite_email(invite_id):
    from .models import TeamInvite

    inv = TeamInvite.objects.get(pk=invite_id)
    html = render("You're invited to Rarity CRM", [("Role", inv.get_role_display())],
                  f"{settings.CRM_BASE_URL}/invite/{inv.token}", "Accept invite")
    return send_email([inv.email], "Rarity CRM invitation", html)


def run_stale_leads_digest(hours=48):
    from .models import Lead, LeadStatus

    cutoff = timezone.now() - timedelta(hours=hours)
    stale = Lead.objects.alive().filter(status__in=[LeadStatus.NEW, LeadStatus.CONTACTED], created_at__lt=cutoff,
                                        last_contacted_at__isnull=True)
    n = stale.count()
    if not n:
        return "no stale leads"
    rows = [(l.name, f"{l.phone} — created {timezone.localtime(l.created_at):%d %b %H:%M}") for l in stale[:50]]
    return send_email(manager_emails(), f"{n} leads untouched for {hours}h+", render(f"{n} untouched leads", rows, f"{settings.CRM_BASE_URL}/leads"))


def run_daily_task_digest():
    from .models import CRMUser, Task

    end_today = timezone.localtime().replace(hour=23, minute=59, second=59)
    sent = 0
    for rep in CRMUser.objects.filter(is_active=True).select_related("user"):
        tasks = Task.objects.filter(assigned_to=rep, is_done=False, due_at__lte=end_today).select_related("lead")
        if not tasks.exists() or not rep.user.email:
            continue
        rows = [(t.lead.name, f"{t.title} (due {timezone.localtime(t.due_at):%d %b %H:%M})") for t in tasks[:30]]
        send_email([rep.user.email], "Your tasks for today", render("Today's tasks", rows, f"{settings.CRM_BASE_URL}/tasks"))
        sent += 1
    return f"digests sent: {sent}"


def run_appointment_reminders(hours=24):
    """Email the assigned rep about appointments starting within `hours`."""
    from .models import Appointment

    now = timezone.now()
    appts = Appointment.objects.filter(start_at__gte=now, start_at__lte=now + timedelta(hours=hours),
                                       status__in=["booked", "confirmed"]).select_related("lead__assigned_to__user", "doctor")
    sent = 0
    for a in appts:
        rep = a.lead.assigned_to
        if rep and rep.user.email:
            send_email([rep.user.email], f"Upcoming appointment: {a.lead.name}",
                       render("Upcoming appointment", [("Patient", a.lead.name), ("Phone", a.lead.phone),
                              ("When", f"{timezone.localtime(a.start_at):%d %b %H:%M}"), ("Doctor", a.doctor.name if a.doctor else "")],
                              _lead_link(a.lead_id)))
            sent += 1
    return f"reminders sent: {sent}"


def run_calendar_sync(appt_id):
    """Push an appointment to Google Calendar. Cancelled appointments remove their event."""
    from .models import Appointment
    from .utils import gcal

    if not gcal.is_configured():
        return "skipped: Google Calendar not configured"
    appt = Appointment.objects.select_related("lead", "doctor", "branch").get(pk=appt_id)
    try:
        if appt.status == "cancelled":
            if appt.gcal_event_id:
                gcal.delete_event(appt.gcal_calendar_id, appt.gcal_event_id)
                appt.gcal_event_id = appt.gcal_calendar_id = ""
            appt.gcal_sync_error = ""
            appt.save(update_fields=["gcal_event_id", "gcal_calendar_id", "gcal_sync_error"])
            return "event removed"
        if not gcal.calendar_id_for(appt):
            appt.gcal_sync_error = "No calendar: set the doctor's calendar ID or GOOGLE_DEFAULT_CALENDAR_ID"
            appt.save(update_fields=["gcal_sync_error"])
            return "skipped: no calendar id"
        cal, event_id = gcal.upsert_event(appt)
        appt.gcal_calendar_id, appt.gcal_event_id, appt.gcal_sync_error = cal, event_id, ""
        appt.save(update_fields=["gcal_calendar_id", "gcal_event_id", "gcal_sync_error"])
        return f"synced {event_id}"
    except Exception as e:  # keep the appointment; surface the error in the UI
        appt.gcal_sync_error = str(e)[:500]
        appt.save(update_fields=["gcal_sync_error"])
        raise


def run_calendar_delete(calendar_id, event_id):
    from .utils import gcal

    if not gcal.is_configured() or not event_id:
        return "skipped"
    gcal.delete_event(calendar_id, event_id)
    return "event removed"


def run_refresh_scores():
    """Nightly: lead scores decay when contact goes stale."""
    from .scoring import refresh_all

    return f"scores updated: {refresh_all()}"


def run_expire_quotes():
    from .quotes import expire_overdue

    return f"quotes expired: {expire_overdue()}"
