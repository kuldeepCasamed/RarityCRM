from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path


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
    path("admin/", admin.site.urls),
    path("api/crm/", include("crm.urls")),
]
