# Rarity CRM

`crm_backend/` Django + DRF + Postgres · `crm_frontend/` Next.js 16. No Docker. Plan: `../RARITY_CRM_PLAN.md`.

## Backend
```bash
cd crm_backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set DATABASE_URL (Postgres), CRM_INTAKE_API_KEY, RESEND_API_KEY
createdb rarity_crm             # Postgres must be installed locally
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser   # then in /admin create a CRMUser (role=admin) for it
.venv/bin/python manage.py runserver 8010
.venv/bin/python manage.py setup_schedules   # once: digests/reminders
.venv/bin/python manage.py qcluster          # background worker (alerts + schedules)
.venv/bin/python manage.py test crm
```
Without `DATABASE_URL` it falls back to SQLite (dev/tests only).

## Frontend
```bash
cd crm_frontend && npm install
cp .env.example .env.local      # CRM_API_URL=http://localhost:8010/api/crm
npm run dev -- -p 3010
```
The browser only talks to Next.js (`/api/crm/*` proxy); the token lives in an httpOnly cookie.

## Website hookup (Rarity-Dental)
Set in the site's env: `CRM_INTAKE_URL=https://<crm-api>/api/crm/interest/` and `CRM_INTAKE_API_KEY=<same value as backend>`.
Unset = no CRM push; the staff email still goes out either way.

## Neon (Postgres) notes
- `DATABASE_URL` in `crm_backend/.env` (gitignored) points at Neon's **pooled** host. Server-side cursors are disabled for this reason.
- Tables are already migrated. For `manage.py test`, use the **direct** host (remove `-pooler`) or run on SQLite
  (unset `DATABASE_URL`): each Neon round trip is ~250 ms from India, so the full suite is slow remotely.
- Create your first admin: `manage.py createsuperuser`, then `manage.py shell` ->
  `CRMUser.objects.create(user=User.objects.get(username="..."), role="admin")`.

## Google Calendar sync
Appointments push to the doctor's calendar (create / reschedule / cancel / delete). Status shows on `/appointments`.
1. Google Cloud: create a project, enable **Google Calendar API**, create a **service account**, download its JSON key.
2. In Google Calendar, share each doctor's calendar with the service account email ("Make changes to events").
3. Set the doctor's *Google Calendar ID* in Settings -> Clinic (or `GOOGLE_DEFAULT_CALENDAR_ID` as a fallback).
4. `.env`: `GOOGLE_SERVICE_ACCOUNT_FILE=/path/key.json` (or `GOOGLE_SERVICE_ACCOUNT_JSON=<raw json>`).
Not configured = sync is skipped silently. Needs the `qcluster` worker running.

## Twilio calling (browser -> lead's phone)
1. Twilio console: buy/verify a voice-capable number (`TWILIO_FROM_NUMBER`). Indian numbers need regulatory
   approval; calling India from a US number works but check Twilio's India caller-ID rules.
2. Create an **API Key** (`TWILIO_API_KEY`/`TWILIO_API_SECRET`) and a **TwiML App** (`TWILIO_TWIML_APP_SID`).
3. TwiML App *Voice request URL* (POST): `https://<PUBLIC_BASE_URL>/api/crm/calls/voice-twiml/`.
4. `.env`: all `TWILIO_*` vars + `PUBLIC_BASE_URL` = the **public https URL of this backend**
   (webhook signatures are validated against it; localhost won't work - use ngrok/cloudflared for local tests).
5. The CRM frontend must be served over **https** (or localhost) for microphone access.
Recording is OFF (`TWILIO_RECORD_CALLS=false`). Turn it on only after deciding the consent/announcement policy
(India/GDPR); recordings are played through the CRM (`/calls/:id/recording/`), never exposed directly.

## Lead scoring
0-100, additive and explained on each lead (Overview tab -> "Lead score"). Bands: **hot >= 60**, warm 30-59, cold < 30.
Points come from profile fit (phone/email, treatment, budget, quote, urgency, travel dates), source quality, engagement
(connected calls, inbound contact, recency, neglect penalties) and pipeline (stage, appointments attended/no-show).
Converted = 100, lost/invalid/not-interested = 0. Recomputed on every lead/contact/appointment change and nightly
(`run_refresh_scores`, needs `qcluster`) so untouched leads decay. Tune weights in `crm_backend/crm/scoring.py`;
after changing them run `manage.py rescore_leads`.

## Source ROI (Analytics page, managers/admins)
- Enter ad spend under Analytics -> Marketing spend (manual or CSV: `date,source,amount,campaign,notes`).
- Revenue = a lead's **Converted value** (Profile -> Quote & consent). It defaults to the quote when a lead is marked converted.
- Report is a cohort view: leads *created* in the range, their conversions/revenue so far, vs spend dated in the range.
- Campaign grouping matches `utm_campaign` (case/whitespace-insensitive) to the spend row's campaign - use the same names in ad URLs and the spend sheet.
- All amounts are INR. A source with no spend shows "-" for cost/ROI (not zero).

## Quotes & PDFs
Lead page -> **Quotes** tab. Line items (treatment, tooth/area, qty, unit price), % or amount discount (applied before tax),
optional tax %, currency (INR/USD/GBP/EUR/AED), validity date (default 30 days), notes and terms. Numbers are `RQ-<year>-<seq>`.
- Price list (default prices) is managed in Settings -> Price list. Reps can override prices per quote.
- Workflow: draft -> sent -> accepted / rejected / expired. Only drafts are editable; use **Revise** to copy a sent quote into a new draft.
- Sending (email or "Mark as sent") moves the lead to *Treatment Plan Sent* and, for INR quotes, sets the lead's quote amount/validity.
  Accepting moves it to *Treatment Booked*. Leads are never moved backwards or out of a closed state.
- **Email to patient** attaches the PDF via Resend (needs `RESEND_API_KEY`) and logs an email contact on the lead.
- Sent quotes past their validity date become *expired* nightly (`run_expire_quotes`, needs `qcluster`).
- PDF letterhead: set `CLINIC_ADDRESS`, `CLINIC_PHONE`, `CLINIC_EMAIL`, `CLINIC_TAX_ID` (GSTIN), `QUOTE_DEFAULT_TERMS` in `.env`.
  The logo and DejaVu font (so the rupee sign renders) are bundled in `crm_backend/crm/assets/`.
