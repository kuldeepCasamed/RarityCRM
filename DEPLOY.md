# Deploying Rarity CRM

```
 Browser ──► Netlify (crm_frontend, Next.js)  ──server-to-server──►  Render (crm_backend: Django API + worker)  ──► Neon Postgres
 raritydental.com (website) ───── POST /api/crm/interest/ + X-Rarity-Key ─────────────┘
 Twilio ───── webhooks ─────────────────────────────────────────────────────────────────┘
```
One git repo (this folder). Render builds only `crm_backend/`, Netlify builds only `crm_frontend/`.
**Local development is unchanged** - see "Run locally" at the bottom.

## 0. Before you start
- [ ] **Rotate the Neon database password** (it was shared in chat) and copy the new *pooled* connection string.
- [ ] Push this repo to GitHub (`main`). Real `.env` files are git-ignored; never commit them.

## 1. Backend on Render (free plan)
1. Render dashboard -> **New -> Blueprint** -> pick this repo. Render reads `render.yaml` and proposes one web service,
   `rarity-crm-backend` (free plan, Ohio region, root dir `crm_backend`).
   *Without the blueprint:* New -> Web Service, Root Directory `crm_backend`, Build `bash build.sh`, Start `bash start.sh`,
   Health Check Path `/healthz/`, Instance type Free.
2. Fill the prompted secrets (they are `sync: false`, so they are never stored in git):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | Neon **pooled** string, e.g. `postgresql://user:pass@ep-xxx-pooler.../neondb?sslmode=require` |
   | `CRM_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | your first login (username defaults to `admin`; password **12+ chars**). See below |
   | `CRM_INTAKE_API_KEY` | a long random string (`openssl rand -hex 32`). Put the **same** value in the website env (step 3) |
   | `CRM_BASE_URL` | the Netlify URL from step 2, e.g. `https://rarity-crm.netlify.app` (used in email/invite links). Can be filled after step 2 |
   | `RESEND_API_KEY`, `CRM_MANAGER_EMAIL` | email sending + who gets new-lead alerts |
   | `CLINIC_ADDRESS/PHONE/EMAIL/TAX_ID` | letterhead on quote PDFs |
   | `TWILIO_*` | leave empty until calling is set up |

   `SECRET_KEY` is generated for you. `PUBLIC_BASE_URL` and `ALLOWED_HOSTS` are filled in automatically from Render's own
   URL. **Custom domain?** set `ALLOWED_HOSTS=crm-api.yourdomain.com`, `CSRF_TRUSTED_ORIGINS=https://crm-api.yourdomain.com`
   and `PUBLIC_BASE_URL=https://crm-api.yourdomain.com`.
3. Deploy. **`build.sh`** installs dependencies, collects static files, runs the database migrations, registers the scheduled
   jobs and creates your first admin. A failing migration fails the build, so the previous version keeps running.
   **`start.sh`** then runs the API **and** the background worker in the one service.
4. Check `https://<service>.onrender.com/healthz/?db=1` -> `{"status": "ok"}` (`?db=1` also tests the database).
5. **First login.** The free plan has no Shell, so the first admin comes from the `CRM_BOOTSTRAP_ADMIN_*` variables.
   Log in, change your password (Settings -> Profile), then **delete `CRM_BOOTSTRAP_ADMIN_PASSWORD`** from Render.
   It only ever acts while no admin exists, and never resets an existing password. Other staff: Settings -> Team -> Invite.

## 2. Frontend on Netlify
1. Netlify -> **Add new site -> Import from Git** -> this repo. `netlify.toml` sets base dir `crm_frontend`,
   build `npm run build`, Node 20.
2. **Site settings -> Environment variables**:
   `CRM_API_URL = https://<render-service>.onrender.com/api/crm`   (server-side only; note the `/api/crm`, no trailing slash)
3. Deploy, then put the resulting Netlify URL into Render's `CRM_BASE_URL` (step 1.2).
4. Open the site and log in. **First things to test**: login, the dashboard, creating a lead, downloading a quote PDF
   (confirms the API proxy and binary responses work through Netlify).
   > Not verified by me: this app uses Next.js 16's `proxy.ts` and route-handler API proxying. Netlify's Next.js runtime
   > should support both, but if login or API calls misbehave there, the same repo deploys unchanged on Vercel.

