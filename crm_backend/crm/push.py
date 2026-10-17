"""Web Push (PWA notifications). Keys: VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY (see `generate_vapid_keys`).
Push is best effort: it never raises into the caller, and dead subscriptions are pruned as they're found."""
import json
import logging
from collections.abc import Iterable

from django.conf import settings
from django.utils import timezone

from .models import CRMUser, PushSubscription, Role

log = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def managers() -> list[CRMUser]:
    return list(CRMUser.objects.filter(is_active=True, role__in=[Role.ADMIN, Role.MANAGER]))


def send_push(users: Iterable[CRMUser | None], title: str, body: str = "", url: str = "/dashboard", tag: str = "") -> str:
    """Notify every device of `users` (None entries and duplicates are ignored). `url` is a CRM path opened on tap;
    `tag` makes a newer notification replace an older one with the same tag on the device."""
    if not is_configured():
        return "push skipped: VAPID keys not set"
    ids = {u.pk for u in users if u is not None and u.is_active}
    subs = PushSubscription.objects.filter(user_id__in=ids)
    if not subs:
        return "push skipped: no subscribed devices"
    from pywebpush import WebPushException, webpush

    payload = json.dumps({"title": title, "body": body[:300], "url": url, "tag": tag})
    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=24 * 60 * 60,
                timeout=10,
            )
            sub.last_success_at = timezone.now()
            sub.save(update_fields=["last_success_at"])
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 404, 410):  # expired/unsubscribed, or created with other VAPID keys
                sub.delete()
            else:
                log.warning("push to subscription %s failed: %s", sub.pk, e)
        except Exception:
            log.exception("push to subscription %s failed", sub.pk)
    return f"push sent: {sent}/{len(subs)}"


def safe_push(*args, **kwargs) -> str:
    """send_push for use inside other jobs: a push problem must never stop the email (or vice versa)."""
    try:
        return send_push(*args, **kwargs)
    except Exception:
        log.exception("push failed")
        return "push failed"
