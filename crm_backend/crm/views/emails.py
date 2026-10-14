import json

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import generics, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import campaigns, emailing
from ..models import CampaignRecipient, EmailCampaign, EmailMessage, EmailSequence, EmailTemplate, EmailThread, Lead, Note, SequenceEnrollment
from ..permissions import IsCRMManager, IsCRMUser, crm_profile
from ..serializers import (
    EmailMessageSerializer, EmailTemplateSerializer, EmailThreadRowSerializer, EmailThreadSerializer,
    CampaignSerializer, EnrollmentSerializer, SequenceSerializer,
)

THREADS = EmailThread.objects.select_related("lead__assigned_to__user").prefetch_related(
    Prefetch("messages", queryset=EmailMessage.objects.select_related("sent_by__user")))


class LeadEmails(APIView):
    """GET: the lead's conversations.  POST {subject, body, thread_id?, copy_to_sender?}: send an email to the lead."""

    def get(self, request, lead_id):
        lead = get_object_or_404(Lead.objects.alive(), pk=lead_id)
        data = EmailThreadSerializer(THREADS.filter(lead=lead).order_by("-last_message_at"), many=True).data
        return Response({"threads": data, "can_email": bool(lead.email) and not lead.email_invalid,
                         "email": lead.email, "email_invalid": lead.email_invalid, "email_opt_out": lead.email_opt_out,
                         "sender": {k: v for k, v in emailing.sender_identity(crm_profile(request)).items() if k != "wanted"},
                         "reply_capture": bool(settings.EMAIL_REPLY_DOMAIN)})

    def post(self, request, lead_id):
        lead = get_object_or_404(Lead.objects.alive(), pk=lead_id)
        thread = None
        if request.data.get("thread_id"):
            thread = get_object_or_404(EmailThread, pk=request.data["thread_id"], lead=lead)
        try:
            msg = emailing.send_to_lead(lead, subject=request.data.get("subject", ""), body=request.data.get("body", ""),
                                        by=crm_profile(request), thread=thread, copy_to_sender=bool(request.data.get("copy_to_sender")))
        except emailing.EmailError as e:
            return Response({"detail": str(e)}, status=400)
        return Response(EmailMessageSerializer(msg).data, status=201)


class EmailPreview(APIView):
    """Shows exactly what will be sent (merge fields filled, sender), without sending."""

    def post(self, request, lead_id):
        lead = get_object_or_404(Lead.objects.alive(), pk=lead_id)
        profile = crm_profile(request)
        who = emailing.sender_identity(profile)
        values = emailing.merge_values(lead, who["name"])
        return Response({"subject": emailing.render_merge(request.data.get("subject", ""), values),
                         "body": emailing.with_signature(emailing.render_merge(request.data.get("body", ""), values), profile),
                         "from": who["formatted"], "used_fallback": who["used_fallback"]})


class Inbox(generics.ListAPIView):
    """GET /inbox/?kind=needs_reply|unmatched|all"""

    serializer_class = EmailThreadRowSerializer

    def get_queryset(self):
        qs = THREADS.filter(Q(lead__isnull=True) | Q(lead__deleted_at__isnull=True)).annotate(n=Count("messages")).filter(n__gt=0)
        kind = self.request.query_params.get("kind", "needs_reply")
        if kind == "unmatched":
            qs = qs.filter(lead__isnull=True)
        elif kind == "needs_reply":
            qs = qs.filter(needs_reply=True)
        if self.request.query_params.get("mine") == "1":
            qs = qs.filter(lead__assigned_to=crm_profile(self.request))
        return qs.order_by("-last_message_at")


class InboxCount(APIView):
    def get(self, request):
        base = EmailThread.objects.filter(Q(lead__isnull=True) | Q(lead__deleted_at__isnull=True))
        return Response({"needs_reply": base.filter(needs_reply=True).count(), "unmatched": base.filter(lead__isnull=True).count()})


