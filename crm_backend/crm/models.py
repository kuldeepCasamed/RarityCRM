import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    REP = "rep", "Sales Rep"
    FRONT_DESK = "front_desk", "Front Desk"


class LeadStatus(models.TextChoices):
    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    CONSULT_BOOKED = "consult_booked", "Consult Booked"
    CONSULT_DONE = "consult_done", "Consult Done"
    PLAN_SENT = "treatment_plan_sent", "Treatment Plan Sent"
    NEGOTIATING = "negotiating", "Negotiating"
    TREATMENT_BOOKED = "treatment_booked", "Treatment Booked"
    CONVERTED = "converted", "Converted"
    LOST = "lost", "Lost"
    NOT_INTERESTED = "not_interested", "Not Interested"
    INVALID = "invalid", "Invalid"


class LeadSource(models.TextChoices):
    WEBSITE = "website", "Website Form"
    GOOGLE_ADS = "google_ads", "Google Ads"
    META_ADS = "meta_ads", "Meta Ads"
    WHATSAPP = "whatsapp", "WhatsApp"
    PHONE = "phone", "Phone Call-in"
    WALK_IN = "walk_in", "Walk-in"
    REFERRAL = "referral", "Referral"
    PARTNER = "partner", "Partner / Facilitator"
    CSV = "csv", "CSV Import"
    MANUAL = "manual", "Manual"


class Priority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class Treatment(models.TextChoices):
    IMPLANTS = "implants", "Dental Implants"
    INVISALIGN = "invisalign", "Invisalign / Aligners"
    SMILE_DESIGN = "smile_design", "Smile Designing"
    SINGLE_DAY = "single_day", "Single-Day Dentistry"
    ROOT_CANAL = "root_canal", "Root Canal"
    BRACES = "braces", "Braces"
    WHITENING = "whitening", "Whitening"
    FULL_MOUTH = "full_mouth", "Full Mouth Rehab"
    PEDIATRIC = "pediatric", "Pediatric"
    OTHER = "other", "Other"


TREATMENT_SLUGS = {t.value for t in Treatment}


class CRMUser(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="crm_profile"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.REP)
    phone = models.CharField(max_length=20, blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    is_active = models.BooleanField(default=True)
    daily_lead_limit = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    receives_auto_assign = models.BooleanField(default=True)
    email_from = models.EmailField(blank=True, help_text="Address emails to leads are sent from (must be on a domain verified in Resend, else the default sender is used)")
    email_signature = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.role})"

    @property
    def is_manager(self):
        return self.role in (Role.ADMIN, Role.MANAGER)


def _invite_token():
    return secrets.token_urlsafe(32)


def _invite_expiry():
    return timezone.now() + timedelta(days=7)


