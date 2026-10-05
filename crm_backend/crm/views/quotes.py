import base64
from html import escape

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import quotes as svc
from ..models import ContactLog, Lead, Quote, TreatmentCatalog
from ..permissions import IsCRMManager, IsCRMUser, crm_profile
from ..serializers import QuoteSerializer, TreatmentCatalogSerializer
from ..utils.email_service import send_email
from ..utils.quote_pdf import render_quote_pdf

QUOTES = Quote.objects.select_related("lead", "created_by__user").prefetch_related("items")


class LeadQuotes(generics.ListCreateAPIView):
    serializer_class = QuoteSerializer
    pagination_class = None

    def get_queryset(self):
        return QUOTES.filter(lead_id=self.kwargs["lead_id"])

    def perform_create(self, serializer):
        lead = get_object_or_404(Lead.objects.alive(), pk=self.kwargs["lead_id"])
        serializer.instance = svc.create_quote(lead, serializer.validated_data, crm_profile(self.request))


class QuoteList(generics.ListAPIView):
    """GET /quotes/?status=sent  - cross-lead view (e.g. quotes awaiting a decision)."""

    serializer_class = QuoteSerializer

    def get_queryset(self):
        qs = QUOTES.filter(lead__deleted_at__isnull=True)
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs


class QuoteDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = QUOTES
    serializer_class = QuoteSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def perform_update(self, serializer):
        serializer.instance = svc.update_quote(serializer.instance, serializer.validated_data)

    def perform_destroy(self, instance):
        profile = crm_profile(self.request)
        if instance.status != "draft":
            raise ValidationError("Only draft quotes can be deleted.")
        if instance.created_by_id != profile.id and not profile.is_manager:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Only the author or a manager can delete this draft.")
        instance.delete()


class QuoteStatusView(APIView):
    def post(self, request, pk):
        quote = get_object_or_404(QUOTES, pk=pk)
        try:
            svc.set_status(quote, request.data.get("status", ""), crm_profile(request))
        except svc.QuoteError as e:
            return Response({"detail": str(e)}, status=400)
        except KeyError:
            return Response({"detail": "Unknown status."}, status=400)
        return Response(QuoteSerializer(QUOTES.get(pk=pk)).data)


class QuoteDuplicateView(APIView):
    def post(self, request, pk):
        new = svc.duplicate(get_object_or_404(QUOTES, pk=pk), crm_profile(request))
        return Response(QuoteSerializer(QUOTES.get(pk=new.pk)).data, status=201)


class QuotePDFView(APIView):
    def get(self, request, pk):
        quote = get_object_or_404(QUOTES, pk=pk)
        disp = "attachment" if request.query_params.get("download") else "inline"
        resp = HttpResponse(render_quote_pdf(quote), content_type="application/pdf")
        resp["Content-Disposition"] = f'{disp}; filename="{quote.number}.pdf"'
        return resp


class QuoteEmailView(APIView):
    """Email the PDF to the patient. Sending a draft also marks it sent."""

    def post(self, request, pk):
        quote = get_object_or_404(QUOTES, pk=pk)
        if not settings.RESEND_API_KEY:
            return Response({"detail": "Email is not configured on the server (RESEND_API_KEY)."}, status=503)
        if quote.status in ("rejected", "expired"):
            return Response({"detail": f"A {quote.status} quote cannot be sent."}, status=400)
        to = (request.data.get("to") or quote.lead.email or "").strip()
        if not to:
            return Response({"detail": "The lead has no email address. Enter one to send to."}, status=400)
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_email(to)
        except DjangoValidationError:
            return Response({"detail": "Enter a valid email address."}, status=400)

        profile = crm_profile(request)
        if quote.status == "draft":
            try:
                svc.set_status(quote, "sent", profile)
            except svc.QuoteError as e:
                return Response({"detail": str(e)}, status=400)
            quote = QUOTES.get(pk=pk)

        message = (request.data.get("message") or "").strip()
        html = (
            '<div style="font-family:Arial,sans-serif;line-height:1.6;color:#333">'
            f"<p>Dear {escape(quote.lead.name)},</p>"
            f"<p>{escape(message) if message else 'Thank you for choosing ' + escape(settings.CLINIC_NAME) + '. Please find your treatment estimate attached.'}</p>"
            f"<p>Estimate <b>{escape(quote.number)}</b> is valid until {quote.valid_until:%d %b %Y}.</p>"
            f"<p>Warm regards,<br/>{escape(settings.CLINIC_NAME)}</p></div>"
        )
        attachment = {"filename": f"{quote.number}.pdf", "content": base64.b64encode(render_quote_pdf(quote)).decode()}
        try:
            send_email([to], f"Your treatment estimate {quote.number} - {settings.CLINIC_NAME}", html, [attachment], settings.CLINIC_EMAIL)
        except Exception as e:
            return Response({"detail": f"Could not send the email: {e}"}, status=502)

        ContactLog.objects.create(lead=quote.lead, logged_by=profile, method="email", direction="outbound",
                                  notes=f"Quote {quote.number} emailed to {to}")
        Lead.objects.filter(pk=quote.lead_id).update(last_contacted_at=timezone.now())
        return Response(QuoteSerializer(QUOTES.get(pk=pk)).data)


class CatalogViewSet(viewsets.ModelViewSet):
    queryset = TreatmentCatalog.objects.all()
    serializer_class = TreatmentCatalogSerializer
    pagination_class = None

    def get_permissions(self):
        return [IsCRMUser()] if self.request.method in ("GET", "HEAD", "OPTIONS") else [IsCRMManager()]
