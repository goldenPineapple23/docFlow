# DocFlow — Runbook

Step-by-step procedures for running DocFlow. Written for the founder: every
step says exactly what to click or paste, and what you should see. Phase 6
completes this file (restore drill, parser upgrades, constants); Phase 5.5
starts it with the procedures that were needed first.

---

## 1. Applying a database migration

Migrations are SQL files in `supabase/migrations/`, applied in number order,
never edited after they have been applied (build prompt Section 5). They are
applied by hand in the Supabase SQL Editor, because the app's own database
role deliberately cannot change the schema (DECISIONS.md D-013, D-017).

**Order of events for any pull request that needs a migration:**

1. Take the backup below, **before** anything else.
2. Check the row counts.
3. Apply the migration file to `docflow-staging`.
4. Tell Claude it is applied. **Do not merge the pull request until Claude has
   run the tests against staging and confirmed** (the PR message says so at the
   top).
5. Merge. Keep the backup until you decide to drop it.
6. From Phase 6 on: only after all of this on staging, apply the same file to
   `docflow-prod`, again with its own backup first.

**The one exception to backup-first (decided 2026-09-30, Stage 3b):** a
migration that **only creates Storage buckets** (inserts into
`storage.buckets`) and touches no existing table, policy or row skips steps
1-2. There is nothing in it to restore. A migration that does anything else
as well, even adding one policy, takes the backup as normal.

### 1.1 The standard backup (run before the migration)

Replace `NNNN` with the migration's number and list every table the migration
touches (the PR message names them). In the Supabase SQL Editor:

```sql
create schema if not exists backup_NNNN;

create table backup_NNNN.<table_one> as table public.<table_one>;
alter table backup_NNNN.<table_one> enable row level security;

create table backup_NNNN.<table_two> as table public.<table_two>;
alter table backup_NNNN.<table_two> enable row level security;
```

**Why RLS on a backup table:** Supabase's security advisor flags any table
without row-level security. Turning it on with no policies means only the
project owner (you, in the SQL Editor) can read it; the app and the public API
cannot. A backup holds customer data and gets exactly the same protection as
the table it copies.

### 1.2 The row-count check

```sql
select 'table_one' as t, (select count(*) from public.table_one) as live,
       (select count(*) from backup_NNNN.table_one) as backup
union all
select 'table_two', (select count(*) from public.table_two),
       (select count(*) from backup_NNNN.table_two);
```

`live` and `backup` must match on every row. If they don't, stop and say so;
don't run the migration.

### 1.3 Dropping a backup

Only when you decide to, never automatically, and never while the change it
protects is still being checked. When (founder, 2026-09-29):

- **Staging:** once the migration's PR has merged (the suites have passed on
  staging with the migration applied by then).
- **Production:** 14 days after the migration was applied there, if nothing
  about it has needed the backup.

Each drop is its own decision: look at what the schema holds first, then drop
that one schema. A backup holds customer data (in production), so it should
not outlive its purpose.

```sql
-- what it holds
select table_name from information_schema.tables where table_schema = 'backup_NNNN';

drop schema backup_NNNN cascade;
```

To list every backup still there:

```sql
select n.nspname as backup, string_agg(c.relname, ', ' order by c.relname) as tables
  from pg_namespace n left join pg_class c on c.relnamespace = n.oid and c.relkind = 'r'
 where n.nspname like 'backup%' group by n.nspname order by n.nspname;
```