## 3. Connect the website (Rarity-Dental)
Set in the website's environment, then redeploy it:
```
CRM_INTAKE_URL=https://<render-service>.onrender.com/api/crm/interest/
CRM_INTAKE_API_KEY=<same value as the backend's CRM_INTAKE_API_KEY>
```
Form submissions then create CRM leads. The CRM call happens **after** the visitor gets their response and **after** the staff
email is sent, waits up to 60 s per attempt and retries (3 attempts), so a sleeping backend doesn't slow the form or lose the lead -
as long as the website's host lets that function run that long (see the table below). The staff email always contains the lead.

## 4. Twilio (when ready)
TwiML App -> Voice Request URL: `https://<render-service>.onrender.com/api/crm/calls/voice-twiml/` (POST).
Set the `TWILIO_*` variables on Render and redeploy. See README -> "Twilio calling".

## What "free plan, it sleeps" really means
Render's free web service stops after ~15 minutes without requests and takes ~50 s to wake on the next one.

| While the backend is asleep... | What happens |
|---|---|
| Someone opens the CRM | The first request answers "Server is waking up…" (the frontend retries by itself for ~1 min). Then normal. |
| A website visitor submits the form | Staff email goes out immediately. The CRM lead is created after the wake-up (retries up to ~3 min), **provided the website's host keeps the function alive that long** (Vercel hobby may cut it earlier - then that lead is only in the email). |
| **Background jobs** (daily task digest, stale-lead digest, appointment reminders, nightly score refresh, quote expiry) | **Do not run.** The worker sleeps too, and missed runs are skipped, not replayed. |
| New-lead / assignment emails | Sent once the service is awake (a form submission wakes it). |
| A Twilio call | Click-to-call first fetches a token, which wakes the backend, so calls normally work. Twilio's webhook timeout is short, so the very first call after a long sleep can fail - just retry. |

**Recommended fix (free): keep it awake.** Create a free monitor at UptimeRobot (or cron-job.org) that requests
`https://<render-service>.onrender.com/healthz/` every 5 minutes. A single free service running 24/7 fits within Render's free
instance-hours (about 750/month - check Render's current terms), and then none of the rows above apply. It's your call whether to do this.
Trade-off: the always-on worker polls Neon, which keeps Neon's compute awake too; on Neon's free plan check the monthly compute-hour limit,
or raise `Q_POLL`. Without the monitor, jobs only run when someone happens to wake the service.

## Other limits and notes
- **CSV import via Netlify:** Netlify cuts functions at ~10 s, so import in batches of about 200 rows or fewer (leads and ad spend).
- **Memory:** web + worker measured ~190 MB of the free plan's 512 MB (light load). Heavy PDF/CSV work spikes it; if the instance restarts
  with out-of-memory errors, lower `WEB_CONCURRENCY` to 1.
- **Region:** Render is set to Ohio to sit next to Neon (us-east-2). If you move the database, move the service.
- **Backups:** rely on Neon's point-in-time restore (check your plan's retention); there is no separate backup job.
- **Monitoring:** logs go to Render's log stream. No error tracker (e.g. Sentry) is wired in yet.
- **HSTS** is set to 1 hour. After confirming everything works over HTTPS, set `SECURE_HSTS_SECONDS=31536000`.
- **/admin/** is reachable on the public URL (login required). Use strong passwords.
- **Going always-on later:** change `plan: free` to `plan: starter` in `render.yaml` (or pick it in the dashboard). Nothing else changes.

## Run locally (unchanged)
```bash
# backend  (uses crm_backend/.env; DEBUG=True there, so none of the production hardening applies)
cd crm_backend && .venv/bin/python manage.py runserver 8010
# frontend (uses crm_frontend/.env.local -> CRM_API_URL=http://localhost:8010/api/crm)
cd crm_frontend && npm run dev -- -p 3010
# tests
cd crm_backend && DATABASE_URL=sqlite:////tmp/crm-test.sqlite3 .venv/bin/python manage.py test crm
```
To rehearse production locally: `DEBUG=False SECRET_KEY=<long random> DATABASE_URL=... PORT=8020 bash start.sh`.
