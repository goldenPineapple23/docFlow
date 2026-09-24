-- DocFlow — Phase 5, slice 5.9: when each customer's founding price ends
--
-- Recorded as DECISIONS.md D-139.
--
-- MRR now counts what customers actually pay (founder, 2026-09-24): a founding
-- customer at the tier's founding price until their 90 days are over
-- (docflow-pricing.docx: "Founding customer promo (first 90 days)"), then the
-- list price. The dashboard adds MRR up across every tenant and must not call
-- Stripe once per tenant to do it, so the end date is kept here. DocFlow sets
-- it itself at the three moments it creates or changes a founding discount:
-- go-live, a plan change (D-138), and reactivation (which clears it -- the
-- founding price is a go-live perk).
--
-- Additive: one nullable column, plus a backfill for founding customers who
-- are already live. Their Stripe discount was created at go-live as a coupon
-- repeating for ceil(promo_days / 30) months (docflow_core.onboarding
-- promo_months), so that is when it ends -- unless the tenant was reactivated,
-- which starts a subscription with no founding discount. No other data is
-- touched.
--
-- Safe to run once on docflow-staging via the Supabase SQL Editor.

alter table tenants add column founding_price_ends_at timestamptz;

update tenants t
   set founding_price_ends_at =
       t.went_live_at + make_interval(months => ceil(tr.promo_days / 30.0)::int)
  from tiers tr
 where tr.id = t.tier_id
   and t.founding_price
   and t.went_live_at is not null
   and t.stripe_subscription_id is not null
   and tr.promo_days is not null
   and tr.promo_monthly_price is not null
   -- Reactivation starts a new subscription without the founding discount.
   and not exists (
       select 1 from tenant_lifecycle_events e
        where e.tenant_id = t.id and e.event_type = 'reactivated'
   );
