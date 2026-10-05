"""Twilio adapter: browser-calling token, webhook signature check, recording fetch. Lazy imports."""
from django.conf import settings


def is_configured() -> bool:
    s = settings
    return all([s.TWILIO_ACCOUNT_SID, s.TWILIO_AUTH_TOKEN, s.TWILIO_API_KEY, s.TWILIO_API_SECRET,
                s.TWILIO_TWIML_APP_SID, s.TWILIO_FROM_NUMBER])


def make_token(identity: str, ttl: int = 3600) -> str:
    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant

    token = AccessToken(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_API_KEY, settings.TWILIO_API_SECRET,
                        identity=identity, ttl=ttl)
    token.add_grant(VoiceGrant(outgoing_application_sid=settings.TWILIO_TWIML_APP_SID, incoming_allow=False))
    jwt = token.to_jwt()
    return jwt.decode() if isinstance(jwt, bytes) else jwt


def valid_signature(request) -> bool:
    """Validate X-Twilio-Signature against PUBLIC_BASE_URL (explicit, so proxies can't break it)."""
    from twilio.request_validator import RequestValidator

    if not settings.TWILIO_AUTH_TOKEN:
        return False
    url = settings.PUBLIC_BASE_URL.rstrip("/") + request.get_full_path()
    return RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(
        url, request.POST.dict(), request.headers.get("X-Twilio-Signature", "")
    )


def open_recording(recording_url: str):
    """Stream a recording from Twilio (needs account auth, so the browser can't fetch it directly)."""
    import requests

    return requests.get(f"{recording_url}.mp3", auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                        stream=True, timeout=20)
