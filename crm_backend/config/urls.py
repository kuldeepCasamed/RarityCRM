from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from crm.views.contact_clicks import contact_click
from crm.views.meta_leads import LeadgenWebhookView
from crm.views.whatsapp import WhatsAppWebhookView, connect_complete, connect_page


def healthz(request):
    """Liveness probe for the host. Skips the database by default so frequent pings don't keep a serverless
    Postgres awake; use /healthz/?db=1 for a manual end-to-end check."""
    if request.GET.get("db"):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            return JsonResponse({"status": "database_unreachable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", healthz),
    path("api/meta/whatsapp-webhook/", WhatsAppWebhookView.as_view()),  # WhatsApp (coexistence) webhook, signature-authenticated
    path("admin/meta/whatsapp-connect/", connect_page),
    path("admin/meta/whatsapp-connect/complete/", connect_complete),
    path("admin/", admin.site.urls),
    path("api/meta/leadgen-webhook/", LeadgenWebhookView.as_view()),  # Meta Lead Ads webhook (signature-authenticated)
    path("api/public/contact-click/", contact_click),  # anonymous WhatsApp / Call click log (always 204)
    path("api/crm/", include("crm.urls")),
]
