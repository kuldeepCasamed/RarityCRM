import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

NEEDED = {"leads_retrieval", "pages_manage_metadata", "pages_show_list", "pages_read_engagement", "business_management"}


class Command(BaseCommand):
    help = "Verify the Meta Lead Ads setup (token, page access, forms, webhook subscription). Prints no secrets."

    def add_arguments(self, parser):
        parser.add_argument("--subscribe", action="store_true", help="Subscribe the app to the Page's leadgen field if it is not yet")

    def _get(self, path, params=None, token=None):
        r = requests.get(
            f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}/{path}",
            params=params or {}, headers={"Authorization": f"Bearer {token or settings.META_SYSTEM_USER_TOKEN}"}, timeout=15,
        )
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.ok, body

    def handle(self, *args, subscribe=False, **opts):
        s = settings
        missing = [n for n in ("META_APP_ID", "META_APP_SECRET", "META_SYSTEM_USER_TOKEN", "META_LEADS_VERIFY_TOKEN") if not getattr(s, n)]
        if missing:
            raise CommandError("Not set: " + ", ".join(missing))
        out = self.stdout.write

        ok, body = self._get("debug_token", {"input_token": s.META_SYSTEM_USER_TOKEN}, token=f"{s.META_APP_ID}|{s.META_APP_SECRET}")
        d = body.get("data", {})
        scopes = set(d.get("scopes", []))
        exp = d.get("expires_at")
        out(f"1. token valid: {d.get('is_valid')} | type: {d.get('type')} | expires: {'never' if exp == 0 else exp} | app: {d.get('application')}")
        out(f"   missing scopes: {sorted(NEEDED - scopes) or 'none'}")

        ok, body = self._get(s.META_PAGE_ID, {"fields": "name,access_token"})
        page_token = body.get("access_token")
        out(f"2. page access: {'OK' if ok and page_token else 'FAILED'} ({body.get('name', body.get('error', {}).get('message', ''))})")

        ok, body = self._get(f"{s.META_PAGE_ID}/leadgen_forms", {"fields": "id,name,status", "limit": 50}, token=page_token)
        out("3. forms: " + ("" if ok else f"FAILED {body.get('error', {}).get('message', '')}"))
        for f in body.get("data", []):
            out(f"   {f['id']}  {f.get('status')}  {f.get('name')}")
        out(f"   META_LEAD_FORM_IDS currently: {s.META_LEAD_FORM_IDS or 'EMPTY (backfill disabled)'}")

        ok, body = self._get(f"{s.META_PAGE_ID}/subscribed_apps", token=page_token)
        apps = body.get("data", [])
        mine = [a for a in apps if str(a.get("id")) == str(s.META_APP_ID)]
        fields = mine[0].get("subscribed_fields", []) if mine else []
        subscribed = "leadgen" in fields
        out(f"4. subscribed to leadgen: {subscribed} (fields: {fields})")

        if not subscribed and subscribe and page_token:
            r = requests.post(
                f"https://graph.facebook.com/{s.META_GRAPH_VERSION}/{s.META_PAGE_ID}/subscribed_apps",
                data={"subscribed_fields": "leadgen"}, headers={"Authorization": f"Bearer {page_token}"}, timeout=15,
            )
            out(f"5. subscribe: {r.status_code} {r.text[:150]}")
        elif not subscribed:
            out("5. not subscribed: re-run with --subscribe")
