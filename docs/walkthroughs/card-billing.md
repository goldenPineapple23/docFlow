# Walkthrough — card billing with a 7-day trial (Stripe test mode)

For: the founder, with me. About 45 minutes. Everything happens in Stripe
**test mode** in the "Statim Systems sandbox" account, with fake test cards.
No real money moves. Decisions and design: DECISIONS.md D-181.

## Before we start

1. `0031` is applied to staging (the PR's steps), and the staging suites are green.
2. **Stripe CLI**, so Stripe's test events reach DocFlow on this machine:
   - Install: `winget install Stripe.StripeCLI` (or download it from
     https://docs.stripe.com/stripe-cli).
   - `stripe login`, and pick **Statim Systems sandbox** (the account whose
     id is `acct_1UGLsPAtwYQFQd7S`).
   - `stripe listen --forward-to localhost:8000/webhooks/stripe` and leave it
     running. It prints a signing secret starting `whsec_`. Put it in the root
     `.env` as `STRIPE_WEBHOOK_SECRET=whsec_...` (it's a test-mode secret for
     this machine only), then tell me: I restart the API.
3. I start the API and the web app from the card-billing branch, and prepare
   two tenants whose test batch is complete (so Go live is available): one
   **founding** and one **standard**.

Test cards (any future expiry, any CVC, any ZIP):

| Card | What it does |
|---|---|
| `4242 4242 4242 4242` | Always works |
| `4000 0000 0000 0341` | Saves fine, then every charge fails |
| `4000 0000 0000 0002` | Declined straight away |

The Console Outbox holds every email (no email provider yet). The owner's
invite link is there too; open it in a private window to sign in as the owner.

## Part 1 — A standard customer pays the setup fee at signing

1. Console → the standard tenant → Overview → Go live panel. **Expect** "By
   card" selected, "Card on file: not yet", "Setup fee paid when the card was
   added ($1,500.00): not yet", and an **Ask for a card** button.
2. Click **Ask for a card**. **Expect** "Sent to the owner — it waits in the
   Outbox". In the Outbox, the email says the setup fee ($1,500.00) is charged
   when the card is added, and links to `/billing`.
3. As the owner, open `/billing`. **Expect** "No card is on file yet. Adding
   one charges your setup fee ($1,500.00) now."
4. **Add a card** → Stripe's page, showing $1,500.00 → use `4000 0000 0000
   0002`. **Expect** Stripe says the card was declined and nothing is charged.
   Try again with `4242 4242 4242 4242`.
5. Back on `/billing` (refresh after a few seconds). **Expect** "A card is on
   file with Stripe." and "Your setup fee is paid."
6. Console → the tenant's Billing card. **Expect** the card on file and "setup
   fee paid". In Stripe (Open in Stripe ↗), one $1,500.00 payment.
7. Console → Deal terms: change the setup fee to Complex. **Expect ONB-017**
   "The setup fee is already paid", and nothing changes. Changing only the
   plan still works.

## Part 2 — A founding customer: nothing charged until the trial ends

1. The founding tenant → **Ask for a card**. **Expect** the Outbox email says
   nothing is charged now, and the setup fee ($750.00) comes with the first
   month when the trial ends.
2. As its owner, `/billing`. **Expect** "Adding one charges nothing now."
3. **Add a card** with `4000 0000 0000 0341` (it saves, but later charges
   fail — we need that in Part 4). **Expect** Stripe's page asks for no
   amount, and `/billing` then shows the card on file.

## Part 3 — Going live by card

1. The founding tenant → Go live panel, **By invoice** then back to **By
   card**. **Expect** "Card on file: yes".
2. **Go live…** **Expect** the summary says Stripe charges the card on file
   for the first month plus the $750.00 setup fee after 7 days — no
   "invoice", no due date. **Confirm** (with the authenticator code).
3. **Expect** in Stripe: a subscription, **Trialing**, "Charge
   automatically", with a pending $750.00 setup-fee item. In the Outbox, the
   go-live email says "The card on file is charged when it ends."
4. (Optional) The standard tenant: try **Go live** before its fee is paid on
   a fresh standard tenant. **Expect ONB-016.**

## Part 4 — A payment fails, then the card is updated

1. In Stripe, open the founding tenant's subscription → **Actions** → end the
   trial now (Stripe asks to confirm). Stripe charges the `…0341` card, which
   fails.
2. **Expect**, within a minute:
   - Console attention panel: "A tenant's Stripe subscription is past due".
   - Outbox: "Your DocFlow payment didn't go through" to the owner, naming the
     amount, "may be paused from" a date 14 days out — never "will pause".
   - As the owner, the Dashboard and `/billing` show the amber banner:
     "Your last payment didn't go through", with the same date.
   - A reviewer on the same account sees no banner and can't open `/billing`.
3. As the owner, `/billing` → **Update card** → Stripe's page → `4242 4242
   4242 4242`.
4. **Expect**, within a minute, without waiting for Stripe's next retry: the
   invoice is **Paid** in Stripe, the subscription **Active**, and the banner
   gone.

## Part 5 — What I show you from the tests (no clicking)

- The reminder three days before the date, and that it isn't sent once the
  payment has gone through.
- The banner switching to "Your payment is overdue, and processing may be
  paused." after the date.
- A second setup-fee payment, or a wrong amount, raising a founder alert
  instead of being recorded as paid.
- A reactivation with a declined card refused (BIL-007), the tenant still
  suspended.

## Results

| Part | Step | Pass / fail | Notes |
|---|---|---|---|
| | | | |