class ThreadResolve(APIView):
    """Mark a conversation as handled (clears 'Needs reply')."""

    def post(self, request, pk):
        t = get_object_or_404(EmailThread, pk=pk)
        t.needs_reply = False
        t.save(update_fields=["needs_reply"])
        return Response({"id": t.id, "needs_reply": False})


class ThreadAssign(APIView):
    """Attach an unmatched conversation to a lead."""

    def post(self, request, pk):
        thread = get_object_or_404(EmailThread, pk=pk, lead__isnull=True)
        lead = get_object_or_404(Lead.objects.alive(), pk=request.data.get("lead_id"))
        thread.lead = lead
        thread.save(update_fields=["lead"])
        last_in = thread.messages.filter(direction="in").order_by("-created_at").first()
        if last_in:
            emailing.apply_inbound_effects(lead, thread, last_in)
        return Response(EmailThreadSerializer(THREADS.get(pk=thread.pk)).data)


class TemplateViewSet(viewsets.ModelViewSet):
    queryset = EmailTemplate.objects.all()
    serializer_class = EmailTemplateSerializer
    pagination_class = None

    def get_permissions(self):
        return [IsCRMUser()] if self.request.method in ("GET", "HEAD", "OPTIONS") else [IsCRMManager()]

    def perform_create(self, serializer):
        serializer.save(created_by=crm_profile(self.request))

    def perform_destroy(self, instance):
        from django.db.models import ProtectedError
        from rest_framework.exceptions import ValidationError

        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError("This template is used by an email sequence. Remove it from the sequence first (or just deactivate the template).")


class SequenceViewSet(viewsets.ModelViewSet):
    queryset = EmailSequence.objects.select_related("from_user__user").prefetch_related("steps__template")
    serializer_class = SequenceSerializer
    pagination_class = None

    def get_permissions(self):
        return [IsCRMUser()] if self.request.method in ("GET", "HEAD", "OPTIONS") else [IsCRMManager()]

    def perform_create(self, serializer):
        serializer.save(created_by=crm_profile(self.request))

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError

        if instance.enrollments.filter(state="active").exists():
            raise ValidationError("This sequence has leads running through it. Deactivate it (or stop those enrolments) before deleting.")
        instance.delete()

    @action(detail=True, methods=["get"])
    def enrollments(self, request, pk=None):
        qs = SequenceEnrollment.objects.filter(sequence_id=pk).select_related("lead", "sequence")
        if request.query_params.get("state"):
            qs = qs.filter(state=request.query_params["state"])
        return Response(EnrollmentSerializer(qs[:300], many=True).data)

    @action(detail=True, methods=["post"])
    def enroll(self, request, pk=None):
        """Bulk enrol leads: {"lead_ids": [1,2,3]}. Ineligible leads are skipped and reported, never forced."""
        seq = self.get_object()
        enrolled, skipped = 0, []
        for lead in Lead.objects.alive().filter(pk__in=request.data.get("lead_ids", [])[:500]):
            try:
                emailing.enroll(lead, seq, crm_profile(request))
                enrolled += 1
            except emailing.EmailError as e:
                skipped.append({"lead_id": lead.id, "lead": lead.name, "reason": str(e).split(": ", 1)[-1].rstrip(".")})
        if enrolled:
            from ..tasks import kick_sequences

            kick_sequences()
        return Response({"enrolled": enrolled, "skipped": skipped})


