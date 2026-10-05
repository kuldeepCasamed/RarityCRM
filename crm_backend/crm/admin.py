from django.contrib import admin

from . import models

for m in (models.CRMUser, models.TeamInvite, models.Branch, models.Doctor, models.StatusHistory,
          models.ContactLog, models.Note, models.Task, models.Appointment, models.NotificationRecipient, models.CallSession, models.MarketingSpend, models.TreatmentCatalog, models.Quote, models.QuoteItem):
    admin.site.register(m)


@admin.register(models.Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "status", "source", "assigned_to", "created_at")
    list_filter = ("status", "source", "is_international")
    search_fields = ("name", "email", "phone")
