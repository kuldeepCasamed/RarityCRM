# Email campaigns + two-way email inbox — plan

> **Decisions (13 Oct 2026):** backend will move from Render to GCP later (design is host-neutral); the user adds the MX record;
> the sender is the **individual manager/rep's own email** (falls back to the default address when its domain isn't verified in Resend);
> **marketing consent is not required to email existing CRM leads** — unsubscribe, bounce and spam-complaint handling stay mandatory
> regardless (provider rules + Gmail/Yahoo bulk-sender requirements; legal responsibility for consent rests with the clinic).
> **Phase 1 (two-way inbox) is built** — see "Phase 1 status" at the bottom.

Status: **PLAN ONLY, nothing built yet.** Provider stays **Resend** (already used for alerts and quotes).

## 1. Is it possible with Resend?  Yes. What the docs confirm (checked Oct 2026)

| Need | Resend capability | Notes |
|---|---|---|
| Send one email / many | `emails.send`, **batch up to 100 per call** | Batch does **not** support attachments. Custom `headers` supported, so `In-Reply-To`, `References`, `List-Unsubscribe` are possible. `Idempotency-Key` header avoids double sends. |
| Delivery tracking | Webhooks: `email.sent / delivered / delivery_delayed / bounced / failed / suppressed / complained / opened / clicked` | Retries reuse the same `svix-id`, so de-duplicate on it. |
| **Receive replies** | Inbound: add an **MX record** for a domain/subdomain, then webhook **`email.received`** | Payload has `email_id, from, to, received_for, message_id, subject, attachments(metadata)`. **Body is not in the webhook**: fetch with `GET /emails/receiving/{id}` (html, text, headers, attachments). |
| Threaded replies | Send with `In-Reply-To` = received `message_id`, `References`, subject `Re: …` | Docs do **not** confirm that received `headers` include `In-Reply-To`/`References` → the design below does **not depend on it**. |

Things I could not confirm from the docs (must be tested with a real account before promising): Resend's inbound plan limits / pricing, the exact webhook retry schedule while our backend is asleep, and whether inbound headers carry `In-Reply-To`. Plan-level email caps (daily/monthly) also need checking on your Resend plan.

## 2. How replies find the right lead (the key design)

Every outgoing email gets a **unique reply-to address**: `t-<thread-token>@reply.raritydental.com`.
Inbound webhook → `received_for` / `to` contains that token → thread → lead. Fallbacks: sender address == `lead.email`, then `In-Reply-To` if present. Unmatched mail goes to an "Unassigned inbox" screen (never silently dropped).

> **DNS warning:** the MX record must be on a **subdomain** (`reply.raritydental.com`), **never the root domain**. An MX on the root would redirect ALL of the clinic's normal staff email to Resend.

## 3. Data model (new)

- `EmailThread` — lead, subject, token, last_message_at, `needs_reply` (bool), status.
- `EmailMessage` — thread, lead, direction (in/out), from/to, subject, body_html/text, RFC `message_id`, `in_reply_to`, `resend_id`, delivery status (queued/sent/delivered/bounced/complained/failed), `sent_by` (rep or null=automation), campaign/step FK, opened_at, clicked_at, error.
- `EmailTemplate` — name, subject, body (merge fields `{{first_name}}`, `{{clinic_name}}`, `{{treatment}}`, `{{rep_name}}`, `{{booking_link}}`), category (enquiry follow-up / marketing).
- `Campaign` — name, kind (**one-off** | **sequence**), audience filter (same filters as the Leads page), requires_consent flag, schedule, status (draft/scheduled/sending/sent/paused), daily send cap, counters.
- `CampaignStep` — order, delay (days/hours), template, stop-conditions.
- `Enrollment` — lead, campaign, current step, `next_send_at`, state (active/completed/stopped + reason).
- Lead additions: `email_opt_out`, `email_invalid`, `unsubscribed_at`; signed unsubscribe token (no DB lookup needed).

## 4. Automation rules ("status auto-moved")

Moves are **forward-only** and never leave a closed state (same guard as quotes). All rules live in code first; a settings screen to edit them can come later.

