# Walkthroughs: the whole app, by hand, before Phase 6

One checklist per phase or slice. Walked in order, they cover everything built
so far, from sign-in to example prompting. They are the manual half of QA and
UAT: the automated suites prove each layer, and these prove the product works
for a person sitting in front of it. Every walkthrough since Phase 3 has found
at least one defect no test caught.

Record pass / fail in each file's **Results** table as you go. Anything odd,
even cosmetic, tell me: I log it in `DECISIONS.md`, fix it, and update
`docs/BUILD-STATUS.md`.

## Order

| # | File | What it covers | Time |
|---|---|---|---|
| 1 | [0-foundations.md](0-foundations.md) | Sign-in, no signup, the Console wall, tenant isolation | 15 min |
| 2 | [1-intake-extraction.md](1-intake-extraction.md) | Every file format, hostile files, extraction, prompt injection | 40 min |
| 3 | [2-matching.md](2-matching.md) | Buyers, SKU matching, the learning loop, checks, duplicates | 25 min |
| 4 | [3-review.md](3-review.md) | The review screen, the 2-minute golden fixture, approval and history | 30 min |
| 5 | [4-export.md](4-export.md) | CSV, Excel, JSON, QuickBooks IIF; byte-identical files | 20 min |
| 6 | [5.1-console-foundations.md](5.1-console-foundations.md) | Intake staging, plans, the outbox, alerts, invites | 20 min |
| 7 | [5.2-catalog-import.md](5.2-catalog-import.md) | Catalog and customer-list import | 25 min |
| 8 | [5.3-test-batch-go-live.md](5.3-test-batch-go-live.md) | Deal terms, test batch, acting-as review, go-live | 35 min |
| 9 | [5.4-operator-screens.md](5.4-operator-screens.md) | Buyer merge, learned rules, field settings | 25 min |
| 10 | [5.5-dashboard.md](5.5-dashboard.md) | Attention panel, health strip, tenant list, KPI cards | 20 min |
| 11 | [5.6-lifecycle.md](5.6-lifecycle.md) | Cancel, suspend, reactivate, delete (walked 23 Sept) | 30 min |
| 12 | [5.7-allowance-quarantine.md](5.7-allowance-quarantine.md) | Allowances, abuse ceilings, held documents (walked 23 Sept) | 40 min |
| 13 | [5.8abc-tenant-surface.md](5.8abc-tenant-surface.md) | Upload page, navigation, customer dashboard, roles, Activity, digest | 30 min |
| 14 | [5.8d-team.md](5.8d-team.md) | The Team page (walked 24 Sept) | 20 min |
| 15 | [5.9-billing.md](5.9-billing.md) | Plan change through Stripe test mode (walked 24 Sept) | 10 min |
| 16 | [5.10-example-prompting.md](5.10-example-prompting.md) | Approved-example prompting, the Audit tab | 15 min |

About six hours in all. Stop at any file boundary. Walkthroughs 6–8 build one
new tenant, **Acme Test UAT Supply**, and later files use it, so do those three
in order.

Model calls in these walkthroughs cost real money, but not much: about $1 in
total, mostly Phase 1's 21 sample orders. Stripe runs in test mode only.

## Before any of them

1. **Start the four processes.** API (port 8000), web (3000), Celery worker and
   Celery beat. The commands are under "Running locally" in the repo's
   `README.md`, or ask me to start them.
2. **Browse to `http://localhost:3000`**, not `127.0.0.1`. On `127.0.0.1` the
   page draws but never becomes clickable.
3. **Make the test files** (once). From the repo folder:

   ```
   apps\worker\.venv\Scripts\python.exe scripts\make_walkthrough_files.py
   ```

   This writes `DocFlow\walkthrough-files\` beside `catalog-samples\`: 21 good
   orders (one per format), 15 hostile files, and a few extras the later
   walkthroughs name. It prints what the upload check will say about each.
4. **Logins you'll use:**
   - **You, the founder.** Your own account; it lands in the Console.
   - **`walkthrough@example.test`**, a reviewer on **Acme Test Distributor**.
     Walkthrough 0 sets its password.
   - **Acme Test Prospect's owner and reviewer.** Your `+acmetest` and
     `+reviewer2` Gmail addresses, with the passwords you chose on the 5.8
     walks.
   - **The owner of Acme Test UAT Supply**, created in walkthrough 6.
5. **Use a private (incognito) window for the second person.** Two different
   sign-ins can't share one browser window.

## Known before you start

- **All email is held in the Console Outbox.** No email provider is set up
  yet, so invites, notices and digests wait there with their text and links
  shown. Held is expected, not a fault.
- **Staging holds leftovers from earlier test runs.** There are three tenants
  named "Acme Test Distributor". The real one has 20+ orders and the
  `walkthrough@example.test` login; the other two are empty leftovers. Cleanup
  comes after the walkthroughs, as agreed.
- **Fixed on 25 Sept, before you walk them** (D-144 – D-147), and written into
  the walkthroughs: decided orders can be reopened for review; a failed order
  says why; failures whose message says "DocFlow has been alerted" now really
  raise that alert; the Console's tenant page links to its orders; a damaged
  `.xls` / `.msg` is called corrupted, not "PowerPoint or Visio".
