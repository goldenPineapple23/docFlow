# Card billing with a 7-day trial

Moved here word for word from `docs/BUILD-STATUS.md` on 2026-10-05 (the founder's context
housekeeping, D-191). Nothing below the marker was edited: it is the text as it stood under
"Stage 3 -- agreed with the founder before building", built 2026-09-29, between 3a and 3b (D-181). It keeps its
original wording, including statuses that were true when each part was written.

Other files cite these sections as BUILD-STATUS "<heading>". Each cited heading is still in
`docs/BUILD-STATUS.md`, as a one-line stub pointing here. "Above" and "below" in this text
refer to the order the blocks had there: 3a, 3b, 3c, 3d, card billing, 3e.

<!-- moved text starts on the next line -->
**Card billing with a 7-day trial -- required before the first pilot.
DECIDED (founder, 2026-09-29): D1, D3, D4, D5 and D6 as recommended below;
D2 split by customer type (below). D4's banner wording goes to the founder
before it is added. Build order: after the 3a PR merges and before 3b,
including a walkthrough in Stripe test mode.** **BUILT 2026-09-29 -- as built,
with the founder's follow-up answers and what Stripe test mode showed, in
DECISIONS.md D-181.** `0031` waits on the founder's backup and staging apply. The founder's pilot model is "customer signs, 7 days free, then Stripe charges
their card automatically". Today nothing charges a card:
- Go-live creates a `send_invoice` subscription with a 7-day trial (D-125).
  At the trial's end Stripe emails an invoice (month one plus the setup fee,
  D-113), payable within `INVOICE_DAYS_UNTIL_DUE`.
- The customer pays it on Stripe's invoice page. Unpaid, the subscription
  goes `past_due` only when the due date passes.
- Suspension is always the founder's decision: a `non_payment` cancel whose
  effective date is the first `past_due` notice plus `CURE_PERIOD_DAYS` (0,
  D-125).

What switching to `charge_automatically` would take (facts from Stripe's
documentation, read 2026-09-29; the items marked *verify* get checked in test
mode before building):

1. **Collecting the card.** There is no public signup (Section 3). The owner
   exists from tenant creation and is invited. Card details never touch
   DocFlow: Stripe's hosted pages collect them, and Stripe saves the card on
   the tenant's existing Stripe customer. Two hosted options, used together:
   - **Stripe Checkout in setup mode** for the first card. DocFlow creates the
     session server-side and sends the link with the invite or the go-live
     email. A webhook (`checkout.session.completed`) sets the card as the
     customer's default and records it on the tenant (*verify* the exact
     event fields).
   - **Stripe's Customer Portal** for later changes: an "Update card" link on
     the owner's billing page (owner and admin only). The portal must first
     be configured once in the Stripe dashboard.
   - **Decision D1: when is a card required?**
     - (i) **Recommended: before Go live is enabled.** Step 9's gate gains
       "card on file", so every trial ends with a card to charge.
     - (ii) The trial starts without a card. Stripe supports that, and ends
       the trial by pausing or cancelling the subscription if no card has
       been added. DocFlow would need a "trial ended with no card" alert, and
       its handling of a `paused` subscription.
2. **The trial.** Unchanged: `trial_end` 7 days after go-live. Stripe can
   send its own trial-ending reminder, and emits
   `customer.subscription.trial_will_end` three days before. The
   subscription is `trialing` until the first successful charge, then
   `active`. MRR already shows trials beside it, not in it (D-135).
3. **The setup fee. Decision D2 -- decided (founder, 2026-09-29): it depends
   on the customer.** Both paths:
   - **Founding customers: charged with month one when the trial ends.**
     - The card page is Stripe Checkout in **setup mode**: it saves the card
       and charges nothing.
     - At go-live the setup fee is added as a pending invoice item, exactly as
       today. Stripe puts it on the first invoice at the trial's end and
       charges it with month one, in one charge.
     - If that charge fails, the normal failed-payment path applies (item 4).
   - **Standard customers: charged at signing.**
     - "Signing" is the customer completing the card page DocFlow sends them.
       That page is Stripe Checkout in **payment mode**, for the tier's setup
       fee, and it saves the same card for later charges (*verify* the exact
       parameters in test mode).
     - A webhook records that the fee is paid and the card is on file.
     - At go-live the subscription starts with the 7-day trial and **no**
       setup-fee item, because it's already paid.
     - A declined card: Stripe's page tells the customer, and nothing is
       charged or saved. The same link can be used again, or re-sent.
   - **What this changes in the flow:**
     - Founding or standard has to be known when the card page is sent, not
       only at go-live, where the founding choice is made today. The Console
       action that sends the card link asks for it, and the go-live form shows
       what was chosen.
     - The go-live gate (D1) is "card on file" for founding customers, and
       "card on file and setup fee paid" for standard customers. Either gate
       is met by `invoiced_manually`, which stays available for both.
     - The fee amount always comes from the tenant's tier version, never
       typed in (Section 7.15.2).
   - **For the founder, not blocking:** if a standard customer pays at
     signing and never goes live, any refund is the founder's decision,
     made in Stripe. DocFlow does nothing automatically.
