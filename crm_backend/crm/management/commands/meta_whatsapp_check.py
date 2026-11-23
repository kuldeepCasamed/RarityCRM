import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from crm import whatsapp
from crm.models import WhatsAppEvent

NEEDED = {"whatsapp_business_management", "whatsapp_business_messaging", "ads_read"}


class Command(BaseCommand):
    help = "Verify the WhatsApp setup (token scopes, number, coexistence status, webhook subscription). Prints no secrets."

    def _get(self, path, params=None):
        r = requests.get(f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}/{path}", params=params or {},
                         headers={"Authorization": f"Bearer {settings.META_SYSTEM_USER_TOKEN}"}, timeout=15)
        try:
            return r.ok, r.json()
        except ValueError:
            return r.ok, {}

    def handle(self, *args, **opts):
        s = settings
        missing = [n for n in ("META_APP_ID", "META_APP_SECRET", "META_SYSTEM_USER_TOKEN", "META_WA_VERIFY_TOKEN") if not getattr(s, n)]
        if missing:
            raise CommandError("Not set: " + ", ".join(missing))
        out = self.stdout.write
        r = requests.get(f"https://graph.facebook.com/{s.META_GRAPH_VERSION}/debug_token", timeout=15,
                         params={"input_token": s.META_SYSTEM_USER_TOKEN, "access_token": f"{s.META_APP_ID}|{s.META_APP_SECRET}"})
        d = (r.json() or {}).get("data", {})
        out(f"1. token valid: {d.get('is_valid')} | missing scopes: {sorted(NEEDED - set(d.get('scopes', []))) or 'none'}")
        waba, pnid = whatsapp.connection()
        out(f"2. WABA: {waba or 'NOT ONBOARDED YET'} | phone_number_id: {pnid or '-'}")
        if waba:
            ok, body = self._get(f"{waba}/phone_numbers", {"fields": "display_phone_number,verified_name,platform_type,status,code_verification_status"})
            out("3. numbers: " + ("" if ok else f"FAILED {body.get('error', {}).get('message', '')}"))
            for n in body.get("data", []):
                out(f"   {n.get('display_phone_number')} | {n.get('status')} | platform: {n.get('platform_type')} | {n.get('verified_name')}")
            ok, body = self._get(f"{waba}/subscribed_apps")
            apps = [a.get("whatsapp_business_api_data", {}).get("id") or a.get("id") for a in body.get("data", [])]
            out(f"4. subscribed apps: {apps or 'none'} (ours: {s.META_APP_ID})")
        out("5. last webhook events (field, time):")
        for e in WhatsAppEvent.objects.all()[:5]:
            out(f"   {e.field}  {e.received_at:%Y-%m-%d %H:%M:%S}")
