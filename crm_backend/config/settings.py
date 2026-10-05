import os
import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

DEV_SECRET_KEY = "dev-insecure-change-me"
SECRET_KEY = env("SECRET_KEY", default=DEV_SECRET_KEY)
DEBUG = env("DEBUG")  # False unless DEBUG=True is set (local .env sets it; production must not)
TESTING = "test" in sys.argv

# Refuse to boot in production with the well-known development key.
if not DEBUG and not TESTING and (SECRET_KEY == DEV_SECRET_KEY or len(SECRET_KEY) < 32):
    raise ImproperlyConfigured("Set SECRET_KEY to a long random value (32+ chars) when DEBUG is off.")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# Render injects the service's public hostname; trust it automatically.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host:
    ALLOWED_HOSTS.append(_render_host)
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "corsheaders",
    "django_q",
    "crm",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves /static/ (admin css) without nginx
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Postgres is the target. Set DATABASE_URL=postgres://user:pass@localhost:5432/rarity_crm
# The sqlite fallback exists only so tests/dev run before Postgres is installed.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

# Poolers (Neon/pgbouncer, transaction mode) can't hold server-side cursors.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
# Seconds to keep a DB connection open between requests (0 = reconnect each request). Set ~60 in production.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)
if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    # psycopg3 prepared statements don't survive transaction-mode poolers.
    DATABASES["default"].setdefault("OPTIONS", {})["prepare_threshold"] = None

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if (DEBUG or TESTING)
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["crm.permissions.IsCRMUser"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "crm.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_RATES": {
        "public_intake": "20/hour",
        "login": "10/minute",
        "calls": "30/hour",
    },
}

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"]
)

# django-q2 uses the ORM (Postgres/sqlite) as broker: no Redis needed.
# Note: the worker polls the database, which keeps a serverless Postgres (e.g. Neon) awake while it runs.
Q_CLUSTER = {
    "name": "rarity_crm",
    "workers": env.int("Q_WORKERS", default=2),
    "timeout": 60,
    "retry": 90,
    "max_attempts": 3,
    "poll": env.float("Q_POLL", default=1.0),  # seconds between DB polls; higher = fewer queries
    "catch_up": False,  # after downtime, don't replay every missed daily digest at once
    "orm": "default",
    "sync": env.bool("Q_SYNC", default=False),
}

# --- CRM settings ---
CRM_BASE_URL = env("CRM_BASE_URL", default="http://localhost:3000")
# On Render this defaults to the service URL; set explicitly only for a custom domain.
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default=os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000"))
# Shared secret the marketing site sends as X-Rarity-Key (server-side only).
CRM_INTAKE_API_KEY = env("CRM_INTAKE_API_KEY", default="")
CRM_MANAGER_EMAIL = env("CRM_MANAGER_EMAIL", default="")
RESEND_API_KEY = env("RESEND_API_KEY", default="")
CRM_FROM_EMAIL = env("CRM_FROM_EMAIL", default="Rarity CRM <onboarding@raritydental.com>")
CRM_DEFAULT_REGION = "IN"

# --- Google Calendar (service account; share each doctor's calendar with its email) ---
GOOGLE_SERVICE_ACCOUNT_FILE = env("GOOGLE_SERVICE_ACCOUNT_FILE", default="")
GOOGLE_SERVICE_ACCOUNT_JSON = env("GOOGLE_SERVICE_ACCOUNT_JSON", default="")  # raw JSON alternative
GOOGLE_DEFAULT_CALENDAR_ID = env("GOOGLE_DEFAULT_CALENDAR_ID", default="")  # used when a doctor has none

# --- Twilio (browser calling) ---
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", default="")
TWILIO_API_KEY = env("TWILIO_API_KEY", default="")
TWILIO_API_SECRET = env("TWILIO_API_SECRET", default="")
TWILIO_TWIML_APP_SID = env("TWILIO_TWIML_APP_SID", default="")
TWILIO_FROM_NUMBER = env("TWILIO_FROM_NUMBER", default="")
# Recording is OFF by default: enable only once consent/announcement rules are settled.
TWILIO_RECORD_CALLS = env.bool("TWILIO_RECORD_CALLS", default=False)

# --- Quotes / PDF letterhead ---
CLINIC_NAME = env("CLINIC_NAME", default="Rarity Dental")
CLINIC_ADDRESS = env("CLINIC_ADDRESS", default="")
CLINIC_PHONE = env("CLINIC_PHONE", default="")
CLINIC_EMAIL = env("CLINIC_EMAIL", default="")
CLINIC_WEBSITE = env("CLINIC_WEBSITE", default="www.raritydental.com")
CLINIC_TAX_ID = env("CLINIC_TAX_ID", default="")  # GSTIN, printed on the quote if set
QUOTE_VALID_DAYS = env.int("QUOTE_VALID_DAYS", default=30)
QUOTE_DEFAULT_TERMS = env(
    "QUOTE_DEFAULT_TERMS",
    default="This estimate is based on a preliminary assessment. The final treatment plan and fees may change after "
            "clinical examination, imaging or if additional treatment becomes necessary. Prices are valid until the date shown.",
)

# --- Production hardening (only when DEBUG is off, so local development is unchanged) ---
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Render terminates TLS in front of us
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # the platform health check talks plain HTTP
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=3600)  # raise to 31536000 once confirmed working
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    # We deliberately don't preload HSTS or cover subdomains (the domain also hosts other things).
    SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

# Errors and request logs go to stdout so the host (Render) captures them.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}
