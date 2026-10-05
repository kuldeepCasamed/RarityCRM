"""Google Calendar adapter (service account). All imports are lazy so the CRM runs without Google libs/config."""
import json
import logging

from django.conf import settings

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def is_configured() -> bool:
    return bool(settings.GOOGLE_SERVICE_ACCOUNT_FILE or settings.GOOGLE_SERVICE_ACCOUNT_JSON)


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if settings.GOOGLE_SERVICE_ACCOUNT_JSON:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON), scopes=SCOPES
        )
    else:
        creds = service_account.Credentials.from_service_account_file(settings.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def calendar_id_for(appt) -> str:
    return (appt.doctor.calendar_id if appt.doctor and appt.doctor.calendar_id else "") or settings.GOOGLE_DEFAULT_CALENDAR_ID


def _body(appt) -> dict:
    lead = appt.lead
    who = f"{appt.get_kind_display()}: {lead.name}"
    desc = [f"Patient: {lead.name}", f"Phone: {lead.phone}", f"Status: {appt.get_status_display()}"]
    if lead.chief_complaint:
        desc.append(f"Concern: {lead.chief_complaint}")
    if appt.notes:
        desc.append(f"Notes: {appt.notes}")
    desc.append(f"CRM: {settings.CRM_BASE_URL}/leads/{lead.id}")
    return {
        "summary": who,
        "description": "\n".join(desc),
        "location": appt.branch.name if appt.branch else "",
        "start": {"dateTime": appt.start_at.isoformat()},
        "end": {"dateTime": appt.end_at.isoformat()},
    }


def upsert_event(appt) -> tuple[str, str]:
    """Create or update the event. Returns (calendar_id, event_id). Moves the event if the calendar changed."""
    cal = calendar_id_for(appt)
    svc = _service()
    if appt.gcal_event_id and appt.gcal_calendar_id and appt.gcal_calendar_id != cal:
        delete_event(appt.gcal_calendar_id, appt.gcal_event_id)
        appt.gcal_event_id = ""
    body = _body(appt)
    if appt.gcal_event_id:
        ev = svc.events().update(calendarId=cal, eventId=appt.gcal_event_id, body=body).execute()
    else:
        ev = svc.events().insert(calendarId=cal, body=body).execute()
    return cal, ev["id"]


def delete_event(calendar_id: str, event_id: str) -> None:
    from googleapiclient.errors import HttpError

    try:
        _service().events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as e:
        if e.resp.status not in (404, 410):  # already gone is fine
            raise