| Event | Automatic result |
|---|---|
| Lead replies to an email | Status `new → contacted` (if earlier), thread marked **Needs reply**, task "Reply to <name>" (2 h, high priority) for the assigned rep, rep notified, **all active sequences for that lead stop** (reason: replied), score rises |
| Rep sends a reply | `Needs reply` cleared, `last_contacted_at` updated, contact logged |
| Appointment booked / quote sent / status ≥ consult booked | Sequences stop (reason: progressed) |
| Hard bounce | `email_invalid` set, sequence stops, task "Fix email address" |
| Spam complaint, unsubscribe click, or reply "stop/unsubscribe" | `email_opt_out` set, all marketing stops immediately |
| Link click (booking link) | Score bump; opens are **not** used as signals (Apple Mail privacy makes them unreliable) |

## 5. Flow after implementation

1. **Enquiry arrives** (website form / Smile Studio) → lead created → rule "source=website" enrols them in the **Welcome sequence**.
2. **Minute 0:** email 1 ("Thanks for your enquiry…") from `hello@raritydental.com`, reply-to token address.
3. **Day 2 / Day 5:** follow-ups, sent only if the lead hasn't replied, booked, or progressed.
4. **Lead replies from Gmail** → Resend receives it → webhook → CRM stores it in the lead's **Emails tab**, moves status, creates the task, notifies the rep, stops the sequence.
5. **Rep replies in the CRM** → goes out threaded (`In-Reply-To`/`References`) → appears in the same thread; further replies keep landing there.
6. **Manager runs a campaign** (e.g. "Invisalign offer to past leads"): pick audience with live count (consent-filtered, unsubscribed/bounced excluded), pick template, **send test to self**, schedule. Sends in batches of ≤100 within the daily cap. A report shows delivered, clicked, replied, bounced, unsubscribed, and how many later booked.
7. Everything also appears on the lead's activity timeline and in the lead score.

## 6. Screens

- Lead → **Emails** tab (thread view, composer with templates, "Needs reply" badge).
- **Inbox** page: all threads needing a reply, plus unmatched inbound mail.
- **Campaigns** page: list, builder (audience → template/steps → schedule), report.
- **Templates** page; Settings → email (from-name, reply domain, daily cap, test send).
- Public **unsubscribe** page (frontend) + one-click `List-Unsubscribe` header.

## 7. Compliance & deliverability (decisions needed — not just code)

- **Marketing vs. enquiry follow-up.** Marketing campaigns must go only to leads with `consent_marketing = true`. Today the website contact form has **no consent checkbox**, so most leads have no marketing consent. Replying to someone's own enquiry is a different (lower-risk) category. Have a lawyer confirm what applies under India's DPDP Act and, for EU/UK patients, GDPR/PECR.
- Every marketing email carries an unsubscribe link and `List-Unsubscribe` + `List-Unsubscribe-Post` headers (Gmail/Yahoo bulk-sender rules).
- Add **SPF, DKIM and DMARC** for the sending domain (SPF/DKIM likely exist since Resend already sends from `raritydental.com`); consider a separate marketing subdomain so campaign problems can't hurt the clinic's alert/quote emails.
- **Warm up** volume gradually (daily cap starts low) to protect sender reputation.

## 8. Infrastructure impact

- Sequences and scheduled sends need the background worker **always running**. On Render's free plan the service sleeps, so timed sends are late and the nightly engine misses runs. For this feature use an always-on instance (Render paid) or a reliable keep-alive monitor.
- Webhooks (`email.received`, delivery events) hit `https://raritycrm.onrender.com/api/crm/email/webhook/`, verified with Resend's signing secret (`RESEND_WEBHOOK_SECRET`). If the service is asleep, delivery depends on Resend's retry behaviour (to be verified).
- The worker polls the database, which keeps Neon awake while it runs.

## 9. Phases (each is shippable and testable on its own)