4. **When a charge fails.** The subscription goes `past_due` on the **first**
   failed charge. That's day 7, not day 22 as with invoices due in 15 days.
   - **Retries:** Stripe retries on the retry schedule set in the dashboard.
   - **Customer emails:** Stripe can email the customer about a failed charge
     or an expiring card (dashboard settings). For a card that needs 3D
     Secure off-session, it can email a link to authenticate.
   - **After the final retry**, Stripe does what the dashboard says: cancel,
     mark `unpaid`, or leave `past_due`. **It must not be "cancel".** A
     Stripe-side cancel bypasses DocFlow's lifecycle and leaves an active
     DocFlow tenant with no billing. This is already an open item: set it to
     "leave past due" before the first real customer.
   - **The founder sees** the existing `stripe_subscription_past_due` alert,
     as today.
   - **Decision D4: what the tenant sees.** Today, nothing in the product.
     Proposed: a banner for owners and admins while the subscription is
     `past_due`: the last payment didn't go through, update your card
     (portal link). It needs a new catalog entry, whose wording goes to the
     founder first.
   - **Decision D3: when suspension can start.** Unchanged in mechanism: the
     founder confirms a `non_payment` cancel. But with `CURE_PERIOD_DAYS = 0`
     the computed date is the first failed charge, while Stripe is still
     retrying. **Recommended: set `CURE_PERIOD_DAYS` to cover the retry
     window**, e.g. 14 days; the ToS placeholder is 15.
5. **How it meets what 3a built:**
   - **Suspension's cancel:** cancelling a subscription also stops Stripe
     collecting its unpaid invoices (documented), so no retry charges a
     suspended customer. **An owed cancel matters more with cards:** until it
     goes through, Stripe charges the card, not just emails an invoice. The
     sweep retries it every tick (D-179), and the reactivation invoice alert
     lists anything charged.
   - **Reactivation reusing a live `past_due` subscription:** Stripe keeps
     retrying its open invoices. **The card may be charged for the
     before-suspension invoices straight after reactivation.** The alert
     lists them. If the founder wants them voided first, that's done in
     Stripe before clicking Reactivate.
   - **Reactivation with a new subscription** (the old one cancelled): the
     saved card stays on the Stripe customer (cancelling a subscription
     doesn't remove it). With no trial on reactivation (D-125), the first
     month is charged at once.
   - **Decision D5: what if that first charge fails?**
     - **Recommended: refuse the reactivation.** Stripe's
       `payment_behavior=error_if_incomplete` (*verify*) makes the create
       fail. The tenant stays suspended, and the founder sees a catalog
       message saying the card was declined.
     - The alternative, an `incomplete` subscription on an active tenant,
       would lapse to `incomplete_expired` after 23 hours.
6. **Decision D6: can both methods coexist, per tenant?** Yes.
   `collection_method` belongs to each Stripe subscription, not the account.
   A subscription can be switched later (only invoices created after the
   switch use the new method, and it needs a default card first).
   - **Recommended:** a `billing_method` on the tenant (`card` | `invoice`),
     chosen at go-live, with card the default for pilots. Invoice billing
     stays for a customer who pays by bank transfer.
   - Reuse, the owed cancel and the invoice alert work the same for both.
7. **What building it touches:**
   - one migration (`tenants.billing_method`, and when a card was put on
     file);
   - `external_services`: Checkout and portal sessions, the collection method
     and payment behaviour on create;
   - the go-live gate;
   - an owner billing page (update card; the past-due banner);
   - webhook handling for the new events;
   - catalog entries (card needed, payment failed, reactivation declined);
   - Stripe dashboard settings (retry schedule, customer emails, after the
     final retry = leave past due, the portal);
   - tests, including a walkthrough in Stripe test mode with Stripe's test
     cards (one that is declined, one that needs authentication).
   About the size of slice 5.9. Stripe charges its standard card fees; no
   new service is needed.

