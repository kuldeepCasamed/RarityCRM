from django.contrib import admin

from . import models

for m in (models.CRMUser, models.TeamInvite, models.Branch, models.Doctor, models.StatusHistory,
          models.ContactLog, models.Note, models.Task, models.Appointment, models.NotificationRecipient, models.CallSession, models.MarketingSpend, models.TreatmentCatalog, models.Quote, models.QuoteItem, models.EmailThread, models.EmailMessage, models.EmailTemplate, models.WebhookEvent, models.EmailSequence, models.EmailSequenceStep, models.SequenceEnrollment, models.EmailCampaign, models.CampaignRecipient):
    admin.site.register(m)


@admin.register(models.Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "status", "source", "assigned_to", "created_at")
    list_filter = ("status", "source", "is_international")
    search_fields = ("name", "email", "phone")


@admin.register(models.ContactClick)
class ContactClickAdmin(admin.ModelAdmin):
    list_display = ("created_at", "channel", "source", "utm_campaign", "utm_term", "utm_content", "page_path", "button_location")
    list_filter = ("channel", "source", "utm_campaign", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