| Phase | Scope | Result you can see |
|---|---|---|
| **1 — Two-way inbox** | Models, send from lead page, templates, delivery webhook, inbound `email.received` → thread, Needs-reply flag, auto status/task/notification, Emails tab, Inbox page | Reply from a real Gmail appears in the CRM; reply back from the CRM threads correctly |
| **2 — Automation** | Sequences/enrolments, stop rules, scheduler with daily cap, unsubscribe + suppression, bounce/complaint handling | New website lead gets the Welcome sequence; it stops when they reply |
| **3 — Campaigns** | One-off broadcasts, audience builder with consent filter, test send, schedule, reports | Manager sends a campaign and reads delivered/clicked/replied numbers |
| **4 — Polish** | Editable rules screen, template editor niceties, per-rep reply signatures, export | — |

Tests per phase: webhook signature + de-duplication, reply→lead matching (token, sender, fallback), stop-rule matrix, forward-only status moves, cap/batching, unsubscribe, plus an end-to-end rehearsal with your own Gmail.

## 10. What I need from you before building

1. **Resend:** your plan (daily/monthly limits) and whether inbound is available on it; create a **webhook** later and give me the signing secret (put it straight into Render as `RESEND_WEBHOOK_SECRET`).
2. **DNS:** ability to add an MX record for **`reply.raritydental.com`** (subdomain only), plus DMARC if missing.
3. **Addresses:** sender (e.g. `hello@raritydental.com`) and from-name.
4. **Consent policy:** confirm the marketing-vs-enquiry split; whether to add a consent checkbox to the website forms.
5. **Always-on backend:** Render paid instance (or accept delayed sends).
6. **Content:** the Welcome sequence copy (I can draft, the clinic must approve).

## Phase 1 status (built)
Lead -> **Emails** tab (compose with templates + merge fields + preview, threaded replies, delivery status), **Inbox** page
(Needs reply / Unmatched / All), Settings -> **Email** (own sender + signature, setup checklist, templates), sidebar badge,
"Needs reply" badge and filter on leads. Inbound replies: matched by reply-address token, then by sender address, else Unmatched;
they set the thread to *Needs reply*, move a `new` lead to `contacted` (forward-only), create a high-priority task, alert the rep,
log a contact, and a "STOP" reply opts the lead out. Delivery webhooks update status; hard bounce flags the address; spam complaint opts out.
Not yet: sequences, campaigns, unsubscribe page, attachments download, editable rules (Phases 2-4).

## Phase 2 status (built)
**Sequences** (Sequences page): ordered steps with delays, each step an email template; per-sequence "keeps running while status is..." (default New/Contacted;
a win-back sequence can target Lost); auto-enrol new leads by source; manual enrol (single lead from its Emails tab, bulk via API); pause/resume; stats.
Engine: runs every 5 min, daily cap (`EMAIL_DAILY_CAP`), follow-ups only in office hours, the first email immediate, retries failures (3 attempts) and
cannot double-send (row lock + idempotency key + DB unique constraint on enrolment+step). **Stops automatically** on: reply, status moving outside the allowed set,
appointment booked, unsubscribe, spam complaint, hard bounce, address removed, lead archived, or a person pressing Stop.
Every automated email: unsubscribe footer + link (confirm page, so link scanners can't unsubscribe anyone), one-click `List-Unsubscribe` headers, postal address;
automated mail is **not** logged as human contact (no fake score/last-contact boosts). Replies land in the same thread as the sequence emails.
Not yet: one-off campaigns / audience builder / reports (Phase 3), editable rules screen (Phase 4).

## Phase 3 status (built)
**Campaigns** (managers): message (from a template, editable), audience from the Leads-page filters (status, source, treatment, score band, India/international,
country, tag, created range, min score) with a **live count and a breakdown of everyone left out and why**; automatic exclusions that cannot be turned off
(no email, bounced, unsubscribed, archived) and two soft ones (emailed by a sequence/campaign in the last N days; waiting for a reply). Test send to yourself,
send now or schedule, **audience frozen at confirmation**, per-person re-check at send time, pause / resume / cancel, shared daily cap (campaign share 70%),
office hours only. Report: sent, delivered, clicked, replied, bounced, unsubscribed, marked spam, **booked an appointment afterwards**, plus a per-person table.
Opens are shown with a caveat (unreliable). Replies land in the normal Inbox. Not yet: A/B subjects, link-level click detail, editable automation rules (Phase 4).
