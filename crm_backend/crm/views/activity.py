from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Appointment, ContactLog, Lead, Note, Task
from ..permissions import IsCRMUser, crm_profile
from ..tasks import delete_calendar_event, sync_appointment
from ..serializers import (
    AppointmentSerializer, ContactLogSerializer, NoteSerializer, TaskSerializer,
)


class LeadChildList(generics.ListCreateAPIView):
    """Base for /leads/:id/<children>/ list+create."""

    model = None
    pagination_class = None

    def lead(self):
        return get_object_or_404(Lead.objects.alive(), pk=self.kwargs["lead_id"])

    def get_queryset(self):
        return self.model.objects.filter(lead_id=self.kwargs["lead_id"])


class ContactList(LeadChildList):
    model = ContactLog
    serializer_class = ContactLogSerializer

    def perform_create(self, serializer):
        lead = self.lead()
        contact = serializer.save(lead=lead, logged_by=crm_profile(self.request))
        if lead.last_contacted_at is None or contact.contacted_at > lead.last_contacted_at:
            lead.last_contacted_at = contact.contacted_at
            lead.save(update_fields=["last_contacted_at", "updated_at"])


class NoteList(LeadChildList):
    model = Note
    serializer_class = NoteSerializer

    def perform_create(self, serializer):
        serializer.save(lead=self.lead(), author=crm_profile(self.request))


class NoteDetail(generics.DestroyAPIView):
    queryset = Note.objects.all()

    def perform_destroy(self, instance):
        profile = crm_profile(self.request)
        if instance.author_id != profile.id and not profile.is_manager:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only the author or a manager can delete this note.")
        instance.delete()


class LeadTaskList(LeadChildList):
    model = Task
    serializer_class = TaskSerializer

    def perform_create(self, serializer):
        profile = crm_profile(self.request)
        serializer.save(lead=self.lead(), assigned_to=serializer.validated_data.get("assigned_to") or profile)


class MyTasks(generics.ListAPIView):
    """GET /tasks/?bucket=overdue|today|upcoming"""

    serializer_class = TaskSerializer
    pagination_class = None

    def get_queryset(self):
        qs = Task.objects.filter(assigned_to=crm_profile(self.request), is_done=False, lead__deleted_at__isnull=True)
        now = timezone.now()
        end_today = timezone.localtime(now).replace(hour=23, minute=59, second=59)
        bucket = self.request.query_params.get("bucket")
        if bucket == "overdue":
            qs = qs.filter(due_at__lt=now)
        elif bucket == "today":
            qs = qs.filter(due_at__gte=now, due_at__lte=end_today)
        elif bucket == "upcoming":
            qs = qs.filter(due_at__gt=end_today)
        return qs.select_related("lead")


class TaskDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer

    def perform_update(self, serializer):
        task = serializer.instance
        done = serializer.validated_data.get("is_done")
        extra = {}
        if done is True and not task.is_done:
            extra["completed_at"] = timezone.now()
        elif done is False:
            extra["completed_at"] = None
        serializer.save(**extra)


class AppointmentList(generics.ListCreateAPIView):
    """GET /appointments/?from=&to=&doctor=&status=   POST creates (lead in body)."""

    serializer_class = AppointmentSerializer
    pagination_class = None

    def get_queryset(self):
        qs = Appointment.objects.select_related("lead", "doctor")
        p = self.request.query_params
        if p.get("from"):
            qs = qs.filter(start_at__gte=p["from"])
        if p.get("to"):
            qs = qs.filter(start_at__lte=p["to"])
        if p.get("doctor"):
            qs = qs.filter(doctor_id=p["doctor"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("lead"):
            qs = qs.filter(lead_id=p["lead"])
        return qs

    def perform_create(self, serializer):
        appt = serializer.save(created_by=crm_profile(self.request))
        sync_appointment(appt.id)
        # booking a consult moves an early-stage lead forward
        from .. import services
        from ..models import LeadStatus
        if appt.lead.status in (LeadStatus.NEW, LeadStatus.CONTACTED):
            services.change_status(appt.lead, LeadStatus.CONSULT_BOOKED, crm_profile(self.request))


class AppointmentDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Appointment.objects.all()
    serializer_class = AppointmentSerializer

    def perform_update(self, serializer):
        sync_appointment(serializer.save().id)

    def perform_destroy(self, instance):
        cal, event = instance.gcal_calendar_id, instance.gcal_event_id
        instance.delete()
        if event:
            delete_calendar_event(cal, event)
