import csv

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import services
from ..filters import filter_leads
from ..models import Lead, LeadSource
from ..permissions import IsCRMManager, IsCRMUser, crm_profile
from ..serializers import (
    LeadDetailSerializer, LeadListSerializer, LeadPipelineSerializer, StatusHistorySerializer,
)
from ..tasks import notify_assigned, notify_new_lead

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
EXPORT_FIELDS = ["id", "name", "email", "phone", "country", "status", "source", "priority",
                 "assigned_to_name", "treatment_interests", "created_at"]


class Echo:
    def write(self, value):
        return value


class LeadViewSet(viewsets.ModelViewSet):
    ordering_fields = ["score", "created_at", "updated_at", "next_followup_at", "last_contacted_at", "name"]

    def get_queryset(self):
        qs = Lead.objects.alive().select_related("assigned_to__user")
        if self.action == "list":
            return filter_leads(self.request, qs)
        return qs

    def get_permissions(self):
        if self.action in ("destroy", "import_csv", "merge"):
            return [IsCRMManager()]
        return [IsCRMUser()]

    def get_serializer_class(self):
        return LeadListSerializer if self.action == "list" else LeadDetailSerializer

    def create(self, request, *args, **kwargs):
        ser = LeadDetailSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        data.setdefault("source", LeadSource.MANUAL)
        profile = crm_profile(request)
        lead, created = services.create_lead(changed_by=profile, **data)
        if not created:
            return Response(
                {"detail": "A lead with this phone/email already exists.", "existing_id": lead.id},
                status=http.HTTP_409_CONFLICT,
            )
        notify_new_lead(lead.id)
        return Response(LeadDetailSerializer(lead).data, status=http.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        instance.soft_delete()

    @action(detail=True, methods=["patch"])
    def pipeline(self, request, pk=None):
        """Restricted status/assignee/priority/follow-up update."""
        lead = self.get_object()
        old_assignee = lead.assigned_to_id
        old_status = lead.status
        ser = LeadPipelineSerializer(lead, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        new_status = ser.validated_data.pop("status", None)
        ser.save()
        if new_status and new_status != old_status:
            services.change_status(lead, new_status, crm_profile(request))
        if lead.assigned_to_id and lead.assigned_to_id != old_assignee:
            notify_assigned(lead.id)
        lead.refresh_from_db()
        return Response(LeadDetailSerializer(lead).data)

    @action(detail=True, methods=["get"], url_path="status-history")
    def status_history(self, request, pk=None):
        lead = self.get_object()
        return Response(StatusHistorySerializer(lead.status_history.all(), many=True).data)

    @action(detail=True, methods=["get"])
    def duplicates(self, request, pk=None):
        """Other live leads sharing this lead's phone or email (for the merge UI)."""
        from django.db.models import Q

        lead = self.get_object()
        q = Q()
        if lead.phone:
            q |= Q(phone=lead.phone)
        if lead.email:
            q |= Q(email__iexact=lead.email)
        qs = Lead.objects.alive().exclude(pk=lead.pk).filter(q) if q else Lead.objects.none()
        return Response(LeadListSerializer(qs[:10], many=True).data)

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        """Merge another lead (body: {"duplicate_id": N}) into this one."""
        primary = self.get_object()
        dup = get_object_or_404(Lead.objects.alive(), pk=request.data.get("duplicate_id"))
        if dup.pk == primary.pk:
            return Response({"detail": "Cannot merge a lead into itself."}, status=400)
        services.merge_leads(primary, dup)
        return Response(LeadDetailSerializer(primary).data)

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        qs = filter_leads(request, Lead.objects.alive().select_related("assigned_to__user"))
        writer = csv.writer(Echo())

        def rows():
            yield writer.writerow(EXPORT_FIELDS)
            for lead in qs.iterator():
                row = LeadListSerializer(lead).data
                yield writer.writerow(
                    [",".join(row[f]) if isinstance(row[f], list) else row[f] for f in EXPORT_FIELDS]
                )

        resp = StreamingHttpResponse(rows(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="leads-{timezone.localdate()}.csv"'
        return resp

    @action(detail=False, methods=["post"], url_path="import")
    def import_csv(self, request):
        """CSV columns: name,email,phone,country. Reports per-row errors."""
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "file is required"}, status=400)
        if f.size > MAX_IMPORT_BYTES:
            return Response({"detail": "File too large (max 2 MB)."}, status=400)
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return Response({"detail": "File must be UTF-8 encoded CSV."}, status=400)
        reader = csv.DictReader(text.splitlines())
        headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
        if "name" not in headers or not headers & {"phone", "email"}:
            return Response({"detail": "CSV needs a 'name' column and a 'phone' or 'email' column."}, status=400)
        reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]
        created, duplicates, errors = 0, 0, []
        profile = crm_profile(request)
        for i, row in enumerate(reader, start=2):
            if i - 1 > MAX_IMPORT_ROWS:
                errors.append({"row": i, "error": f"Row limit ({MAX_IMPORT_ROWS}) reached; remaining rows ignored"})
                break
            name = (row.get("name") or "").strip()
            phone = (row.get("phone") or "").strip()
            email = (row.get("email") or "").strip()
            if not name or not (phone or email):
                errors.append({"row": i, "error": "name and phone/email required"})
                continue
            if phone and not services.normalize_phone(phone, country=(row.get("country") or "")):
                errors.append({"row": i, "error": f"invalid phone: {phone}"})
                continue
            lead, was_created = services.create_lead(
                name=name, email=email, phone=phone, country=(row.get("country") or "").strip(),
                source=LeadSource.CSV, changed_by=profile,
            )
            if was_created:
                created += 1
                notify_new_lead(lead.id)
            else:
                duplicates += 1
        return Response({"created": created, "duplicates": duplicates, "errors": errors})
