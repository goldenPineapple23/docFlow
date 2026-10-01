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

**On `docflow-staging`:** the founder dropped `backup_0026` to
`backup_0032` on 2026-09-30, after the PR for `0032` (the card billing
follow-up, PR #29) merged. Since then:
- `backup_3b` (the 3b cutover's restore point), unless the founder has
  dropped it;
- **`backup_0034`** (documents 105, extraction_runs 17; live = backup),
  taken 2026-10-01 before `0034`. **Founder, 2026-10-01: dropped after 3c
  has run cleanly on staging for 3 days** -- 3c merged 2026-10-01 20:53 UTC,
  so not before 2026-10-04 20:53 UTC, and only after Claude reports the
  check (staging suites green on `main`; no `document_failed`,
  `document_stuck`, `parse_service_unavailable` or `parse_seccomp_kill`
  alert on staging since the merge). The founder runs, in the SQL Editor on
  `docflow-staging` (no `cascade`, so nothing else can go with it):

  ```sql
  drop table backup_0034.documents;
  drop table backup_0034.extraction_runs;
  drop schema backup_0034;
  ```

  and the date is recorded here and in BUILD-STATUS.

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
already received go back to waiting (Stage 3d: `pending`, `wait_cause =
storage`) without using up their tries. Check the Supabase status page and
the project's Storage logs. Nothing needs doing once Storage is back: the
dispatcher sends each waiting document again every
`STORAGE_WAIT_RETRY_MINUTES` (5), with no maximum.

A `document_failed` alert with **DOC-026** is different: the stored original
is missing or isn't the file that was received. That is never an outage. Ask
the customer to upload the file again, and look for how the object went
missing.

## 8. The parse service (Stage 3c)

The parse service opens every file a stranger sends, inside a per-file
sandbox (BUILD-STATUS "3c detailed design"). More of this section is
written as 3c is built: running it, deploying it, reading the canary's
startup log, the parser-upgrade process, and the
`parse_service_unavailable` alert.

### 8.1 Gate: the Fly self-tests pass before a merge or a production deploy that touches it (founder, 2026-10-01)

CI runs on cgroup v2. Fly machines run cgroup v1, the path production
uses, and **only a Fly run proves that path**. So the self-tests must pass
**on Fly staging** before a change that touches the parse service merges to
`main`, and again before any production deploy of it, not only at the 3c
checkpoint (founder, 2026-10-01: `main` is what every later stage builds on,
so it must not carry a sandbox whose production path is unproven): all of them, `python -m
parse_service.selftest all` (the canary, A, S and B; BUILD-STATUS "3c test
table", B1-B16 and A15 included). **IPv6 isolation is proven only there:**
CI's runner has no IPv6, so A-net IPv6 is NOT-RUN in CI (D-183).

"Touches the parse service" means any change to:
- its code (`apps/parse/`), its Dockerfile, base image or packages
  (LibreOffice and `python3-seccomp` included), or its lock file;
- its `fly.toml`;
- the worker's `parse_client`.

**Setting secrets on Fly (founder, 2026-10-01):** start a new PowerShell
window and run `Set-PSReadLineOption -HistorySaveStyle SaveNothing` before
typing anything else. Values typed literally (a database URL, S3 keys, an
API key) are otherwise saved in plain text in PowerShell's history file. It
applies to that window only; close it when done. Never paste a value into
chat. Each app gets only the secrets its process reads, and its own
database login (F-1): never `docflow_app` shared between apps, never
`postgres` or the service role.

The procedure:
1. Deploy the change to Fly staging first.
2. Check the canary's startup log: every line PASS.
3. Run the self-tests on Fly staging (every line PASS; A-net IPv6 must be
   PASS there, not NOT-RUN), and keep the output in
   `docs/spikes/3c-fly-staging/` with the date and the image digest.
4. Only then merge, and later deploy to production (after its own Fly
   staging run of the merged code).

If any self-test fails on Fly, nothing merges and nothing goes to
production: stop and report (1.7).

**What gates the 3c merge, and what waits (founder, 2026-10-01).** The
worker and the API are not on Fly until F-1's own logins exist (Stage 3e),
so the 3c merge gate is the parse app alone, every item on the Fly machine:
- the canary (8.3), every line PASS;
- every A, S and B self-test PASS, A-net IPv6 PASS (not NOT-RUN);
- **A4 against a stand-in:** a TCP listener on the private network, in a
  throwaway app, passed with `--targets`. The control (reached from outside
  the sandbox on the parse machine) runs first and must succeed; inside a
  job the same address must be blocked. NOT-RUN fails the gate;
- **N1:** `fly ips list` shows one private IPv6 and nothing else;
- **N2:** from outside Fly, the app's public name resolves to no public
  address and nothing answers;
- **N3:** with the parse machine stopped, a client machine in a throwaway
  app calls `http://docflow-parse-staging.flycast/...` with no token. A
  401 proves the request started the machine and the token is enforced.

The throwaway app is destroyed afterwards; its name, machine IDs and times
go in the evidence.

**These gate the first worker/API deploy, not the 3c merge:** G (end to end
through the queue and the parse service), and A4 again against the real
staging API and worker. At that deploy, generate a fresh
`PARSE_SERVICE_TOKEN` and set it on the parse app and the worker together;
the 3c token is not carried over.

### 8.2 Running it

- **Dev (this Windows machine):** SETUP.md Step 7a. `PARSE_ISOLATION=off`,
  one process per file and the same time limit, no sandbox. Refused in
  production mode.
- **CI:** the `parse` job builds `apps/parse/Dockerfile` and runs it with
  `--privileged` (the closest a GitHub runner gets to root in a Fly VM).
  Its self-test, E1, E4 and canary results are annotations on the run's
  summary page.
- **Fly:** the same image. Production mode is on (Fly sets `FLY_APP_NAME`):
  isolation must be on, `PARSE_SERVICE_TOKEN` must be set, and any
  `PARSE_TEST_ONLY_*` setting makes it refuse to start. Private only, over
  Flycast (`fly ips list` must show one private IPv6 address and nothing
  else, after every deploy). Staging deploys with `--ha=false` (one
  machine). **That is staging only:** production's machine count is a
  Phase 6 decision and does not carry over by default (founder,
  2026-10-01).
- **Secrets:** `PARSE_SERVICE_TOKEN` goes on the worker's app and the parse
  service's app, never the API's. If it is set on the API, the API refuses
  to start ("DocFlow API refused to start: PARSE_SERVICE_TOKEN is set ...")
  and Fly keeps restarting it: `fly secrets unset PARSE_SERVICE_TOKEN -a
  <api app>`.

### 8.3 Reading the startup log (the canary)

Before it opens its port the service runs its canary through the real
sandbox and prints one line per check:

```
canary: cgroup v1
RESULT canary:network PASS -- {...}
RESULT canary:view PASS -- {...}
RESULT canary:seccomp PASS -- {...}
RESULT canary:memory-cap-kills PASS -- {...}
RESULT canary:cpu-quota-set-and-budget-kills PASS -- {...}
canary: PASS
parse service listening on port 8100
```

Any FAIL, or "refused to start", means the machine cannot isolate a file:
the service exits, Fly restarts it, it fails again, and the worker gets
"unavailable" -- documents wait, nothing is parsed unsandboxed, and the
`parse_service_unavailable` alert fires. **Stop and report (1.7); never
switch isolation off to get it running.**

### 8.4 When the founder gets a `parse_service_unavailable` alert

At most once an hour for the whole platform. The payload's `reason`:
- `no_connection` / `http_503`: the service is down,
  overloaded, or its canary is failing. Check the app's machines and its
  startup log (8.3). Documents wait (`wait_cause = parse_service`) and the
  dispatcher sends them again every `PARSE_SERVICE_WAIT_RETRY_MINUTES` (2);
  nothing is failed for waiting (Stage 3d).
- `unauthorized`: the worker's and the service's `PARSE_SERVICE_TOKEN`
  differ. Set the same value on both apps.
- `http_4xx`: a request the service refused (a DocFlow bug, never the
  file's fault). Report it.

A **DOC-022** alert whose detail has `last_lost_reason` (`http_502`,
`http_504`, or a read error such as `ReadTimeout`) means the parse requests
got in and never came out: a 502/504 is Fly's proxy (the machine died or
didn't start), not a parser crash, which arrives as a `crashed` answer and
DOC-005.

**A `parse_seccomp_kill` alert** (founder, 2026-10-01: every one, never
rate-limited) means a parse job was killed by the seccomp filter (SIGSYS).
The filter kills only a system call from a foreign architecture (x32 or
32-bit), which no parser makes by accident: treat the file as an attack.
The alert has no syscall number (the kill reports none; only the kernel's
audit log on the parse machine has it, in `fly logs` if the kernel printed
it). The document failed with DOC-005 (an import with IMP-004); nothing
else needs doing in DocFlow. Keep the file's hash from the alert's
document for any follow-up.

### 8.5 Upgrading a parser (an ongoing duty, CLAUDE.md 7.11)

Every library in the image reads files strangers send. When a CVE lands in
one (pdfplumber, python-docx, openpyxl, Pillow, pillow-heif, olefile, xlrd,
defusedxml, LibreOffice, libseccomp, or the base image):
1. Move the pin: `apps/parse/requirements.lock.txt` for a Python package;
   `DEBIAN_SNAPSHOT` in `apps/parse/Dockerfile` for LibreOffice, libseccomp
   or any other Debian package (a snapshot.debian.org date, e.g.
   `20261008T000000Z`: every Debian package in the image comes from the
   archive as it was then); the digest for the base image.
2. CI must pass in full: the unit tests, D2 parity (every fixture's text
   must stay the same), and the self-tests in the real sandbox.
3. Then 8.1: deploy to Fly staging, check the canary log, run all the
   self-tests there.
4. Only then merge (the weekly job's PR included), and deploy to
   production.

**The weekly Debian move (D-183).** Every Monday at 06:00 UTC
`.github/workflows/debian-snapshot.yml` runs the whole CI workflow with the
image built from that day's snapshot. Green: it pushes
`deps/debian-snapshot-<date>` with the new date, and the run's summary page
has the PR link. Open the PR, let its own CI run pass, merge, then 8.1.
Red: nothing is pushed and GitHub emails the failure; read the run's
annotations (D2 parity and the self-tests name what changed) and treat it
as step 2 failing. Run it by hand any time from the Actions tab ("Run
workflow"), for example the day a LibreOffice CVE is announced, rather than
waiting for Monday.

A build never changes by itself: until the date moves, a rebuild installs
exactly what the last one did. If snapshot.debian.org is down, the build
fails; it never falls back to the live archive.

## 9. The dispatcher and waiting documents (Stage 3d)

Since 3d nothing sends a document straight to the queue. A new, released or
test-batch document waits as `pending`, and the dispatcher
(`docflow_core/dispatch.py`, in the worker) sends the next ones, taking turns
between tenants: fewest in flight first, then the tenant served least
recently. At most `TENANT_IN_FLIGHT_CAP` (2) per tenant while another tenant
has something ready, and never more than `DISPATCH_IN_FLIGHT_TARGET` in
flight across all tenants. A dispatch pass runs after every intake, at the
end of every document task, and from beat every 30 seconds.

### 9.1 Cutover order (the 3d rollout, and any fresh environment)

1. Back up and apply `0035` (section 1; `backup_0035`: documents,
   founder_alerts, email_outbox).
2. Deploy the worker with its `beat` process (9.3).
3. Then deploy the API, which stops sending the document task.

An API running without a dispatcher behind it leaves every new document
waiting. `dispatcher_stopped` says so within `DISPATCHER_STALE_MIN` (10
minutes), and `/healthz` shows `"stale": true`.

### 9.2 Adding worker capacity

`DISPATCH_IN_FLIGHT_TARGET` (a setting, staging 1) is the worker's document
slots. The worker's Celery concurrency is set from it, plus one process the
dispatcher never fills (so the dispatch pass and the sweeps run during a long
order). With one worker machine:
1. Set `DISPATCH_IN_FLIGHT_TARGET` to the new number of slots, on the worker
   app AND the API app (the API doesn't use it today, but keep them equal).
2. Size the machine for slots + 1 processes (each document process may reach
   `WORKER_MAX_MEMORY_PER_CHILD_KIB` before it is replaced).
3. Deploy the worker.

More than one worker machine is a Phase 6 decision. Until then,
`DISPATCH_IN_FLIGHT_TARGET` must equal one machine's slots. The dispatcher
would otherwise keep more documents in flight than any worker can take.

### 9.3 Beat: exactly one, never two at once

Beat is its own process, never `celery worker -B`. Locally it is the
README's second terminal. On Fly it is the worker app's `beat` process group
(`apps/worker/fly.toml`):
- **Never scale `beat` above 1:** `fly scale count worker=1 beat=1`.
- **Never deploy the worker app with `--strategy canary` or `bluegreen`.**
  Both start a new machine beside the old one, which means two beats for a
  while. `fly.toml` pins `strategy = "rolling"`, which updates the machine in
  place.
- **Run only one stack (local or Fly) against staging at a time.** Two
  dispatchers on one database would share the in-flight count but send to
  two different queues.

If two beats ever do run, nothing breaks. Every beat task is safe to run
twice (the table in BUILD-STATUS "3d -- APPROVED WITH CHANGES", change C):
scheduled jobs are claimed with `SKIP LOCKED`, the rollup upserts, the
lifecycle sweep holds a row lock across Stripe, the stuck sweep is
compare-and-set, and dispatch passes take an advisory lock.

### 9.4 The external uptime monitor (from Phase 6)

The dispatcher and its own stale check run in the worker, so a dead worker
machine can't alert anyone itself. Point an uptime monitor at the API's
`GET /healthz` (no sign-in) and alert when the body does **not** contain
`"stale": false`. The response is always HTTP 200, so the monitor must match
the body, not the status. The Console's health strip shows the same
heartbeat, red past `DISPATCHER_STALE_MIN`.

### 9.5 When the founder gets one of these alerts

- **`model_api_failure`** (high, no tenant): the model provider is marked
  down. Nothing is failed and nothing is sent except one probe every
  `PROVIDER_PROBE_MINUTES` (2). Customers see **Delayed** (DOC-023). Its
  `cause`:
  - `our_configuration`: the API key, the account, the credit or the model.
    Its `last_error` names it: `http_401` key, `http_402` billing or credit,
    `http_403` access, `http_404` the model retired or renamed,
    `http_400_own_spend_limit` our own spend limit in the Console,
    `http_429_spend_cap` the tier's monthly cap. Fix it in the Anthropic
    Console. A retired model needs a code change (the model IDs are still
    constants, review M10). The next probe after the fix recovers.
  - `5xx`, `overloaded`, `rate_limited`, `network`: the provider's side.
    Check status.anthropic.com. Nothing to do: it recovers on the first
    probe that succeeds.
  Documents still waiting after `PROVIDER_MAX_WAIT_HOURS` (6) fail with
  **DOC-024** (a `document_failed` alert per tenant per day). The customer is
  told to upload again or enter the order by hand.
- **`model_api_recovered`** (info): the first success after an outage. It
  shows how long it lasted, how many documents waited and how many reached
  the 6-hour limit. The backlog goes out at once, in turns. Acknowledge
  the `model_api_failure` alert yourself; recovery doesn't.
- **`dispatcher_stopped`** (high, at most one an hour): documents are
  waiting and no dispatch pass has run for `DISPATCHER_STALE_MIN`. Check
  that the worker and the `beat` process are running (`fly status --app
  docflow-worker-staging`), and the Upstash queue. Documents resume by
  themselves once both run.
- **`routing_model_failure`** (warning, once a UTC day): the cheaper routing
  model refused DocFlow (same causes as `our_configuration` above).
  Documents are still read, but without approved examples.
  `documents_without_examples` on the Console's alert counts them since the
  day's first failure (the email has the count when it was raised).
- **`document_stuck` with `lost_jobs_returned`**: documents were sent to the
  queue and never claimed (a queue or broker problem). They are back to
  waiting and go out again by themselves.

### 9.6 The constants (`packages/core/docflow_core/constants.py`)

| Constant | Value | What it does |
|---|---|---|
| `TENANT_IN_FLIGHT_CAP` | 2 | Most in flight for one tenant while another has something ready |
| `INTERACTIVE_BATCH_MAX` | 10 | An upload or release of up to this many goes in the interactive lane |
| `DISPATCH_INTERVAL_SECONDS` | 30 | Beat's backstop pass |
| `DISPATCHER_STALE_MIN` | 10 | Heartbeat age that means stopped |
| `PROVIDER_RETRY_MINUTES` | 1, 2, 4, 8, 15 | Provider wait backoff (then every 15) |
| `PROVIDER_MAX_WAIT_HOURS` | 6 | Then DOC-024 |
| `PROVIDER_DOWN_FAILURES` / `PROVIDER_DOWN_WINDOW_MIN` | 3 / 5 | Marked down (our own configuration: at the first) |
| `PROVIDER_PROBE_MINUTES` | 2 | One probe while down |
| `STORAGE_WAIT_RETRY_MINUTES` | 5 | Storage wait, no maximum |
| `PARSE_SERVICE_WAIT_RETRY_MINUTES` | 2 | Parse-service wait, no maximum |
