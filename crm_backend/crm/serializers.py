from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
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
                  "daily_lead_limit", "receives_auto_assign"]
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

    def get_score_band(self, obj):
        from .scoring import band

        return band(obj.score)

    class Meta:
        model = Lead
        fields = ["id", "name", "email", "phone", "country", "status", "source", "priority",
                  "assigned_to", "assigned_to_name", "tags", "treatment_interests",
                  "is_international", "last_contacted_at", "next_followup_at", "created_at", "score", "score_band"]

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.user.get_full_name() if obj.assigned_to else None


class LeadDetailSerializer(serializers.ModelSerializer):
    """Full read + create/edit of core fields. Status/assignment go through
    LeadPipelineSerializer so they can't be clobbered by a profile edit."""

    assigned_to_name = serializers.SerializerMethodField()
    score_band = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        exclude = ["deleted_at"]
        read_only_fields = ["status", "assigned_to", "converted_at", "created_at", "updated_at",
                            "score", "score_breakdown", "scored_at"]

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
