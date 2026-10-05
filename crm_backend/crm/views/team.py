from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Branch, CRMUser, Doctor, NotificationRecipient, TeamInvite
from ..permissions import IsCRMManager, IsCRMUser, crm_profile
from ..serializers import (
    AcceptInviteSerializer, BranchSerializer, CRMUserSerializer, DoctorSerializer,
    InviteCreateSerializer, NotificationRecipientSerializer,
)
from ..tasks import send_invite_email
from ..throttles import LoginThrottle

User = get_user_model()


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    def post(self, request):
        user = authenticate(username=request.data.get("username"), password=request.data.get("password"))
        profile = getattr(user, "crm_profile", None) if user else None
        if not profile or not profile.is_active:
            return Response({"detail": "Invalid credentials or no CRM access."}, status=401)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": CRMUserSerializer(profile).data})


class MeView(APIView):
    def get(self, request):
        return Response(CRMUserSerializer(crm_profile(request)).data)

    def patch(self, request):
        profile = crm_profile(request)
        ser = CRMUserSerializer(profile, data={k: v for k, v in request.data.items() if k in ("phone", "timezone")}, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class CRMUserViewSet(viewsets.ModelViewSet):
    queryset = CRMUser.objects.select_related("user")
    serializer_class = CRMUserSerializer
    pagination_class = None
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        return [IsCRMUser()] if self.action in ("list", "retrieve") else [IsCRMManager()]

    def perform_update(self, serializer):
        from rest_framework.exceptions import PermissionDenied

        me = crm_profile(self.request)
        target = serializer.instance
        data = serializer.validated_data
        if target.pk == me.pk and (data.get("is_active") is False or ("role" in data and data["role"] != me.role)):
            raise PermissionDenied("You cannot change your own role or deactivate yourself.")
        if me.role != "admin" and (data.get("role") == "admin" or target.role == "admin"):
            raise PermissionDenied("Only an admin can grant or change the admin role.")
        serializer.save()


class InviteView(APIView):
    permission_classes = [IsCRMManager]

    def post(self, request):
        ser = InviteCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        invite = TeamInvite.objects.create(invited_by=crm_profile(request), **ser.validated_data)
        send_invite_email(invite.id)
        return Response({"id": invite.id, "token": invite.token, "expires_at": invite.expires_at}, status=201)


class InviteListView(APIView):
    permission_classes = [IsCRMManager]

    def get(self, request):
        rows = TeamInvite.objects.filter(is_used=False).order_by("-created_at")
        return Response([
            {"id": i.id, "email": i.email, "role": i.role, "expires_at": i.expires_at,
             "expired": not i.is_valid, "token": i.token}
            for i in rows
        ])


class InviteDetailView(APIView):
    permission_classes = [IsCRMManager]

    def delete(self, request, pk):
        get_object_or_404(TeamInvite, pk=pk, is_used=False).delete()
        return Response(status=204)

    def post(self, request, pk):
        """Resend: refresh expiry and re-send the email."""
        from ..models import _invite_expiry

        inv = get_object_or_404(TeamInvite, pk=pk, is_used=False)
        inv.expires_at = _invite_expiry()
        inv.save(update_fields=["expires_at"])
        send_invite_email(inv.id)
        return Response({"id": inv.id, "expires_at": inv.expires_at})


class ChangePasswordView(APIView):
    def post(self, request):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        user = request.user
        if not user.check_password(request.data.get("current_password", "")):
            raise ValidationError({"current_password": "Incorrect password."})
        new = request.data.get("new_password", "")
        try:
            validate_password(new, user)
        except DjangoValidationError as e:
            raise ValidationError({"new_password": list(e.messages)})
        user.set_password(new)
        user.save()
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        return Response({"token": token.key})


class NotificationTestView(APIView):
    """POST: send a test email to the configured recipients; GET: config status."""

    permission_classes = [IsCRMManager]

    def get(self, request):
        from django.conf import settings
        from ..utils.email_service import manager_emails

        return Response({"email_configured": bool(settings.RESEND_API_KEY), "recipients": manager_emails()})

    def post(self, request):
        from ..utils.email_service import manager_emails, render, send_email

        return Response({"result": send_email(manager_emails(), "Rarity CRM test", render("Notification test", [("Status", "OK")]))})


class AcceptInviteView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def _invite(self, token):
        invite = get_object_or_404(TeamInvite, token=token)
        if not invite.is_valid:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Invite is expired or already used.")
        return invite

    def get(self, request, token):
        invite = self._invite(token)
        return Response({"email": invite.email, "role": invite.role})

    @transaction.atomic
    def post(self, request, token):
        invite = self._invite(token)
        ser = AcceptInviteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        user, created = User.objects.get_or_create(
            username=invite.email, defaults={"email": invite.email, "first_name": d["first_name"], "last_name": d.get("last_name", "")}
        )
        if not created and hasattr(user, "crm_profile"):
            from rest_framework.exceptions import ValidationError
            raise ValidationError("This email already has CRM access.")
        user.set_password(d["password"])
        user.save()
        CRMUser.objects.create(user=user, role=invite.role)
        invite.is_used = True
        invite.save(update_fields=["is_used"])
        token_obj, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token_obj.key}, status=201)


class BranchViewSet(viewsets.ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    pagination_class = None

    def get_permissions(self):
        return [IsCRMUser()] if self.request.method in ("GET", "HEAD", "OPTIONS") else [IsCRMManager()]


class DoctorViewSet(BranchViewSet):
    queryset = Doctor.objects.select_related("branch")
    serializer_class = DoctorSerializer


class RecipientViewSet(viewsets.ModelViewSet):
    queryset = NotificationRecipient.objects.all()
    serializer_class = NotificationRecipientSerializer
    permission_classes = [IsCRMManager]
    pagination_class = None
