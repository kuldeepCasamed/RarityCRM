from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    EmailCampaign,
    EmailSequence, EmailSequenceStep, SequenceEnrollment,
    EmailMessage, EmailTemplate, EmailThread,
    Quote, QuoteItem, TreatmentCatalog,
    MarketingSpend,
    CallSession,
    TREATMENT_SLUGS, Appointment, Branch, ContactLog, CRMUser, Doctor, Lead, LeadSource,
    Note, NotificationRecipient, StatusHistory, Task, TeamInvite,
)

User = get_user_model()


class CRMUserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = CRMUser
        fields = ["id", "name", "email", "role", "phone", "timezone", "is_active",
                  "daily_lead_limit", "receives_auto_assign", "email_from", "email_signature"]
        read_only_fields = ["id", "name", "email"]

    def get_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = "__all__"


class DoctorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Doctor
        fields = "__all__"


class LeadListSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.SerializerMethodField()
    score_band = serializers.SerializerMethodField()
    needs_reply = serializers.BooleanField(read_only=True, default=False)  # annotated by the list view

    def get_score_band(self, obj):
        from .scoring import band

        return band(obj.score)

    class Meta:
        model = Lead
        fields = ["id", "name", "email", "phone", "country", "status", "source", "priority",
                  "assigned_to", "assigned_to_name", "tags", "treatment_interests",
                  "is_international", "last_contacted_at", "next_followup_at", "created_at", "score", "score_band", "needs_reply", "email_opt_out", "email_invalid"]

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.user.get_full_name() if obj.assigned_to else None


class LeadDetailSerializer(serializers.ModelSerializer):
    """Full read + create/edit of core fields. Status/assignment go through
    LeadPipelineSerializer so they can't be clobbered by a profile edit."""

    assigned_to_name = serializers.SerializerMethodField()
    score_band = serializers.SerializerMethodField()
    needs_reply = serializers.SerializerMethodField()

    def get_needs_reply(self, obj):
        return obj.email_threads.filter(needs_reply=True).exists() if obj.pk else False

    class Meta:
        model = Lead
        exclude = ["deleted_at"]
        read_only_fields = ["status", "assigned_to", "converted_at", "created_at", "updated_at",
                            "score", "score_breakdown", "scored_at", "email_opt_out", "email_invalid", "unsubscribed_at"]

    def get_score_band(self, obj):
        from .scoring import band

        return band(obj.score)

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.user.get_full_name() if obj.assigned_to else None

    def validate_email(self, value):
        from .services import find_duplicate

        value = (value or "").strip().lower()
        if value and self.instance and (dup := find_duplicate(email=value, exclude_id=self.instance.pk)):
            raise serializers.ValidationError(f"Another lead ({dup.name}, #{dup.id}) already has this email.")
        return value

    def validate_tags(self, value):
        if not isinstance(value, list) or not all(isinstance(t, str) and len(t) <= 40 for t in value):
            raise serializers.ValidationError("Tags must be a list of short strings.")
        return value

    def validate(self, attrs):
        from .services import find_duplicate, normalize_phone

        if attrs.get("phone"):
            country = attrs.get("country", getattr(self.instance, "country", ""))
            e164 = normalize_phone(attrs["phone"], country=country)
            if not e164:
                raise serializers.ValidationError({"phone": "Enter a valid phone number (include the country code, or set the lead's country)."})
            if self.instance and (dup := find_duplicate(phone=e164, exclude_id=self.instance.pk)):
                raise serializers.ValidationError({"phone": f"Another lead ({dup.name}, #{dup.id}) already has this number."})
            attrs["phone"] = e164
        f, t = attrs.get("travel_from", getattr(self.instance, "travel_from", None)), attrs.get("travel_to", getattr(self.instance, "travel_to", None))
        if f and t and t < f:
            raise serializers.ValidationError({"travel_to": "Must be on or after the arrival date."})
        return attrs

    def validate_treatment_interests(self, value):
        bad = [v for v in value if v not in TREATMENT_SLUGS]
        if bad:
            raise serializers.ValidationError(f"Unknown treatments: {bad}")
        return value


class LeadPipelineSerializer(serializers.ModelSerializer):
    """Restricted PATCH: only pipeline fields."""

    class Meta:
        model = Lead
        fields = ["status", "assigned_to", "priority", "next_followup_at", "tags", "lost_reason"]


