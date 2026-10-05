from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import CallSession, ContactLog, Lead
from ..permissions import IsCRMUser, crm_profile
from ..serializers import CallSessionSerializer
from ..throttles import CallThrottle
from ..utils import twilio_service as tw

TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled"}
STATUS_MAP = {"initiated": "initiated", "ringing": "ringing", "answered": "in-progress", "in-progress": "in-progress"}


def _not_configured():
    return Response({"detail": "Calling is not configured on the server."}, status=503)


class CallTokenView(APIView):
    def get(self, request):
        if not tw.is_configured():
            return _not_configured()
        profile = crm_profile(request)
        return Response({"token": tw.make_token(f"crm-{profile.id}"), "ttl": 3600})


class CallInitiateView(APIView):
    throttle_classes = [CallThrottle]

    def post(self, request):
        if not tw.is_configured():
            return _not_configured()
        lead = get_object_or_404(Lead.objects.alive(), pk=request.data.get("lead_id"))
        if not lead.phone:
            return Response({"detail": "This lead has no phone number."}, status=400)
        session = CallSession.objects.create(
            lead=lead, crm_user=crm_profile(request), to_number=lead.phone, from_number=settings.TWILIO_FROM_NUMBER
        )
        return Response({"id": session.id, "to": lead.phone}, status=201)


class CallListView(generics.ListAPIView):
    serializer_class = CallSessionSerializer

    def get_queryset(self):
        qs = CallSession.objects.select_related("lead", "crm_user__user")
        if self.request.query_params.get("lead"):
            qs = qs.filter(lead_id=self.request.query_params["lead"])
        return qs


class CallDetailView(generics.RetrieveAPIView):
    queryset = CallSession.objects.select_related("lead", "crm_user__user")
    serializer_class = CallSessionSerializer


class CallLogView(APIView):
    """Post-call outcome: creates the ContactLog for a finished call (once)."""

    def post(self, request, pk):
        session = get_object_or_404(CallSession, pk=pk)
        if session.logged:
            return Response({"detail": "This call is already logged."}, status=409)
        profile = crm_profile(request)
        log = ContactLog.objects.create(
            lead=session.lead, logged_by=profile, method="call", direction="outbound",
            outcome=request.data.get("outcome", ""), notes=request.data.get("notes", ""),
            duration_sec=session.duration_sec, call_session=session,
            contacted_at=session.started_at or session.created_at,
        )
        session.logged = True
        session.save(update_fields=["logged"])
        lead = session.lead
        lead.last_contacted_at = log.contacted_at
        fields = ["last_contacted_at", "updated_at"]
        if request.data.get("next_followup_at"):
            lead.next_followup_at = request.data["next_followup_at"]
            fields.append("next_followup_at")
        lead.save(update_fields=fields)
        return Response({"contact_id": log.id}, status=201)


class CallRecordingView(APIView):
    def get(self, request, pk):
        session = get_object_or_404(CallSession, pk=pk)
        if not session.recording_url:
            return Response({"detail": "No recording."}, status=404)
        upstream = tw.open_recording(session.recording_url)
        if upstream.status_code != 200:
            return Response({"detail": "Recording unavailable."}, status=502)
        return StreamingHttpResponse(upstream.iter_content(8192), content_type="audio/mpeg")


# ---------------- Twilio webhooks (signature-verified, no user auth) ----------------
class TwilioWebhook(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not tw.valid_signature(request._request):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Invalid Twilio signature")


class VoiceTwimlView(TwilioWebhook):
    """TwiML App voice URL. The browser SDK sends SessionId; the dialled number always comes
    from our DB, never from the (client-controlled) `To` param."""

    def post(self, request):
        from twilio.twiml.voice_response import Dial, VoiceResponse

        resp = VoiceResponse()
        try:
            session = CallSession.objects.get(pk=int(request.POST.get("SessionId", "")))
        except (CallSession.DoesNotExist, ValueError):
            resp.say("Sorry, this call could not be placed.")
            return HttpResponse(str(resp), content_type="text/xml")

        fresh = session.created_at > timezone.now() - timedelta(minutes=5)
        identity_ok = request.POST.get("From", "") == f"client:crm-{session.crm_user_id}"
        if session.call_sid or not fresh or not identity_ok:
            resp.say("Sorry, this call could not be placed.")
            return HttpResponse(str(resp), content_type="text/xml")

        session.call_sid = request.POST.get("CallSid", "")
        session.save(update_fields=["call_sid"])

        base = settings.PUBLIC_BASE_URL.rstrip("/") + "/api/crm/calls"
        kwargs = {"caller_id": settings.TWILIO_FROM_NUMBER, "answer_on_bridge": True}
        if settings.TWILIO_RECORD_CALLS:
            kwargs.update(record="record-from-answer-dual", recording_status_callback=f"{base}/recording-callback/")
        dial = Dial(**kwargs)
        dial.number(session.to_number, status_callback=f"{base}/status-callback/",
                    status_callback_event="initiated ringing answered completed")
        resp.append(dial)
        return HttpResponse(str(resp), content_type="text/xml")


class StatusCallbackView(TwilioWebhook):
    def post(self, request):
        parent = request.POST.get("ParentCallSid") or request.POST.get("CallSid", "")
        session = CallSession.objects.filter(call_sid=parent).first()
        if not session:
            return HttpResponse("")
        status = request.POST.get("CallStatus", "")
        session.child_sid = request.POST.get("CallSid", session.child_sid)
        if status in TERMINAL:
            session.status = status
            session.ended_at = timezone.now()
            session.duration_sec = int(request.POST.get("CallDuration") or 0)
        elif status in STATUS_MAP and session.status not in TERMINAL:
            session.status = STATUS_MAP[status]
            if status in ("answered", "in-progress") and not session.started_at:
                session.started_at = timezone.now()
        session.save()
        return HttpResponse("")


class RecordingCallbackView(TwilioWebhook):
    def post(self, request):
        session = CallSession.objects.filter(call_sid=request.POST.get("CallSid", "")).first()
        if session and request.POST.get("RecordingStatus") == "completed":
            session.recording_sid = request.POST.get("RecordingSid", "")
            session.recording_url = request.POST.get("RecordingUrl", "")
            session.save(update_fields=["recording_sid", "recording_url"])
        return HttpResponse("")