class TeamInvite(models.Model):
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.REP)
    token = models.CharField(max_length=64, unique=True, default=_invite_token)
    invited_by = models.ForeignKey(
        CRMUser, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField(default=_invite_expiry)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_valid(self):
        return not self.is_used and self.expires_at > timezone.now()


class PasswordResetCode(models.Model):
    """One-time code emailed to a user who forgot their password. Only a keyed hash of the code is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_reset_codes")
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"])]


class Branch(models.Model):
    name = models.CharField(max_length=120, unique=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Doctor(models.Model):
    name = models.CharField(max_length=120)
    speciality = models.CharField(max_length=120, blank=True)
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="doctors"
    )
    calendar_id = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class LeadQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)


class Lead(models.Model):
    # identity
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True, help_text="E.164")
    country = models.CharField(max_length=100, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    language = models.CharField(max_length=30, blank=True)

    # pipeline
    status = models.CharField(
        max_length=30, choices=LeadStatus.choices, default=LeadStatus.NEW, db_index=True
    )
    source = models.CharField(
        max_length=20, choices=LeadSource.choices, default=LeadSource.MANUAL, db_index=True
    )
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    assigned_to = models.ForeignKey(
        CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads"
    )
    tags = models.JSONField(default=list, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)

    # clinical / interest profile
    treatment_interests = models.JSONField(default=list, blank=True)
    chief_complaint = models.TextField(blank=True)
    urgency = models.CharField(max_length=20, blank=True)
    budget_band = models.CharField(max_length=50, blank=True)
    insurance = models.CharField(max_length=120, blank=True)
    preferred_doctor = models.ForeignKey(
        Doctor, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    preferred_branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    preferred_days = models.JSONField(default=list, blank=True)
    preferred_time = models.CharField(max_length=50, blank=True)
    preferred_channel = models.CharField(max_length=20, blank=True)

    # international
    is_international = models.BooleanField(default=False)
    travel_from = models.DateField(null=True, blank=True)
    travel_to = models.DateField(null=True, blank=True)
    visa_support_needed = models.BooleanField(default=False)
    companions = models.PositiveSmallIntegerField(default=0)
    facilitator = models.CharField(max_length=200, blank=True)

    # quote / lifecycle
    quote_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    quote_valid_until = models.DateField(null=True, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)

    # engagement
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    next_followup_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(default=0, db_index=True)
    score_breakdown = models.JSONField(default=list, blank=True)
    scored_at = models.DateTimeField(null=True, blank=True)
    converted_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Revenue booked when converted (defaults to the quote). Feeds source ROI.",
    )

    # attribution
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=200, blank=True)
    utm_term = models.CharField(max_length=200, blank=True)
    utm_content = models.CharField(max_length=200, blank=True)
    gclid = models.CharField(max_length=255, blank=True)
    fbclid = models.CharField(max_length=255, blank=True)
    landing_url = models.URLField(max_length=1000, blank=True)
    referrer = models.URLField(max_length=1000, blank=True)

    # consent
    consent_marketing = models.BooleanField(default=False)
    consent_recording = models.BooleanField(default=False)
    whatsapp_opt_out = models.BooleanField(default=False)
    sms_opt_out = models.BooleanField(default=False)
    email_opt_out = models.BooleanField(default=False, help_text="Unsubscribed / complained: no automated or campaign email")
    email_invalid = models.BooleanField(default=False, help_text="Address hard-bounced: do not email")
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = LeadQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])


class StatusHistory(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]


class ContactMethod(models.TextChoices):
    CALL = "call", "Call"
    WHATSAPP = "whatsapp", "WhatsApp"
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"
    VISIT = "visit", "Visit"


class ContactOutcome(models.TextChoices):
    CONNECTED = "connected", "Connected"
    NO_ANSWER = "no_answer", "No Answer"
    BUSY = "busy", "Busy"
    VOICEMAIL = "voicemail", "Voicemail"
    WRONG_NUMBER = "wrong_number", "Wrong Number"
    CALLBACK = "callback_requested", "Callback Requested"
    INTERESTED = "interested", "Interested"
    NOT_INTERESTED = "not_interested", "Not Interested"


class ContactLog(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="contacts")
    logged_by = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="+")
    method = models.CharField(max_length=20, choices=ContactMethod.choices)
    direction = models.CharField(
        max_length=10, choices=[("outbound", "Outbound"), ("inbound", "Inbound")], default="outbound"
    )
    outcome = models.CharField(max_length=30, choices=ContactOutcome.choices, blank=True)
    duration_sec = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    call_session = models.ForeignKey(
        "CallSession", null=True, blank=True, on_delete=models.SET_NULL, related_name="contact_logs"
    )
    contacted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-contacted_at"]


class Note(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Task(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="tasks")
    assigned_to = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="tasks")
    title = models.CharField(max_length=255)
    due_at = models.DateTimeField()
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    is_done = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["is_done", "due_at"]


class AppointmentStatus(models.TextChoices):
    BOOKED = "booked", "Booked"
    CONFIRMED = "confirmed", "Confirmed"
    ATTENDED = "attended", "Attended"
    NO_SHOW = "no_show", "No Show"
    CANCELLED = "cancelled", "Cancelled"


class Appointment(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="appointments")
    doctor = models.ForeignKey(Doctor, null=True, blank=True, on_delete=models.SET_NULL, related_name="appointments")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(
        max_length=20,
        choices=[("consult", "Consultation"), ("treatment", "Treatment"), ("follow_up", "Follow-up")],
        default="consult",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=AppointmentStatus.choices, default=AppointmentStatus.BOOKED)
    notes = models.TextField(blank=True)
    gcal_event_id = models.CharField(max_length=255, blank=True)
    gcal_calendar_id = models.CharField(max_length=255, blank=True)
    gcal_sync_error = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_at"]


class NotificationRecipient(models.Model):
    channel = models.CharField(max_length=10, choices=[("email", "Email"), ("whatsapp", "WhatsApp")])
    value = models.CharField(max_length=200)
    label = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("channel", "value")


class CallSession(models.Model):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        RINGING = "ringing", "Ringing"
        IN_PROGRESS = "in-progress", "In progress"
        COMPLETED = "completed", "Completed"
        BUSY = "busy", "Busy"
        NO_ANSWER = "no-answer", "No answer"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="calls")
    crm_user = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="calls")
    call_sid = models.CharField(max_length=64, blank=True, db_index=True, help_text="Twilio parent (browser) leg")
    child_sid = models.CharField(max_length=64, blank=True)
    to_number = models.CharField(max_length=20)
    from_number = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_sec = models.PositiveIntegerField(default=0)
    recording_sid = models.CharField(max_length=64, blank=True)
    recording_url = models.URLField(max_length=500, blank=True)
    logged = models.BooleanField(default=False, help_text="A ContactLog was created from this call")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class MarketingSpend(models.Model):
    """Ad / marketing spend per day, source and (optionally) campaign. Feeds cost-per-lead and ROI."""

    date = models.DateField(db_index=True)
    source = models.CharField(max_length=20, choices=LeadSource.choices)
    campaign = models.CharField(max_length=200, blank=True, help_text="Match the utm_campaign used on landing pages")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]


class TreatmentCatalog(models.Model):
    """Price list: pick items when building a quote. Prices are defaults the rep can override per quote."""

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=30, choices=Treatment.choices, blank=True)
    description = models.CharField(max_length=300, blank=True)
    default_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]
        verbose_name_plural = "treatment catalog"

    def __str__(self):
        return self.name


class QuoteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class Currency(models.TextChoices):
    INR = "INR", "INR (₹)"
    USD = "USD", "USD ($)"
    GBP = "GBP", "GBP (£)"
    EUR = "EUR", "EUR (€)"
    AED = "AED", "AED"


class QuoteSequence(models.Model):
    """One row per year; locked while allocating the next quote number."""

    year = models.PositiveSmallIntegerField(primary_key=True)
    last = models.PositiveIntegerField(default=0)


class Quote(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="quotes")
    number = models.CharField(max_length=20, unique=True, editable=False)
    status = models.CharField(max_length=10, choices=QuoteStatus.choices, default=QuoteStatus.DRAFT, db_index=True)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.INR)
    valid_until = models.DateField()
    discount_type = models.CharField(max_length=7, choices=[("percent", "Percent"), ("amount", "Amount")], default="percent")
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    notes = models.TextField(blank=True, help_text="Shown to the patient")
    terms = models.TextField(blank=True)
    # stored so reports/lead sync never recompute from items; set by pricing.apply()
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(CRMUser, null=True, on_delete=models.SET_NULL, related_name="+")
    sent_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class QuoteItem(models.Model):
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=300)
    area = models.CharField(max_length=100, blank=True, help_text="Tooth / region, e.g. UR6 or Upper arch")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["position", "id"]


# ---------------------------------------------------------------- email (two-way inbox)
def _thread_token():
    return secrets.token_hex(8)  # 16 hex chars; used in the reply-to address t-<token>@<reply domain>


class EmailThread(models.Model):
    """One conversation with a lead. lead=None means an inbound email we could not match (shown in the Unmatched inbox)."""

    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.CASCADE, related_name="email_threads")
    subject = models.CharField(max_length=300, blank=True)
    token = models.CharField(max_length=16, unique=True, default=_thread_token)
    needs_reply = models.BooleanField(default=False, db_index=True)
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_message_at"]


class EmailMessage(models.Model):
    class Direction(models.TextChoices):
        OUT = "out", "Sent"
        IN = "in", "Received"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELAYED = "delayed", "Delayed"
        DELIVERED = "delivered", "Delivered"
        BOUNCED = "bounced", "Bounced"
        COMPLAINED = "complained", "Marked as spam"
        FAILED = "failed", "Failed"
        RECEIVED = "received", "Received"

    thread = models.ForeignKey(EmailThread, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    from_address = models.CharField(max_length=320, blank=True)
    to_address = models.CharField(max_length=320, blank=True)
    subject = models.CharField(max_length=300, blank=True)
    body_text = models.TextField(blank=True, help_text="What is shown: for inbound mail the reply without the quoted history")
    body_full = models.TextField(blank=True, help_text="Inbound only: full plain text including quoted history")
    body_html = models.TextField(blank=True, help_text="Outbound only: the HTML that was sent. Inbound HTML is never stored or rendered.")
    rfc_message_id = models.CharField(max_length=300, blank=True, db_index=True)
    in_reply_to = models.CharField(max_length=300, blank=True)
    resend_id = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=500, blank=True)
    sent_by = models.ForeignKey(CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    enrollment = models.ForeignKey("SequenceEnrollment", null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    campaign = models.ForeignKey("EmailCampaign", null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    step = models.ForeignKey("EmailSequenceStep", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    attachments = models.JSONField(default=list, blank=True, help_text="Inbound attachment metadata (name, type); files are not downloaded")
    delivered_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["resend_id"], condition=~models.Q(resend_id=""), name="uniq_email_resend_id"),
            # a sequence step can only ever be sent once per enrolment, even if two workers race
            models.UniqueConstraint(fields=["enrollment", "step"], condition=models.Q(enrollment__isnull=False, step__isnull=False),
                                    name="uniq_email_enrollment_step"),
        ]


class EmailTemplate(models.Model):
    """Reusable subject/body with merge fields such as {{first_name}} (see crm/emailing.py MERGE_FIELDS)."""

    name = models.CharField(max_length=120, unique=True)
    subject = models.CharField(max_length=300)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]


class WebhookEvent(models.Model):
    """Processed provider webhook ids, so a retried delivery is never applied twice."""

    event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=60, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)


# ---------------------------------------------------------------- automated email sequences
class EmailSequence(models.Model):
    """A timed series of emails (e.g. Welcome: now, +2 days, +5 days). Stops by itself when the lead replies, books, etc."""

    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True, help_text="Inactive sequences send nothing and enrol nobody")
    auto_enroll = models.BooleanField(default=False, help_text="Enrol new leads automatically")
    auto_enroll_sources = models.JSONField(default=list, blank=True, help_text="LeadSource values that trigger auto-enrolment")
    run_while_status = models.JSONField(default=list, blank=True, help_text="Keeps running only while the lead is in one of these statuses")
    from_user = models.ForeignKey(CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                  help_text="Sender when the lead has no assigned rep (blank = the default address)")
    created_by = models.ForeignKey(CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]


class EmailSequenceStep(models.Model):
    sequence = models.ForeignKey(EmailSequence, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveSmallIntegerField()
    delay_hours = models.PositiveIntegerField(default=0, help_text="Wait this long after the previous step (or after enrolment for step 1)")
    template = models.ForeignKey(EmailTemplate, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["sequence", "position"], name="uniq_sequence_step_position")]


class SequenceEnrollment(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        STOPPED = "stopped", "Stopped"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="enrollments")
    sequence = models.ForeignKey(EmailSequence, on_delete=models.CASCADE, related_name="enrollments")
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE, db_index=True)
    stop_reason = models.CharField(max_length=30, blank=True)
    current_step = models.PositiveSmallIntegerField(default=0, help_text="Index of the NEXT step to send")
    next_send_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    thread = models.ForeignKey(EmailThread, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    enrolled_by = models.ForeignKey(CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    enrolled_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-enrolled_at"]
        constraints = [
            # one live enrolment per lead per sequence (a finished one can be followed by a fresh enrolment)
            models.UniqueConstraint(fields=["lead", "sequence"], condition=models.Q(state="active"), name="uniq_active_enrollment"),
        ]


# ---------------------------------------------------------------- one-off campaigns (broadcasts)
class EmailCampaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        SENDING = "sending", "Sending"
        PAUSED = "paused", "Paused"
        SENT = "sent", "Sent"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=150)
    subject = models.CharField(max_length=300)
    body = models.TextField(help_text="Plain text with merge fields; a copy of the template taken when the campaign is created")
    template = models.ForeignKey(EmailTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    audience_filters = models.JSONField(default=dict, blank=True, help_text="Same filters as the Leads page")
    skip_recent_days = models.PositiveSmallIntegerField(default=3, help_text="Skip leads who got an automated/campaign email in the last N days (0 = off)")
    skip_open_conversations = models.BooleanField(default=True, help_text="Skip leads who replied and are waiting for a person")
    from_user = models.ForeignKey(CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="Sender (default: whoever created it)")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(CRMUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CampaignRecipient(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name="recipients")
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="campaign_recipients")
    state = models.CharField(max_length=8, choices=State.choices, default=State.PENDING, db_index=True)
    skip_reason = models.CharField(max_length=40, blank=True)
    message = models.ForeignKey(EmailMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campaign", "lead"], name="uniq_campaign_recipient")]