class ContactLogSerializer(serializers.ModelSerializer):
    logged_by_name = serializers.SerializerMethodField()
    has_recording = serializers.SerializerMethodField()

    class Meta:
        model = ContactLog
        fields = "__all__"
        read_only_fields = ["lead", "logged_by", "call_session"]

    def get_has_recording(self, obj):
        return bool(obj.call_session_id and obj.call_session.recording_url)

    def get_logged_by_name(self, obj):
        return obj.logged_by.user.get_full_name() if obj.logged_by else None


class NoteSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = "__all__"
        read_only_fields = ["lead", "author"]

    def get_author_name(self, obj):
        return obj.author.user.get_full_name() if obj.author else None


class TaskSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source="lead.name", read_only=True)

    class Meta:
        model = Task
        fields = "__all__"
        read_only_fields = ["lead", "completed_at"]


class AppointmentSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source="lead.name", read_only=True)

    class Meta:
        model = Appointment
        fields = "__all__"
        read_only_fields = ["created_by", "gcal_event_id", "gcal_calendar_id", "gcal_sync_error"]

    def validate(self, attrs):
        start = attrs.get("start_at", getattr(self.instance, "start_at", None))
        end = attrs.get("end_at", getattr(self.instance, "end_at", None))
        if start and end and end <= start:
            raise serializers.ValidationError("end_at must be after start_at")
        return attrs


class StatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StatusHistory
        fields = "__all__"


class NotificationRecipientSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationRecipient
        fields = "__all__"


class InviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=CRMUser._meta.get_field("role").choices)


class AcceptInviteSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    last_name = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(min_length=8, write_only=True)


class PublicInterestSerializer(serializers.Serializer):
    """Payload from raritydental.com ConsultationForm."""

    firstName = serializers.CharField(max_length=100)
    lastName = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    contactNumber = serializers.CharField(max_length=30)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    submissionUrl = serializers.URLField(max_length=1000, required=False, allow_blank=True, allow_null=True)
    referrer = serializers.CharField(max_length=1000, required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(max_length=2000, required=False, allow_blank=True, allow_null=True)
    utm_source = serializers.CharField(max_length=120, required=False, allow_blank=True, allow_null=True)
    utm_medium = serializers.CharField(max_length=120, required=False, allow_blank=True, allow_null=True)
    utm_campaign = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    utm_term = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    utm_content = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    gclid = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    fbclid = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    consent = serializers.BooleanField(required=False, default=False)
    website = serializers.CharField(required=False, allow_blank=True, allow_null=True)  # honeypot
    # Smile Studio consultation request (all optional; plain form posts are unaffected)
    treatment_interest = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True)
    consultation_type = serializers.ChoiceField(choices=["clinic", "virtual"], required=False, allow_null=True)
    preferred_date = serializers.DateField(required=False, allow_null=True)
    preferred_time = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    design_summary = serializers.CharField(max_length=500, required=False, allow_blank=True, allow_null=True)
    international = serializers.BooleanField(required=False, default=False)
    smile_studio_reference = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True)


class CallSessionSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source="lead.name", read_only=True)
    user_name = serializers.SerializerMethodField()
    has_recording = serializers.SerializerMethodField()

    class Meta:
        model = CallSession
        fields = ["id", "lead", "lead_name", "crm_user", "user_name", "to_number", "status", "started_at",
                  "ended_at", "duration_sec", "has_recording", "logged", "created_at"]

    def get_user_name(self, obj):
        return obj.crm_user.user.get_full_name() if obj.crm_user else None

    def get_has_recording(self, obj):
        return bool(obj.recording_url)


class MarketingSpendSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketingSpend
        fields = "__all__"
        read_only_fields = ["created_by", "created_at"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_campaign(self, value):
        return value.strip()


class TreatmentCatalogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TreatmentCatalog
        fields = "__all__"

    def validate_default_price(self, v):
        if v < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return v


class QuoteItemSerializer(serializers.ModelSerializer):
    quantity = serializers.IntegerField(min_value=1, max_value=999)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)

    class Meta:
        model = QuoteItem
        fields = ["id", "description", "area", "quantity", "unit_price", "line_total"]
        read_only_fields = ["id", "line_total"]


