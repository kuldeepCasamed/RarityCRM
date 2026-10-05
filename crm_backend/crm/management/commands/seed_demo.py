"""Demo data for trying the CRM. Everything is tagged so `--remove` deletes exactly what this created.

Safe fake contacts: phones are +91 0000000NNN (no valid Indian number starts with 0) and emails are @demo.example
(a reserved domain), so calling/emailing a demo lead can never reach a real person."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm import quotes, services
from crm.models import (Appointment, ContactLog, CRMUser, Lead, MarketingSpend, Note, Quote, Task, TreatmentCatalog)
from crm.scoring import rescore

TAG = "demo"
MARK = "[demo]"

CATALOG = [("Dental implant (single)", "implants", 45000), ("Zirconia crown", "implants", 18500),
           ("Invisalign - full treatment", "invisalign", 285000), ("Smile design (10 veneers)", "smile_design", 250000),
           ("Root canal treatment", "root_canal", 9000), ("Teeth whitening", "whitening", 12000),
           ("Consultation & CBCT scan", "", 3500)]

# name, phone suffix, email, country, source, status, treatments, days_ago, extras
LEADS = [
    ("Priya Nair", 1, "priya", "India", "google_ads", "converted", ["invisalign"], 24, dict(utm_campaign="invisalign-q4", utm_source="google", urgency="soon", budget_band="2.5-3 lakh")),
    ("Rohan Mehta", 2, "rohan", "India", "google_ads", "treatment_plan_sent", ["implants"], 12, dict(utm_campaign="implants-q4", utm_source="google", urgency="urgent", insurance="Star Health")),
    ("Sunita Rao", 3, "sunita", "India", "website", "consult_booked", ["smile_design"], 6, dict(utm_campaign="smile-design", chief_complaint="Wants a smile makeover before her daughter's wedding in January.")),
    ("Arjun Kapoor", 4, "arjun", "India", "meta_ads", "contacted", ["whitening"], 4, dict(utm_source="facebook", utm_campaign="whitening-promo")),
    ("Meera Iyer", 5, "meera", "India", "referral", "negotiating", ["implants", "full_mouth"], 15, dict(urgency="soon", budget_band="5-6 lakh", chief_complaint="Multiple missing teeth, referred by an existing patient.")),
    ("Vikram Singh", 6, "vikram", "India", "phone", "new", ["root_canal"], 0, dict(urgency="urgent", chief_complaint="Severe tooth pain since 2 days.")),
    ("Neha Gupta", 7, "neha", "India", "website", "lost", ["braces"], 20, dict(lost_reason="Chose a clinic closer to home")),
    ("James Whitaker", 8, "james", "United Kingdom", "website", "consult_done", ["implants", "smile_design"], 9, dict(is_international=True, travel_from=timezone.localdate() + timedelta(days=30), travel_to=timezone.localdate() + timedelta(days=38), companions=1, utm_source="google", utm_campaign="international-implants", timezone="Europe/London", visa_support_needed=False)),
    ("Fatima Al-Mansoori", 9, "fatima", "United Arab Emirates", "partner", "new", ["invisalign"], 1, dict(is_international=True, facilitator="MedTravel Dubai", timezone="Asia/Dubai", visa_support_needed=True)),
    ("Karan Malhotra", 10, "karan", "India", "walk_in", "contacted", ["single_day"], 3, dict()),
]


class Command(BaseCommand):
    help = "Create demo leads, a price list, ad spend and quotes (tagged 'demo'). Use --remove to delete them."

    def add_arguments(self, parser):
        parser.add_argument("--remove", action="store_true", help="Delete everything this command created")

    def handle(self, *args, **o):
        if o["remove"]:
            return self.remove()
        if Lead.objects.filter(tags__contains=[TAG]).exists() if self.is_pg() else any(TAG in (l.tags or []) for l in Lead.objects.all()):
            self.stdout.write("Demo data already exists. Run with --remove first to recreate it.")
            return
        self.create()

    @staticmethod
    def is_pg():
        from django.db import connection
        return connection.vendor == "postgresql"

    def remove(self):
        with transaction.atomic():
            ids = [l.id for l in Lead.objects.all() if TAG in (l.tags or [])]
            n = Lead.objects.filter(id__in=ids).delete()[0]
            s = MarketingSpend.objects.filter(notes__startswith=MARK).delete()[0]
            c = TreatmentCatalog.objects.filter(description__startswith=MARK).delete()[0]
        self.stdout.write(self.style.SUCCESS(f"Removed {len(ids)} leads (+ related rows: {n}), {s} spend rows, {c} price-list items."))

    @transaction.atomic
    def create(self):
        now = timezone.now()
        admin = CRMUser.objects.filter(role="admin", is_active=True).order_by("id").first()

        for name, cat, price in CATALOG:
            TreatmentCatalog.objects.create(name=name, category=cat, default_price=price, description=f"{MARK} sample price")

        made = {}
        for name, n, email, country, source, status, treatments, days_ago, extra in LEADS:
            lead, _ = services.create_lead(
                auto_assign=False, name=name, phone=f"+91000000{n:04d}", email=f"{email}@demo.example", country=country,
                source=source, treatment_interests=treatments, tags=[TAG], **{"is_international": country != "India", **extra})
            created = now - timedelta(days=days_ago, hours=2)
            Lead.objects.filter(pk=lead.pk).update(created_at=created, assigned_to=admin if n % 2 else None)
            made[name] = (Lead.objects.get(pk=lead.pk), created, status)

        def log(lead, created, method, outcome, hours_after, notes="", direction="outbound"):
            ContactLog.objects.create(lead=lead, logged_by=admin, method=method, direction=direction, outcome=outcome,
                                      notes=notes, duration_sec=240 if method == "call" and outcome == "connected" else 0,
                                      contacted_at=created + timedelta(hours=hours_after))

        # --- conversations, notes, tasks, appointments per lead ---
        L = lambda n: made[n][0]  # noqa: E731
        C = lambda n: made[n][1]  # noqa: E731
        log(L("Priya Nair"), C("Priya Nair"), "call", "connected", 1, "Interested in Invisalign, wants to see before/after cases.")
        log(L("Priya Nair"), C("Priya Nair"), "whatsapp", "interested", 26, "Shared case photos.")
        log(L("Rohan Mehta"), C("Rohan Mehta"), "call", "connected", 1, "Missing lower molar, wants an implant. Asked about recovery time.")
        log(L("Rohan Mehta"), C("Rohan Mehta"), "call", "no_answer", 50)
        log(L("Sunita Rao"), C("Sunita Rao"), "call", "connected", 3, "Wants smile makeover before a wedding in January.")
        log(L("Arjun Kapoor"), C("Arjun Kapoor"), "call", "no_answer", 2)
        log(L("Arjun Kapoor"), C("Arjun Kapoor"), "call", "voicemail", 26)
        log(L("Meera Iyer"), C("Meera Iyer"), "call", "connected", 1, "Referred by Mrs. Sharma. Needs full-mouth assessment.")
        log(L("Meera Iyer"), C("Meera Iyer"), "visit", "connected", 72, "Came for consult, CBCT done.")
        log(L("Meera Iyer"), C("Meera Iyer"), "call", "callback_requested", 200, "Comparing with another clinic, wants a discount.")
        log(L("Neha Gupta"), C("Neha Gupta"), "call", "connected", 2)
        log(L("Neha Gupta"), C("Neha Gupta"), "call", "not_interested", 120, "Going to a clinic near her home.")
        log(L("James Whitaker"), C("James Whitaker"), "email", "", 4, "Sent international patient guide.")
        log(L("James Whitaker"), C("James Whitaker"), "call", "connected", 30, "Video consult done, plans to travel in a month.")
        log(L("Karan Malhotra"), C("Karan Malhotra"), "visit", "connected", 1, "Walked in asking about same-day crowns.", "inbound")
        Note.objects.create(lead=L("Meera Iyer"), author=admin, body="Budget is ~5.5L. Decision maker is her husband - include him in the next call.")
        Note.objects.create(lead=L("James Whitaker"), author=admin, body="Prefers calls 6-8pm IST (11:30am-1:30pm UK).")

        def task(lead, title, due_hours, priority="medium"):
            Task.objects.create(lead=lead, assigned_to=admin, title=title, due_at=now + timedelta(hours=due_hours), priority=priority)

        task(L("Vikram Singh"), "Call back - severe tooth pain, offer same-day slot", 1, "high")
        task(L("Arjun Kapoor"), "Try again - 2 missed calls, send WhatsApp", 3)
        task(L("Rohan Mehta"), "Follow up on implant quote", 20)
        task(L("Meera Iyer"), "Send revised offer to Meera and her husband", -5, "high")  # overdue
        task(L("Fatima Al-Mansoori"), "Confirm visa-support letter requirements", 30)

        def appt(lead, hours, status="booked", kind="consult"):
            Appointment.objects.create(lead=lead, kind=kind, start_at=now + timedelta(hours=hours), end_at=now + timedelta(hours=hours, minutes=30),
                                       status=status, created_by=admin)

        appt(L("Sunita Rao"), 28)
        appt(L("James Whitaker"), 24 * 30, "booked", "treatment")
        appt(L("Meera Iyer"), -24 * 7, "attended")
        appt(L("Karan Malhotra"), 52)

        # --- quotes (these move leads through the pipeline via the normal workflow) ---
        def quote(name, items, **kw):
            q = quotes.create_quote(L(name), {"items": items, **kw}, admin)
            return q
        q1 = quote("Priya Nair", [{"description": "Invisalign - full treatment", "area": "Upper & lower arch", "quantity": 1, "unit_price": 285000},
                                  {"description": "Consultation & CBCT scan", "quantity": 1, "unit_price": 3500}], discount_type="percent", discount_value=5, notes="Includes 12 months of follow-up visits.")
        quotes.set_status(q1, "sent", admin)
        quotes.set_status(Quote.objects.get(pk=q1.pk), "accepted", admin)
        q2 = quote("Rohan Mehta", [{"description": "Dental implant (single)", "area": "LR6", "quantity": 1, "unit_price": 45000},
                                   {"description": "Zirconia crown", "area": "LR6", "quantity": 1, "unit_price": 18500}], notes="Implant placement + crown, 3-4 month process.")
        quotes.set_status(q2, "sent", admin)
        quote("Meera Iyer", [{"description": "Dental implant (single)", "area": "Upper arch", "quantity": 4, "unit_price": 45000},
                              {"description": "Zirconia crown", "area": "Upper arch", "quantity": 4, "unit_price": 18500}], discount_type="amount", discount_value=15000)  # left as a draft

        # --- final statuses & backdated timestamps (history rows are created by the workflow) ---
        for name, (lead, created, status) in made.items():
            lead = Lead.objects.get(pk=lead.pk)
            if lead.status != status:
                services.change_status(lead, status, admin)
            if status == "converted":
                Lead.objects.filter(pk=lead.pk).update(converted_at=created + timedelta(days=5))
            if status in ("contacted", "consult_booked", "negotiating", "consult_done"):
                Lead.objects.filter(pk=lead.pk, last_contacted_at__isnull=True).update(last_contacted_at=created + timedelta(hours=2))
        Lead.objects.filter(pk=L("Priya Nair").pk).update(converted_value=quotes_total(q1))

        # --- ad spend for the ROI report ---
        today = timezone.localdate()
        for src, camp, amt in [("google_ads", "invisalign-q4", 18000), ("google_ads", "implants-q4", 24000), ("google_ads", "international-implants", 15000),
                               ("meta_ads", "whitening-promo", 6000), ("google_ads", "smile-design", 9000)]:
            MarketingSpend.objects.create(date=today - timedelta(days=10), source=src, campaign=camp, amount=amt, notes=f"{MARK} sample spend", created_by=admin)

        for lead, *_ in made.values():
            rescore(Lead.objects.get(pk=lead.pk))
        self.stdout.write(self.style.SUCCESS(f"Created {len(made)} demo leads, {len(CATALOG)} price-list items, 3 quotes, 5 spend rows. Remove with: manage.py seed_demo --remove"))


def quotes_total(q):
    return Quote.objects.get(pk=q.pk).total
