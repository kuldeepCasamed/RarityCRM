import logging
from html import escape

from django.conf import settings

from ..models import NotificationRecipient

log = logging.getLogger(__name__)


def manager_emails() -> list[str]:
    """DB-backed recipients, falling back to CRM_MANAGER_EMAIL."""
    emails = list(
        NotificationRecipient.objects.filter(channel="email", is_active=True).values_list("value", flat=True)
    )
    if not emails and settings.CRM_MANAGER_EMAIL:
        emails = [e.strip() for e in settings.CRM_MANAGER_EMAIL.split(",") if e.strip()]
    return emails


def send_email(to: list[str], subject: str, body_html: str, attachments: list[dict] | None = None, reply_to: str = "") -> str:
    if not to:
        return "skipped: no recipients"
    if not settings.RESEND_API_KEY:
        log.info("[email disabled] to=%s subject=%s", to, subject)
        return "skipped: RESEND_API_KEY not set"
    import resend

    resend.api_key = settings.RESEND_API_KEY
    payload = {"from": settings.CRM_FROM_EMAIL, "to": to, "subject": subject, "html": body_html}
    if attachments:  # [{"filename": "...", "content": <base64 str>}]
        payload["attachments"] = attachments
    if reply_to:
        payload["reply_to"] = reply_to
    resend.Emails.send(payload)
    return f"sent to {len(to)}"


def render(title: str, rows: list[tuple[str, str]], link: str = "", link_text: str = "Open in CRM") -> str:
    body = "".join(f"<p><strong>{escape(k)}:</strong> {escape(str(v))}</p>" for k, v in rows if v)
    btn = f'<p><a href="{escape(link)}" style="background:#8C7864;color:#fff;padding:10px 16px;text-decoration:none;border-radius:4px">{escape(link_text)}</a></p>' if link else ""
    return (
        '<html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#333">'
        f'<h2 style="color:#4a4a4a">{escape(title)}</h2>{body}{btn}'
        '<hr style="border:none;border-top:1px solid #eee"><p style="font-size:12px;color:#888">Rarity Dental CRM</p>'
        "</body></html>"
    )