class QuoteSerializer(serializers.ModelSerializer):
    items = QuoteItemSerializer(many=True, allow_empty=False)
    lead_name = serializers.CharField(source="lead.name", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    discount_value = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False, default=0)
    tax_percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False, default=0)

    class Meta:
        model = Quote
        fields = ["id", "lead", "lead_name", "number", "status", "currency", "valid_until", "discount_type", "discount_value",
                  "tax_percent", "notes", "terms", "subtotal", "discount_amount", "tax_amount", "total", "items",
                  "created_by_name", "sent_at", "decided_at", "created_at"]
        read_only_fields = ["id", "lead", "number", "status", "subtotal", "discount_amount", "tax_amount", "total",
                            "sent_at", "decided_at", "created_at"]
        extra_kwargs = {"valid_until": {"required": False}}

    def get_created_by_name(self, obj):
        return obj.created_by.user.get_full_name() if obj.created_by else None

    def validate(self, attrs):
        from .pricing import calculate

        inst = self.instance
        if inst and inst.status != "draft":
            raise serializers.ValidationError("Only draft quotes can be edited. Use Duplicate to make a revision.")
        dtype = attrs.get("discount_type", inst.discount_type if inst else "percent")
        dval = attrs.get("discount_value", inst.discount_value if inst else 0)
        if dtype == "percent" and dval > 100:
            raise serializers.ValidationError({"discount_value": "A percentage discount cannot exceed 100."})
        items = attrs.get("items")
        if items is not None:
            t = calculate([(i["quantity"], i["unit_price"]) for i in items], dtype, dval, 0)
            if dtype == "amount" and dval > t.subtotal:
                raise serializers.ValidationError({"discount_value": "Discount is larger than the subtotal."})
        return attrs


class EmailMessageSerializer(serializers.ModelSerializer):
    sent_by_name = serializers.SerializerMethodField()
    attachments = serializers.JSONField(read_only=True)

    class Meta:
        model = EmailMessage
        fields = ["id", "thread", "direction", "status", "from_address", "to_address", "subject", "body_text", "body_full",
                  "error", "sent_by_name", "attachments", "delivered_at", "opened_at", "clicked_at", "created_at"]

    def get_sent_by_name(self, obj):
        return obj.sent_by.user.get_full_name() if obj.sent_by else None


class EmailThreadSerializer(serializers.ModelSerializer):
    messages = EmailMessageSerializer(many=True, read_only=True)
    lead_name = serializers.CharField(source="lead.name", read_only=True, default=None)

    class Meta:
        model = EmailThread
        fields = ["id", "lead", "lead_name", "subject", "needs_reply", "last_message_at", "created_at", "messages"]


class EmailThreadRowSerializer(serializers.ModelSerializer):
    """Inbox list row: last message preview only."""

    lead_name = serializers.CharField(source="lead.name", read_only=True, default=None)
    assigned_to_name = serializers.SerializerMethodField()
    preview = serializers.SerializerMethodField()
    from_address = serializers.SerializerMethodField()

    class Meta:
        model = EmailThread
        fields = ["id", "lead", "lead_name", "assigned_to_name", "subject", "needs_reply", "last_message_at", "preview", "from_address"]

    def _last(self, obj):
        msgs = list(obj.messages.all())
        return msgs[-1] if msgs else None

    def get_preview(self, obj):
        m = self._last(obj)
        return (m.body_text or "")[:200] if m else ""

    def get_from_address(self, obj):
        m = self._last(obj)
        return m.from_address if m else ""

    def get_assigned_to_name(self, obj):
        a = obj.lead.assigned_to if obj.lead_id else None
        return a.user.get_full_name() if a else None


class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = ["id", "name", "subject", "body", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class SequenceStepSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True)

    class Meta:
        model = EmailSequenceStep
        fields = ["id", "position", "delay_hours", "template", "template_name"]
        read_only_fields = ["id", "position"]  # position is the order of the list you send

    def validate_delay_hours(self, v):
        if v > 24 * 180:
            raise serializers.ValidationError("Wait at most 180 days between steps.")
        return v

    def validate_template(self, t):
        if not t.is_active:
            raise serializers.ValidationError(f"Template '{t.name}' is inactive.")
        return t