**On `docflow-staging`: none since 2026-09-30.** The founder dropped
`backup_0026` to `backup_0032` that day, after the PR for `0032` (the card
billing follow-up, PR #29) merged.

`docflow-prod` doesn't exist yet (Phase 6), so it has no backups.

### 1.4 Running the test suites against staging

**Standing rule: one suite at a time.** Run core, worker, API and web against
`docflow-staging` one after another, never at the same time. The suites share
one database. Several tests create and delete their own tenants, and some
check a count across the whole table, so one suite's test data can fail
another suite's test. This happened on 2026-09-26: the API suite's `deal7`
case failed while the worker suite was running (DECISIONS.md D-160).

**The API and worker suites enforce it (D-180).** Each run takes a Postgres
advisory lock when it starts and holds it until it ends. A second run of
either suite against the same database stops at once, with exit code 3 and a
message naming the run that holds the lock:

```
Exit: Another test run is using this database: docflow-test-run api pid=23972 host=NKPC started=18:53:17Z.
```

Wait for that run to finish, or stop it, then start again. The lock goes with
its run: it is released at the end, and also when the run is killed (tested
three times against staging), so there is nothing to clear by hand. Collect-only
runs (`--co`) don't take it. It doesn't cover the web suites or scripts such as
`seed_live_e2e.py`: those still follow the rule by hand. This came from
2026-09-29, when two sessions ran the full API suite 76 seconds apart and each
deleted a tenant the other was counting.

The rule stays until every test uses data only it can see: rows with a unique
prefix that the test filters on, or a transaction the test rolls back. The
audit of the "count everything" tests is a Phase 5.5 Stage 5 item
(`docs/BUILD-STATUS.md`).

How a run is reported:

- Save each suite's full output to a file. Never cut it with `tail` or
  `head`: the exit code of the pipe replaces pytest's, so a failed run looks
  like it passed, and the failure details are lost.
- Quote pytest's last line as printed. The API suite should read `571 passed,
  3 deselected` with nothing failed or skipped (staging, 2026-09-30, Stage 3b
  on `aefd428`; the DOC-028 follow-up commit changed test wording, not the
  count; it was 424 at Stage 1, 562 after card billing and 568 on `e3e8641`).
  If the count is *lower* than the number written here, find out what
  stopped running before calling the run green.
  The 3 deselected are the `live_api` tests, which only run at checkpoints
  (`apps/api/pyproject.toml`): `pytest -m live_api` — the golden fixture, the
  golden fixture with examples, and the example-contamination check.

### 1.5 The live end-to-end suite (`apps/web/e2e-live`)

The one browser suite with nothing stubbed: a real sign-in, the real API over
HTTP, real Postgres, real RLS (DECISIONS.md D-166). It needs three things
running before it will do anything useful.

1. **The API**, on port 8000:

   ```
   cd apps/api && .venv/Scripts/python -m uvicorn app.main:app --port 8000
   ```

2. **A current production build of the web app.** The suite serves it on port
   3101, and Next compiles `NEXT_PUBLIC_*` into the bundle — so a build made
   against different settings will point the browser at the wrong stack:

   ```
   cd apps/web && npm run build
   ```

3. **Then the suite itself**, which seeds its own throwaway tenant, reviewer
   and orders before the run and deletes them after it:

   ```
   cd apps/web && npm run test:e2e:live
   ```

It counts as one of the suites under the one-at-a-time rule above. The tenant
it creates is named `Acme Test Live E2E <id>`; if a run is interrupted before
its teardown, remove the leftovers with:

```
python scripts/seed_live_e2e.py teardown --out apps/web/e2e-live/.seed.json
```

CI runs the same suite in the `web-live` job against the local Supabase stack,
so it needs no staging credentials there.

### 1.6 Standing rule: the suite reports its own evidence

**A suite says why it failed. Nobody diagnoses a failure from a symptom.**
When a run fails, the first fix is not to the product — it is to make the
failure name its own cause. Only then fix what it names.

What this means in practice:

- **A timeout is not a diagnosis.** `waitForURL` timing out says the page
  never arrived; it says nothing about why. A suite that can time out on a
  dependency (a sign-in, a service, a build) checks that dependency first,
  outside the browser, and fails there with what it found.
- **Print what the other side actually said.** Not "sign-in failed" but what
  `/auth/v1/settings` reports the server offers. Not "401" but the error type
  the verifier raised. A failure that carries the other side's own answer ends
  in one run.
- **Two plausible fixes in a row means the evidence is missing, not that the
  third guess will land.** Stop changing the product and make the suite talk.
- **CI logs are unreadable here** (`project-github-workflow`: job logs return
  403). The message thrown by the test is the whole of what we get, so it has
  to be enough on its own. Playwright's `github` reporter puts it in the
  annotations; make it worth reading.
- **This applies to the product too.** A path that fails closed in silence —
  a rejected token, a refused write — logs the error type and message, never
  the token, the claim or the data (Section 7.10).

**Why this is a standing rule and not one decision's footnote.** Twice in
Phase 5.5 a diagnosis lived somewhere unreadable and cost multiple round
trips: the `web-live` sign-in failure, where two convincing fixes were both
wrong and what ended it was a seeding-time check that printed the server's own
settings (D-166); and the session-token skew behind it, invisible until the
JWKS path logged why it had rejected a token (D-167). Both were found by
instrumentation, not by reasoning. A test that fails without saying why costs
more than the bug it is hiding.

**Related:** D-166, D-167; §1.4 on how a run is reported.

### 1.7 Standing rule: an isolation failure means stop and report

**When a check of an isolation boundary fails -- a sandbox, a tenant boundary,
a network or permission control -- stop and report it. Do not fix it and
re-run first.** (Founder, 2026-09-28, after the D-150 spike.)

The one exception is a bug in the *measurement*, not the boundary. A
measurement may be fixed and the check re-run **only if**:

1. the fixed check is shown to **fail against the positive control** -- run it
   where the boundary is absent (outside the sandbox, as the privileged user,
   in the other tenant) and quote it failing there. A check that cannot fail
   proves nothing when it passes; and
2. **both runs' output is kept** -- the failing run and the re-run -- next to
   the result, so a reader can judge the call.

If either cannot be done, the failure stands and is reported as a failure.

**Why:** in the D-150 spike, run 1's interfaces check failed because it read
`/sys/class/net`, which shows the machine's interfaces, not the sandbox's. The
fix was right, but it was re-run before the corrected check had been shown to
fail anywhere -- so for a while "PASS" rested on a check nobody had seen
fail. The positive-control run was done afterwards
(`docs/spikes/d150-fly-netns/evidence-run3-interfaces-control.txt`: FAIL on
the machine, PASS in the sandbox). The order is the rule: show it can fail,
then trust it passing.

**Related:** D-150; §1.6.

---

## 2. Inbound email (Postmark)

The inbound webhook answers two separate questions, and it is worth keeping them
separate in your head because only one of them is a secret:

- **Which tenant is this for?** The per-tenant token in the URL path. Public by
  design — it is the local part of the address the customer gives to its buyers.
- **Is this actually Postmark?** HTTP Basic credentials on the webhook URL. This
  is the secret, and it is the only thing standing between the intake endpoint
  and anyone who has ever received a PO from one of your customers (review
  finding H8).

### 2.1 Cutover order — credentials before enforcement

> **Do not run this on an address real customers send to until Stage 2c has
> landed** (founder, 2026-09-27; D-171). A refused request looks identical from
> outside whether it is a bad cutover or an attacker, and a bad cutover means no
> mail arrives at all. Until 2c raises a founder alert on refusal, the only
> signal is step 4's log line — which nobody is watching in real time. Staging
> and a test address are fine now; a production intake address waits.
>
> **2c builds that alert** (D-175): every refusal raises a high-severity
> `intake_webhook_refused` founder alert naming the reason, one open alert per
> reason. The condition is met for an address once migration `0029` is applied
> to the database behind it and the 2c code is deployed there. After a
> cutover, a refusal you did not expect shows up in the Console's attention
> panel -- `not_configured` or `no_credentials` means one of steps 1–3 went wrong;
> `mismatch` means the credential in Postmark's URL and the one deployed differ.


**Do these in order.** Step 3 is what makes mail flow; steps 1–2 are what makes
it flow *authenticated*. Doing 3 first means a window where the old hole is open;
doing 3 last means no window at all, at the cost of inbound mail being refused
until it is done — which is the right way round, and it is why a blank
credential refuses everything rather than falling back to token-only.

1. Generate the credential pair:

   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Use a distinct value for the username too — it is compared in constant time
   like the password, so it is a second 32 bytes of secret, not a label.

2. Set `POSTMARK_WEBHOOK_USERNAME` and `POSTMARK_WEBHOOK_PASSWORD` in the API's
   environment and **deploy**. Inbound mail is now refused for everyone,
   including Postmark, which is expected: nothing is pointed here yet.

3. In Postmark, set the inbound webhook URL to carry them:

   ```
   https://USER:PASS@<api-host>/intake/email/{token}
   ```

   Postmark sends them as an `Authorization: Basic` header. Send one test mail
   and confirm a 200 in Postmark's own delivery log.

4. Confirm nothing is being refused. **From Stage 2c onward** this is the
   Console's attention panel: a refusal raises an `intake_webhook_refused` alert
   at severity **high**, once per day per reason, so a mistake in step 3 shows up
   in minutes rather than when a customer asks where their orders went.
   **Until 2c lands**, read the API log instead — every refusal logs
   `intake_webhook_refused reason=<word>`, and the word tells you which mistake
   it was:

   | reason | what it means |
   |---|---|
   | `not_configured` | step 2 didn't take effect — the environment has no credentials |
   | `no_credentials` | step 3 didn't take effect — Postmark is posting without them |
   | `mismatch` | step 2 and step 3 disagree — compare them, do not guess |
   | `not_basic` / `undecodable` | something other than Postmark is posting here |

**Never** put the credentials in the tenant's intake *address*, a Console
screen, a log line or a support thread. They are not in any of those today and a
test asserts it (`apps/api/tests/test_email_intake_auth.py`).

### 2.2 Rotating the credentials

Rotation has a gap by nature: Postmark's webhook URL holds one credential pair
at a time, so between changing the environment and changing Postmark, mail is
refused. Postmark retries a non-200 for a while, so a short gap loses nothing —
but keep it short and do it deliberately rather than discovering it.

1. Generate a new pair (2.1 step 1).
2. Update Postmark's webhook URL to the new pair **first**. Inbound mail is now
   refused; Postmark begins retrying.
3. Set the new values in the API's environment and deploy.
4. Send a test mail and confirm a 200 in Postmark's delivery log. Postmark's
   retries of anything refused in the gap now succeed.
5. Confirm no new `intake_webhook_refused` alert since the deploy.

**Rotate when:** the credential has been in a shell history, a screenshot, a
ticket or a third party's hands; someone with access to it leaves; or annually,
whichever comes first. Rotating is cheap and the gap is minutes.

**What rotation does not do:** it does not change any tenant's intake address.
Rotating a *tenant's* address is a separate, per-tenant action on the tenant page
(7.16.3) with its own grace period.

### 2.3 Confirming the source addresses, then enforcing the allowlist

`POSTMARK_INBOUND_IP_ALLOWLIST` is **log-only until confirmed** (D-155). An
allowlist enforced on a guess silently drops customers' purchase orders, which
is the exact failure the product exists to prevent — so it corroborates the
credentials, it does not replace them.

1. Leave it blank until real inbound mail is arriving.
2. After the first genuine inbound mail, collect the source addresses the API
   saw. With the allowlist blank nothing is logged about the source, so set it to
   a deliberately wrong value (e.g. `203.0.113.1`) for the collection window:
   every request then logs `source_not_on_allowlist`, and the request's source
   address is in the web server's own access log.
3. Compare what you collected against Postmark's published inbound IP list
   (Postmark documents these; take them from Postmark's own page, not from a
   search result).
4. If they agree, set the confirmed addresses. **They are still log-only** — the
   code has no enforcing branch, deliberately. Turning enforcement on is a code
   change with its own test and its own `DECISIONS.md` entry, made once the list
   has been stable across a few weeks of real mail.
5. If they disagree, stop and find out why before changing anything. A source
   address that is not Postmark's and still passed the credential check is worth
   understanding, not filtering.

### 2.4 Outbound: the sending domain and the support mailbox (founder, 2026-09-30)

Needed before automatic sending (its own stage after 3e, D-103). Verification
can take up to 48 hours, so it is done ahead of the stage. `yourdomain.com`
below is the company's main domain (the one on the website and in the
customer agreement).

**Addresses on the domain:**
- `notifications@yourdomain.com`: the From address of every DocFlow email
  (`EMAIL_FROM_ADDRESS`). Needs no mailbox; customer emails carry Reply-To.
- `support@yourdomain.com`: the support mailbox (`SUPPORT_EMAIL`), routed to
  the founder's inbox.
- The intake addresses stay on their own subdomain (`INTAKE_EMAIL_DOMAIN`,
  e.g. `mail.yourdomain.com`, section 2), so their MX record never conflicts
  with the root domain's.

**1. Find the DNS host.** The records go wherever the domain's nameservers
point, which may not be where it was bought:
`nslookup -type=ns yourdomain.com` (Cloudflare shows `*.ns.cloudflare.com`;
GoDaddy `*.domaincontrol.com`; Namecheap `*.registrar-servers.com`; Google
`ns-cloud-*.googledomains.com`).

**2. Postmark: add the domain.** account.postmarkapp.com → **Sender
Signatures** → add a **domain** (not a single-address signature) → `yourdomain.com`.
Postmark then shows two records under **DNS Settings**.

**3. Add the records at the DNS host** (type, name, value exactly as Postmark
shows them; most hosts add `.yourdomain.com` to the name themselves, so type
only the part before it):

| Type | Name | Value |
|---|---|---|
| TXT | the DKIM name Postmark shows, e.g. `20260930123456pm._domainkey` | the `k=rsa; p=...` value Postmark shows (copy it whole) |
| CNAME | `pm-bounces` | `pm.mtasv.net` |
| TXT | `_dmarc` | see step 4 |

No SPF change is needed for Postmark: with the Return-Path CNAME, Postmark's
mail passes SPF on `pm-bounces.yourdomain.com` (Postmark: "Why we no longer
ask for SPF records"). On Cloudflare, set the CNAME to **DNS only** (grey
cloud), not proxied.

**4. DMARC.** One `_dmarc` TXT record per domain: if one exists, edit it, don't
add a second. Start with monitoring only:

```
v=DMARC1; p=none; rua=mailto:<the address Postmark's DMARC tool gives>
```

Get the `rua` address from dmarc.postmarkapp.com (free weekly digests: enter
the domain and the founder's email). After a few weeks, once the digests show
every legitimate sender passing (Postmark, the support mailbox's provider),
move to `p=quarantine`, later `p=reject`.

**5. Check.** In Postmark, the domain's **DNS Settings** → **Verify** next to
each record, until both show verified (up to 48 hours). From Windows:

```
nslookup -type=cname pm-bounces.yourdomain.com
nslookup -type=txt <dkim name>._domainkey.yourdomain.com
nslookup -type=txt _dmarc.yourdomain.com
```

**6. Account approval.** A new Postmark account can only send to addresses on
its own verified domains until Postmark approves it for sending to anyone:
request approval in the account, describing the mail as transactional
(invites, billing notices, reminders to business customers).

**7. The support mailbox.** Route `support@yourdomain.com` to the founder's
inbox:
- If the domain already has email (Google Workspace, Microsoft 365): add
  `support@` as an alias or group there. Nothing else to change.
- If it has none and DNS is on Cloudflare: **Email** → **Email Routing** →
  enable, then **Routing rules** → **Create address**: `support` → **Send to
  an email** → the founder's inbox (confirm the verification email).
  Cloudflare adds its MX and SPF records to the root domain itself. Replies
  then go out from the founder's own address, not `support@`.
- To reply *as* `support@` (recommended once customers write in), the address
  needs a real mailbox: Google Workspace (or similar) on the domain. Don't use
  Postmark for personal replies: it is for application email.

Then set `SUPPORT_EMAIL=support@yourdomain.com` and
`EMAIL_FROM_ADDRESS=notifications@yourdomain.com` in `.env` and restart the
API. The Postmark server API token (`EMAIL_PROVIDER_API_KEY`) waits for the
automatic-sending stage: while it is blank, every email stays held in the
Outbox.

---

## 3. Onboarding a new tenant — checklist

The Console walks you through the nine setup steps (Intake → Go live). This
list is the checks that sit around them. Phase 6 completes it.

**Once, before the first real customer (account-wide):**

- [ ] **Stripe must never cancel a subscription by itself.** Suspension is
  your decision (D-125), taken through DocFlow's lifecycle; a cancel on
  Stripe's side skips it and leaves an active tenant with no billing. Two
  settings, on two different pages, both set to **leave the subscription past
  due**:
  - **Failed card payments, after the last retry** (card billing): **Billing**
    → **Revenue recovery** → **Retries**
    (`https://dashboard.stripe.com/revenue_recovery/retries`), the outcome
    "if all retries for a payment fail". This is the one found still on
    "cancel" in the sandbox on 2026-09-29, after it had been changed on the
    other page: a test subscription was cancelled after its last retry.
  - **Invoices sent to customers** (invoice billing): **Settings** (gear
    icon) → **Billing** → **Subscriptions and emails**
    (`https://dashboard.stripe.com/settings/billing/automatic`). Today it
    cancels 90 days after the due date.
- [ ] **Retry schedule: Smart Retries, 8 tries within 1 week** (founder,
  2026-09-29), on the same Retries page. The last retry then lands by day 7,
  well before day 14, the earliest date a past-due tenant may be suspended
  (`CURE_PERIOD_DAYS`); from then until the owner updates the card, DocFlow
  charges the new card itself when it is added (D-181).
- [ ] **Check the account before saving.** A sandbox is an account of its
  own: the account switcher (top left) must show the account DocFlow's key
  belongs to, and a link scoped to it carries its id
  (`https://dashboard.stripe.com/<account id>/test/...`). Each setting exists
  separately in test mode, in each sandbox, and in live mode; do live mode
  before the first real customer. To confirm in test mode, ask for the
  "retries exhausted" check (a Stripe test clock): the subscription must end
  `past_due`, never `canceled`.
- [ ] **The webhook endpoint sends card billing's events** (D-181), besides
  the `customer.subscription.*` events it already sends:
  `checkout.session.completed` (a card saved, a setup fee paid at signing)
  and `customer.updated` (a card changed; DocFlow charges a past-due
  account's open invoice to it). In Stripe: **Developers** → **Webhooks** →
  the DocFlow endpoint → **Select events**.
- [ ] **A support mailbox that someone reads**, and its address in
  `SUPPORT_EMAIL` (founder, 2026-09-29). The card-billing emails tell
  customers to email it to cancel, and "Ask for a card" refuses while it is
  blank (ONB-018). Cancel requests that arrive there follow section 6.
- [ ] **The customer portal is on**, for the owner's "Update card" (D-181):
  **Settings** → **Billing** → **Customer portal**. DocFlow uses its own
  portal configuration (tagged `card-update-v1`: update the card and see
  invoices, no cancelling), created automatically the first time an owner
  opens "Update card", and put back if it is changed in the dashboard. Don't
  delete it; the account's default configuration isn't used.
- [ ] **Stripe's own trial-ending email is off** (founder, 2026-09-29; done
  in the sandbox that day): **Settings** → **Billing** → **Subscriptions and
  emails**, the reminder Stripe sends before a free trial ends. DocFlow
  sends its own trial-ending email to card-billed customers, 2 days before
  the trial ends (D-181 addendum 2), so with Stripe's on, customers would get
  two.

**For each new tenant:**

- [ ] **Check the line count of the prospect's largest sample order.** Open
  the biggest purchase order in their intake files and count its line items
  (or look at the line number of the last item).
  - Up to about 1,000 lines: DocFlow reads it in one pass. Measured on
    2026-09-26 (D-161): 600 lines read exactly in 8 minutes, about $0.81.
  - Over about 1,000 lines: DocFlow can't read it yet. It fails as DOC-020
    ("enter this order by hand for now") after a paid read of up to about
    15 minutes and about $1.40. Tell the prospect before go-live that orders
    this long must be keyed by hand for now, and record it in your onboarding
    notes. Splitting long orders across several reads is deferred (D-158).

---

## 4. Console MFA (D-151, D-177)

The Console needs a code from an authenticator app as well as a password, once
`CONSOLE_MFA_ENFORCED` is on. With it on:

- every `/admin` page needs a sign-in confirmed with a code (an `aal2` session);
  until then the Console shows only the authenticator screen;
- **hard delete, clear quarantine, cancel, intake-address rotation, buyer merge,
  go live and tier change** also need a code entered in the last five minutes
  (`MFA_STEP_UP_MAX_AGE_SECONDS`, 300 s, plus `MFA_CLOCK_TOLERANCE_SECONDS`,
  30 s, for Supabase's clock). The Console asks for one and then carries on.

It ships **off**, so you can enrol before it can lock anyone out. While it is
off, three things say so: a warning in the API's startup log, an amber banner
on every Console page, and a high-severity founder alert ("Console MFA
enforcement is off") raised on the first Console request after each API start.

### 4.1 Switching it on, the first time

Do these in order. Don't turn it on before step 3 is confirmed.

1. **Enrol.** Sign in to the Console, open **/admin/security** (the banner
   links to it), choose **Set up an authenticator**, scan the QR code with an
   authenticator app, and enter the code it shows. The page then says "This
   sign-in is confirmed with your authenticator."
2. **Add a backup.** On the same page choose **Add a second authenticator
   (backup)** and enrol a second one -- in a password manager, or on another
   device. Supabase gives no backup codes (recovery codes were considered and
   left out, founder 2026-09-28), so a second authenticator is what gets you in
   if the phone is lost without needing 4.3.
3. **Confirm.** Tell Claude (or check in the Supabase dashboard under
   Authentication → Users → your user) that your account shows at least one
   **verified** TOTP factor.
4. **Turn it on.** Set `CONSOLE_MFA_ENFORCED=true` in the environment the API
   runs with (the root `.env` locally; the host's environment settings when
   deployed) and restart the API. The startup warning and the banner stop.
5. **Check it.** Sign out and in again: the Console should ask for a code
   before showing anything. Enter it. Then, more than five minutes later, try
   a destructive action (e.g. rotate a test tenant's intake address): it
   should ask for a fresh code first.

**Emergency lever.** If something is wrong and you are locked out of the
Console, set `CONSOLE_MFA_ENFORCED=false` and restart the API. The banner,
the startup warning and the founder alert come back while it is off -- that
is intended; turn it back on as soon as the cause is fixed.

### 4.2 docflow-prod: never deployed with it off after enrolment

**Once you have enrolled, `docflow-prod` is never deployed with
`CONSOLE_MFA_ENFORCED` off** (founder, 2026-09-28). The only exception is the
emergency lever above, used deliberately, for as short a time as it takes.
Check the deploy's environment settings for it as part of every production
deploy. In `docflow-prod`, also remember to turn public signup off (SETUP.md
Step 1.7) -- the two together are what keep the Console closed.

### 4.3 A lost or broken authenticator

If you still have your backup authenticator (4.1 step 2), sign in with it and
use /admin/security to add a replacement. Otherwise:

1. **Supabase dashboard** → the project → **Authentication** → **Users** →
   click your user → scroll to **Danger zone** → **Remove MFA factors** →
   confirm. **Not** "Ban user" and **not** "Delete user", which sit just below
   it. (Anyone who can reach this page can remove MFA from any account -- so
   your Supabase account itself must have MFA on, and so must the GitHub
   account that can deploy. They are the root of trust for this procedure.)

   **This removes every authenticator on the account at once** -- the
   dashboard has no per-factor delete -- so your backup goes too.
2. **Sign in to the Console again.** With enforcement on, it shows the
   authenticator screen with **Set up an authenticator**. Enrol a new one,
   **and a new backup**, as in 4.1 steps 1-2.
3. The lost device's codes stopped working at step 1; nothing else needs
   revoking. The founder alert log and `admin_actions` show nothing for this,
   because it happens in Supabase, not in DocFlow -- note it in your own
   records.

**Tested on staging, 2026-09-28** (D-177): a throwaway platform admin with a
verified authenticator; the founder used **Remove MFA factors** in the
dashboard exactly as written above; the account then had no authenticator,
signed in with its password alone, enrolled a replacement and passed a real
challenge with it. The account was then revoked and deleted.

---

## 5. The worker: time limits, the prefork pool, and jobs that never finish (Stage 3a, D-179)

### 5.1 Production runs Celery's prefork pool

Every task has a **hard** time limit, and nothing else stops a parser hung
inside C code. Those limits only work under Celery's **prefork** pool on
Linux. The `solo` pool used on Windows for development **ignores them**.
Start production and staging workers with `--pool=prefork`. Never deploy one
with `--pool=solo` or `--pool=threads`.

The evidence that the limits work is `apps/worker/tests/test_time_limits_prefork.py`,
which starts a real prefork worker in CI (Linux). On Windows it skips.

### 5.2 The limits and related constants (`packages/core/docflow_core/constants.py`)

| Constant | Value | What it does |
|---|---|---|
| `DOCUMENT_TASK_TIME_LIMIT_SECONDS` | 27 min | Hard limit on reading one document. Under `STUCK_PROCESSING_TIMEOUT_MIN`, so a killed attempt is retried by the sweep, never raced by it. |
| `EXPORT_TASK_TIME_LIMIT_SECONDS` / `IMPORT_TASK_TIME_LIMIT_SECONDS` | 5 min each | Hard limits on building an export / reading a catalog or customer list. |
| `ROLLUP_TASK_TIME_LIMIT_SECONDS` | 15 min | Nightly KPI rollup. |
| `SCHEDULED_JOBS_TASK_TIME_LIMIT_SECONDS` | 10 min | Check-ins, reminders, digests. |
| `LIFECYCLE_SWEEP_TASK_TIME_LIMIT_SECONDS` / `LIFECYCLE_SWEEP_TIME_BOX_SECONDS` | 10 min / 4 min | Past the time box the sweep takes no new tenant; the next tick takes the rest. |
| `STUCK_SWEEP_TASK_TIME_LIMIT_SECONDS` | 4 min | The stuck sweep. |
| `WORKER_MAX_MEMORY_PER_CHILD_KIB` | 700 MiB | A worker process is replaced after a task that took it past this. It isn't a cap during a task (the parse service's limits are, from 3c). |
| `STUCK_PROCESSING_TIMEOUT_MIN` | 30 | A document in `processing` this long is retried or failed (DOC-022). An export still `pending`, or an import still `parsing`, this long after it was created is failed (EXP-009 / IMP-009). |
| `MAX_PROCESSING_ATTEMPTS` | 3 | Tries before a document whose worker stopped is failed (DOC-022, cause `worker_stopped`). A timeout gets one retry only (cause `timeout`). |
| `EXPORTS_NOT_FINISHED_ALERT_PER_DAY` | 3 | A tenant with more EXP-009s than this in one UTC day raises one `exports_not_finishing` alert. |

A test reads the finalized Celery app and fails if any task gains a soft
limit, or if the document task's hard limit is no longer 27 minutes. So a
global `task_soft_time_limit` added later is caught (Celery would apply it to
every task that doesn't set its own, which is all of them).

### 5.3 When the founder gets one of these alerts

- **`document_stuck`, cause `timeout`:** the document hit its hard limit
  twice. It is the file, not the worker. Look for `document_timeout
  document_id=...` in the worker log. The customer sees DOC-022.
- **`document_stuck`, cause `worker_stopped`:** the worker died or was
  restarted under the document three times. Check the worker's restarts and
  memory.
- **`exports_not_finishing`:** a tenant's exports keep being lost or killed.
  Check the worker is running and consuming the `interactive` queue, and look
  for `export_` lines in the log. The tenant was told to start the export
  again (EXP-009); the health strip shows the day's total.
- **IMP-009 on an import in the Console:** start the import again from the
  same file. If it stops again, find the import's ID in the worker log.
- **`reactivation_invoices_to_review`:** a reactivation kept the tenant's old
  subscription, because Stripe still had it live after the suspension.
  DocFlow changed nothing at Stripe. In the Stripe dashboard, for that
  subscription: **collect** the invoices under "Before suspension" (service
  was delivered); **void** the drafts and open invoices under "During
  suspension", and **refund** the paid ones. If the alert says the invoice
  list couldn't be read, look at the subscription's invoices in Stripe
  directly.

### 5.4 A Stripe cancel still owed after a suspension

`tenants.stripe_cancel_pending_at` is set when a suspension claims a tenant
with Stripe billing, and cleared when the cancel goes through. Every
lifecycle sweep retries owed cancels first; a failure raises
`stripe_cancel_failed`. To see what is owed:

```sql
select id, name, status, stripe_subscription_id, stripe_cancel_pending_at
from tenants where stripe_cancel_pending_at is not null;
```

Reactivating the tenant clears the mark. Don't clear it by hand while the
tenant is still suspended: the subscription would keep billing.

## 6. Card billing: cancelling during the trial, and refunds (D-181)

### 6.1 A cancel request during the trial

Customers cancel by emailing `SUPPORT_EMAIL` (the card-billing emails say so;
they can't cancel in Stripe, because DocFlow's portal configuration has
cancelling turned off). The go-live and trial-ending emails ask them to
email **by the day before the trial ends** (the cancel-by date), which leaves
you a day to enter the cancel before Stripe charges. A request that arrives
on the trial's last day is still before the trial ends, so 6.2 applies to it.
When a request arrives:

1. **Note the time the request arrived** (the email's timestamp). That time
   decides the refund rule below, not the time you act on it.
2. Console → the tenant → Lifecycle → **Cancel**, reason **Customer
   requested**. For a tenant still in its trial, confirming it also tells
   Stripe to end the subscription at the trial's end and removes a pending
   setup fee, so nothing more can be charged. If Stripe doesn't answer, you
   see CON-006 and nothing has changed: try again.
3. Reply to the customer confirming the cancel and the date service ends
   (the trial's last day, unless you set a later date).

### 6.2 The refund rule (founder, 2026-09-29)

**A cancel request received before the trial ends gets a full refund of
anything charged after that request.** Examples: the request came in on day
6, but the cancel was entered after Stripe had already charged the first
month on day 7 -- refund that charge in full. The standard customer's setup
fee, paid when they added their card (before any request), is
non-refundable and is not part of this rule.

Refund steps, in the Stripe dashboard (check the account switcher first,
section 3):

1. **Customers** → search for the tenant (the Console's billing card has
   **Open in Stripe ↗**, which goes straight there).
2. Under **Payments**, find each payment dated **after** the request's
   arrival time.
3. Open the payment → **Refund** → **Full refund** → reason **Requested by
   customer** → **Refund**.
4. If the subscription is still running (the cancel was entered after the
   trial ended), check that the Console shows the tenant as cancelling, so
   no further month is charged.
5. Add a note in the Console's cancel (or the tenant's lifecycle log) with
   the request time and the refunded amount, so the record says why money
   went back.

Stripe returns the money to the card; it can take 5-10 business days to
reach the customer. DocFlow does not refund anything automatically.

## 7. File storage: Supabase Storage (Stage 3b, D-182)

DocFlow's files live in one private bucket, `docflow-files`, reached through
Storage's S3-compatible endpoint with a Storage-only access key. Nothing
reads the old local `storage/` folder any more except the copy script below.

### 7.1 Creating the key (once per project)

1. Supabase dashboard -> the project -> Storage -> **S3 Configuration**.
2. Copy the **Endpoint** into `STORAGE_S3_ENDPOINT` and the **Region** into
   `STORAGE_S3_REGION` in the root `.env`.
3. **New access key**, described "DocFlow API and worker". Copy the key id
   into `STORAGE_S3_ACCESS_KEY_ID` and the secret into
   `STORAGE_S3_SECRET_ACCESS_KEY`. The secret is shown once.
4. The same four values go into the API's and the worker's secrets on Fly.

The key reaches every file of every tenant. Treat it like the database
password: never in the frontend, never in a log, never in a ticket. If it
leaks, revoke it on the same page and create a new one.

### 7.2 Copying staging's files into the bucket (the 3b rollout)

Run from the repo root with `apps/api/.venv`'s Python, on the machine that
holds the `storage/` folder:

    python scripts/copy_storage_to_bucket.py            # dry run first
    python scripts/copy_storage_to_bucket.py --apply    # then the copy

It copies every file a row still references under the same key, reads each
one back and checks its SHA-256, never overwrites something different that
is already in the bucket, and leaves the local folder untouched. A clean run
exits 0 and lists nothing. **Run it again with `--apply` straight after the
switch** (the delta copy): it picks up anything the old code wrote in the
meantime; "referenced but missing locally" must then be 0.

The orphan count is files no row references (from failed uploads, and old
test runs). They are left where they are.

### 7.3 When the founder gets a `storage_unavailable` alert

At most one an hour for the whole platform; it names the first tenant that
hit it. While Storage is down: uploads answer DOC-025 and nothing is
received; Postmark gets a 503 and sends the mail again later; documents
already received wait in Processing without using up their tries. Check the
Supabase status page and the project's Storage logs. Nothing needs doing
once Storage is back: the waiting documents are picked up by the stuck sweep
within `STUCK_PROCESSING_TIMEOUT_MIN`.

A `document_failed` alert with **DOC-026** is different: the stored original
is missing or isn't the file that was received. That is never an outage. Ask
the customer to upload the file again, and look for how the object went
missing.
