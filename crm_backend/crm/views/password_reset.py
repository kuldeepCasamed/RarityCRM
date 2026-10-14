"""Forgot-password flow: email a 6-digit code, then accept code + new password.

Security notes
- The request endpoint answers identically whether or not the account exists (no account enumeration).
- Only a keyed hash of the code is stored; codes expire after CODE_TTL_MINUTES, allow MAX_ATTEMPTS wrong guesses,
  and are single use. A new request replaces older codes and is limited per account (cooldown + hourly cap).
- A successful reset signs the user out everywhere (all API tokens are deleted).
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from html import escape

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import PasswordResetCode
from ..throttles import PasswordResetConfirmThrottle, PasswordResetRequestThrottle
from ..utils.email_service import render, send_email

log = logging.getLogger(__name__)
User = get_user_model()

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_REQUESTS_PER_HOUR = 5
GENERIC_REQUEST_OK = {"detail": "If an account matches, a verification code has been sent to its email address."}
BAD_CODE = {"detail": "That code is invalid or has expired. Request a new one."}


def _hash(user_id: int, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"pwreset:{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _find_user(identifier: str):
    """Match by username or email (case-insensitive). Only active CRM users with an email address can reset."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    qs = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier), crm_profile__is_active=True, is_active=True)
    return qs.exclude(email="").order_by("id").first()


def _code_email(user, code: str) -> str:
    name = escape(user.get_full_name() or user.username)
    return (
        '<html><body style="font-family:Arial,sans-serif;line-height:1.6;color:#333">'
        f"<p>Hi {name},</p>"
        "<p>Use this code to reset your Rarity CRM password:</p>"
        f'<p style="font-size:32px;letter-spacing:8px;font-weight:bold;color:#8C7864">{escape(code)}</p>'
        f"<p>It expires in {CODE_TTL_MINUTES} minutes. If you did not ask for this, ignore this email; your password stays the same.</p>"
        '<hr style="border:none;border-top:1px solid #eee"><p style="font-size:12px;color:#888">Rarity Dental CRM</p>'
        "</body></html>"
    )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetRequestThrottle]

    def post(self, request):
        user = _find_user(request.data.get("identifier"))
        if user is None:
            return Response(GENERIC_REQUEST_OK)

        now = timezone.now()
        recent = PasswordResetCode.objects.filter(user=user, created_at__gt=now - timedelta(hours=1))
        latest = recent.order_by("-created_at").first()
        too_soon = latest and (now - latest.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS
        if too_soon or recent.count() >= MAX_REQUESTS_PER_HOUR:
            return Response(GENERIC_REQUEST_OK)  # same answer; just don't send another email

        code = f"{secrets.randbelow(10**6):06d}"
        with transaction.atomic():
            PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)  # older codes die
            PasswordResetCode.objects.filter(created_at__lt=now - timedelta(days=1)).delete()
            PasswordResetCode.objects.create(
                user=user, code_hash=_hash(user.pk, code), expires_at=now + timedelta(minutes=CODE_TTL_MINUTES)
            )
        try:
            send_email([user.email], "Your Rarity CRM verification code", _code_email(user, code))
        except Exception:
            log.exception("could not send password reset code to user %s", user.pk)
        return Response(GENERIC_REQUEST_OK)


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetConfirmThrottle]

    def post(self, request):
        user = _find_user(request.data.get("identifier"))
        code = str(request.data.get("code", "")).strip().replace(" ", "")
        new_password = request.data.get("new_password", "")
        if user is None or not code:
            return Response(BAD_CODE, status=400)

        with transaction.atomic():
            row = (
                PasswordResetCode.objects.select_for_update()
                .filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
                .order_by("-created_at")
                .first()
            )
            if row is None or row.attempts >= MAX_ATTEMPTS:
                return Response(BAD_CODE, status=400)
            if not hmac.compare_digest(row.code_hash, _hash(user.pk, code)):
                row.attempts += 1
                row.save(update_fields=["attempts"])
                return Response(BAD_CODE, status=400)

            try:  # the code was right: a weak password must not burn it
                validate_password(new_password, user)
            except DjangoValidationError as e:
                raise ValidationError({"new_password": list(e.messages)})

            user.set_password(new_password)
            user.save(update_fields=["password"])
            row.used_at = timezone.now()
            row.save(update_fields=["used_at"])
            Token.objects.filter(user=user).delete()  # sign out everywhere

        try:
            send_email(
                [user.email], "Your Rarity CRM password was changed",
                render("Password changed", [("Account", user.username)], link=settings.CRM_BASE_URL, link_text="Sign in")
                + "<p>If this wasn't you, contact your administrator right away.</p>",
            )
        except Exception:
            log.exception("could not send password-changed notice to user %s", user.pk)
        return Response({"detail": "Password updated. You can sign in now."})