class SequenceSerializer(serializers.ModelSerializer):
    steps = SequenceStepSerializer(many=True, allow_empty=False)
    stats = serializers.SerializerMethodField()
    from_user_name = serializers.CharField(source="from_user.user.get_full_name", read_only=True, default=None)

    class Meta:
        model = EmailSequence
        fields = ["id", "name", "description", "is_active", "auto_enroll", "auto_enroll_sources", "run_while_status",
                  "from_user", "from_user_name", "steps", "stats", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_stats(self, obj):
        e = SequenceEnrollment.objects.filter(sequence=obj)
        return {
            "active": e.filter(state="active").count(), "completed": e.filter(state="completed").count(),
            "stopped": e.filter(state="stopped").count(), "replied": e.filter(stop_reason="replied").count(),
            "booked": e.filter(stop_reason="booked").count(), "unsubscribed": e.filter(stop_reason="unsubscribed").count(),
            "emails_sent": EmailMessage.objects.filter(enrollment__sequence=obj).exclude(status="failed").count(),
        }

    def validate_auto_enroll_sources(self, v):
        valid = {c for c, _ in LeadSource.choices}
        bad = [x for x in v if x not in valid]
        if bad:
            raise serializers.ValidationError(f"Unknown sources: {bad}")
        return v

    def validate_run_while_status(self, v):
        from .models import LeadStatus

        valid = {c for c, _ in LeadStatus.choices}
        bad = [x for x in v if x not in valid]
        if bad:
            raise serializers.ValidationError(f"Unknown statuses: {bad}")
        return v

    def validate(self, attrs):
        if attrs.get("auto_enroll", getattr(self.instance, "auto_enroll", False)) and not attrs.get(
                "auto_enroll_sources", getattr(self.instance, "auto_enroll_sources", [])):
            raise serializers.ValidationError({"auto_enroll_sources": "Choose at least one lead source to auto-enrol."})
        return attrs

    def _save_steps(self, seq, steps):
        seq.steps.all().delete()
        EmailSequenceStep.objects.bulk_create(
            [EmailSequenceStep(sequence=seq, position=i, delay_hours=s["delay_hours"], template=s["template"]) for i, s in enumerate(steps)])

    def create(self, data):
        from django.db import transaction

        steps = data.pop("steps")
        if not data.get("run_while_status"):
            data["run_while_status"] = ["new", "contacted"]
        with transaction.atomic():
            seq = EmailSequence.objects.create(**data)
            self._save_steps(seq, steps)
        return seq

    def update(self, seq, data):
        from django.db import transaction

        steps = data.pop("steps", None)
        with transaction.atomic():
            for k, v in data.items():
                setattr(seq, k, v)
            if not seq.run_while_status:
                seq.run_while_status = ["new", "contacted"]
            seq.save()
            if steps is not None:
                self._save_steps(seq, steps)
        return seq


class EnrollmentSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source="lead.name", read_only=True)
    sequence_name = serializers.CharField(source="sequence.name", read_only=True)
    total_steps = serializers.SerializerMethodField()

    class Meta:
        model = SequenceEnrollment
        fields = ["id", "lead", "lead_name", "sequence", "sequence_name", "state", "stop_reason", "current_step", "total_steps",
                  "next_send_at", "last_sent_at", "enrolled_at", "finished_at"]

    def get_total_steps(self, obj):
        return obj.sequence.steps.count()


class CampaignSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True, default=None)
    from_user_name = serializers.CharField(source="from_user.user.get_full_name", read_only=True, default=None)
    created_by_name = serializers.CharField(source="created_by.user.get_full_name", read_only=True, default=None)
    counts = serializers.SerializerMethodField()

    class Meta:
        model = EmailCampaign
        fields = ["id", "name", "subject", "body", "template", "template_name", "audience_filters", "skip_recent_days",
                  "skip_open_conversations", "from_user", "from_user_name", "status", "scheduled_at", "started_at", "finished_at",
                  "created_by_name", "created_at", "counts"]
        read_only_fields = ["id", "status", "scheduled_at", "started_at", "finished_at", "created_at"]

    def get_counts(self, obj):
        r = obj.recipients.all()
        return {"recipients": r.count(), "sent": r.filter(state="sent").count(), "pending": r.filter(state="pending").count()}

    def validate_audience_filters(self, v):
        from .campaigns import clean_filters

        try:
            return clean_filters(v)
        except ValueError as e:
            raise serializers.ValidationError(str(e))

    def validate_skip_recent_days(self, v):
        if v > 90:
            raise serializers.ValidationError("At most 90 days.")
        return v

    def validate(self, attrs):
        if self.instance and self.instance.status != "draft":
            raise serializers.ValidationError("Only a draft campaign can be edited.")
        return attrs