class CampaignViewSet(viewsets.ModelViewSet):
    """Everything about campaigns is manager-only."""

    queryset = EmailCampaign.objects.select_related("template", "from_user__user", "created_by__user")
    serializer_class = CampaignSerializer
    permission_classes = [IsCRMManager]
    pagination_class = None

    def perform_create(self, serializer):
        serializer.save(created_by=crm_profile(self.request))

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError

        if instance.status not in ("draft", "cancelled"):
            raise ValidationError("Only drafts and cancelled campaigns can be deleted. Cancel it first to stop sending.")
        instance.delete()

    def _run(self, fn, campaign, **kw):
        try:
            return fn(campaign, **kw), None
        except emailing.EmailError as e:
            return None, Response({"detail": str(e)}, status=400)

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """{"scheduled_at": "2026-10-20T10:00:00+05:30"}  (omit to send as soon as office hours allow)"""
        from rest_framework import serializers as drf

        c = self.get_object()
        when = None
        if request.data.get("scheduled_at"):
            try:
                when = drf.DateTimeField().to_internal_value(request.data["scheduled_at"])
            except drf.ValidationError:
                return Response({"detail": "Invalid scheduled_at."}, status=400)
        n, err = self._run(campaigns.launch, c, when=when)
        if err:
            return err
        c.refresh_from_db()
        return Response({**CampaignSerializer(c).data, "recipients_frozen": n})

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        c = self.get_object()
        profile = crm_profile(request)
        to, err = self._run(campaigns.send_test, c, by=profile, to=(request.data.get("to") or profile.user.email or "").strip())
        return err or Response({"sent_to": to})

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        c = self.get_object()
        _, err = self._run(campaigns.pause, c)
        return err or Response(CampaignSerializer(EmailCampaign.objects.get(pk=c.pk)).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        c = self.get_object()
        _, err = self._run(campaigns.resume, c)
        return err or Response(CampaignSerializer(EmailCampaign.objects.get(pk=c.pk)).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        c = self.get_object()
        n, err = self._run(campaigns.cancel, c)
        return err or Response({**CampaignSerializer(EmailCampaign.objects.get(pk=c.pk)).data, "skipped": n})

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None):
        return Response(campaigns.report(self.get_object()))

    @action(detail=True, methods=["get"])
    def recipients(self, request, pk=None):
        qs = CampaignRecipient.objects.filter(campaign_id=pk).select_related("lead", "message")
        if request.query_params.get("state"):
            qs = qs.filter(state=request.query_params["state"])
        return Response([{"id": r.id, "lead": r.lead_id, "lead_name": r.lead.name, "email": r.lead.email, "state": r.state, "skip_reason": r.skip_reason,
                          "status": r.message.status if r.message else None, "opened": bool(r.message and r.message.opened_at),
                          "clicked": bool(r.message and r.message.clicked_at), "sent_at": r.sent_at} for r in qs.order_by("id")[:500]])


class CampaignAudience(APIView):
    """Live preview while building a campaign: who would receive it, and exactly why others are left out."""

    permission_classes = [IsCRMManager]

    def post(self, request):
        try:
            filters = campaigns.clean_filters(request.data.get("filters"))
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        recent = int(request.data.get("skip_recent_days", 3) or 0)
        qs, info = campaigns.audience(filters, recent, bool(request.data.get("skip_open_conversations", True)))
        sample = list(qs.order_by("-created_at").values("id", "name", "email")[:8])
        return Response({**info, "sample": sample, "limit": settings.EMAIL_CAMPAIGN_MAX_RECIPIENTS,
                         "over_limit": info["eligible"] > settings.EMAIL_CAMPAIGN_MAX_RECIPIENTS})


class LeadEnrollments(APIView):
    """GET /leads/:id/enrollments/   POST {sequence_id} /leads/:id/enroll/"""

    def get(self, request, lead_id):
        qs = SequenceEnrollment.objects.filter(lead_id=lead_id).select_related("lead", "sequence")
        return Response(EnrollmentSerializer(qs, many=True).data)

    def post(self, request, lead_id):
        lead = get_object_or_404(Lead.objects.alive(), pk=lead_id)
        seq = get_object_or_404(EmailSequence, pk=request.data.get("sequence_id"))
        try:
            enr = emailing.enroll(lead, seq, crm_profile(request))
        except emailing.EmailError as e:
            return Response({"detail": str(e)}, status=400)
        from ..tasks import kick_sequences

        kick_sequences()
        return Response(EnrollmentSerializer(enr).data, status=201)


class EnrollmentStop(APIView):
    def post(self, request, pk):
        enr = get_object_or_404(SequenceEnrollment, pk=pk)
        if enr.state != "active":
            return Response({"detail": "Already finished."}, status=400)
        emailing.stop_enrollment(enr, "manual")
        enr.refresh_from_db()
        return Response(EnrollmentSerializer(enr).data)


class EmailStatus(APIView):
    """Setup checklist for the Settings screen."""

    def get(self, request):
        who = emailing.sender_identity(crm_profile(request))
        return Response({
            "resend_key": bool(settings.RESEND_API_KEY), "webhook_secret": bool(settings.RESEND_WEBHOOK_SECRET),
            "reply_domain": settings.EMAIL_REPLY_DOMAIN, "verified_domains": settings.EMAIL_VERIFIED_DOMAINS,
            "default_from": emailing.default_from_address(), "my_sender": who["formatted"], "my_sender_is_fallback": who["used_fallback"],
            "my_wanted_address": who["wanted"], "webhook_url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/crm/email/webhook/",
            "merge_fields": emailing.MERGE_FIELDS, "daily_cap": settings.EMAIL_DAILY_CAP, "sent_today": emailing.sent_today(),
            "send_window": f"{settings.EMAIL_SEND_START_HOUR:02d}:00-{settings.EMAIL_SEND_END_HOUR:02d}:00",
        })


@csrf_exempt
@require_POST
def webhook(request):
    """Resend -> CRM. Public URL, protected by the signature (Svix scheme). A processing error returns 500 so Resend retries."""
    body = request.body
    if not emailing.verify_signature(settings.RESEND_WEBHOOK_SECRET, request.headers, body):
        return HttpResponse("invalid signature", status=403)
    try:
        payload = json.loads(body)
    except ValueError:
        return HttpResponse("bad json", status=400)
    try:
        result = emailing.process_event(request.headers.get("svix-id", ""), payload)
    except Exception:
        emailing.log.exception("email webhook failed (%s)", payload.get("type"))
        return HttpResponse("processing error", status=500)
    return JsonResponse({"status": result})


# ---------------------------------------------------------------- public unsubscribe page (link in every automated email)
def _page(title: str, body_html: str, status: int = 200):
    from html import escape

    html = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title></head><body style=\"font-family:Arial,sans-serif;max-width:480px;margin:12vh auto;padding:0 20px;color:#333\">"
            f"<h2 style='color:#72383d'>{escape(settings.CLINIC_NAME)}</h2>{body_html}</body></html>")
    resp = HttpResponse(html, status=status)
    resp["X-Robots-Tag"] = "noindex"
    resp["Cache-Control"] = "no-store"
    return resp


@csrf_exempt
def unsubscribe(request, token):
    """GET shows a confirm button (so email security scanners that open links can't unsubscribe anyone);
    POST unsubscribes (also what mail apps send for one-click List-Unsubscribe)."""
    from django.views.decorators.http import require_http_methods

    @require_http_methods(["GET", "HEAD", "POST"])
    def handle(request):
        lead = emailing.lead_from_unsubscribe_token(token)
        if lead is None:
            return _page("Invalid link", "<p>This unsubscribe link is not valid. Please reply to the email and we will remove you.</p>", 400)
        if request.method == "POST":
            if not lead.email_opt_out:
                emailing.mark_opt_out(lead)
                Note.objects.create(lead=lead, body="Unsubscribed from automated emails using the link in an email.")
            return _page("Unsubscribed", "<p>You have been unsubscribed and will not receive further automated emails from us.</p>"
                                          "<p style='color:#888;font-size:13px'>If you contact us again, a team member may still reply to you personally.</p>")
        return _page("Unsubscribe", "<p>Do you want to stop receiving these emails?</p>"
                                    "<form method='post'><button style='background:#72383d;color:#fff;border:0;padding:10px 18px;border-radius:4px;font-size:15px;cursor:pointer'>Yes, unsubscribe me</button></form>")

    return handle(request)
