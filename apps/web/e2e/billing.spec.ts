import { expect, test, type Page } from "@playwright/test";

/**
 * The Console's Billing card and plan change (slice 5.9, D-138) against a
 * stubbed API: the card summarises what DocFlow knows and links to Stripe; the
 * founder picks a plan, reads what will happen, and only then confirms. The
 * rules themselves are proven against real Postgres in
 * apps/api/tests/test_billing_api.py and the Stripe side in
 * packages/core/tests/test_tier_change_stripe.py.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const T = "00000000-0000-4000-8000-00000000b111";

const TIERS = [
  { id: "t1", code: "starter", version: 1, name: "Starter", monthly_price: "299.00", promo_monthly_price: "199.00", promo_days: 90, document_allowance: 300 },
  { id: "t2", code: "growth", version: 1, name: "Growth", monthly_price: "399.00", promo_monthly_price: "249.00", promo_days: 90, document_allowance: 1000 },
  { id: "t3", code: "scale", version: 1, name: "Scale", monthly_price: "599.00", promo_monthly_price: "349.00", promo_days: 90, document_allowance: 3000 },
];

function tenant(overrides: Record<string, unknown> = {}) {
  return {
    id: T,
    name: "Acme Test Billing",
    primary_currency: "USD",
    timezone: "America/Chicago",
    status: "active",
    onboarding_status: "live",
    went_live_at: "2026-09-20T12:00:00Z",
    stripe_customer_id: "cus_test_1",
    stripe_subscription_status: "trialing",
    created_at: "2026-09-10T12:00:00Z",
    tier_code: "starter",
    tier_name: "Starter",
    tier_version: 1,
    tier_monthly_price: "299.00",
    tier_promo_monthly_price: "199.00",
    tier_promo_days: 90,
    tier_document_allowance: 300,
    // What go-live billed; Deal terms keeps showing it after a plan change.
    golive_tier_name: "Starter",
    golive_tier_version: 1,
    golive_monthly_price: "299.00",
    golive_promo_monthly_price: "199.00",
    golive_promo_days: 90,
    setup_fee_preset_name: "Founding customer",
    setup_fee_amount: "750.00",
    setup_fee_billing: "stripe",
    founding_price: true,
    owner: null,
    intake_address: "orders+x@intake.example.test",
    intake_address_active: true,
    open_merge_candidates: 0,
    learned_rules: 0,
    billing: {
      subscription_id: "sub_1",
      status: "trialing",
      current_period_end: null,
      first_past_due_at: null,
      trial_ends_at: "2026-09-27T12:00:00Z",
      founding_price_ends_at: "2099-12-20T12:00:00Z",
      stripe_dashboard_url: "https://dashboard.stripe.com/test/customers/cus_test_1",
    },
    ...overrides,
  };
}

async function stub(page: Page, current: () => Record<string, unknown>, onChange: (body: unknown) => void) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants/${T}/overview`, (route) => route.fulfill({ json: { tenant: current() } }));
  await page.route(/\/admin\/outbox/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { emails: [] } }) : route.continue(),
  );
  await page.route(`${API}/admin/tenants/${T}/test-batch`, (route) => route.fulfill({ json: { documents: [] } }));
  await page.route(`${API}/admin/tenants/${T}/go-live`, (route) => route.fulfill({ json: {} }));
  await page.route(`${API}/admin/tiers`, (route) => route.fulfill({ json: { tiers: TIERS } }));
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
  await page.route(`${API}/admin/tenants/${T}/tier`, (route) => {
    onChange(route.request().postDataJSON());
    return route.fulfill({
      json: {
        change: {
          before: { tier: "starter", version: 1, monthly_price: "299.00", document_allowance: 300 },
          after: { tier: "growth", version: 1, monthly_price: "399.00", document_allowance: 1000 },
          founding_months_carried: 2,
          subscription_status: "trialing",
        },
      },
    });
  });
}

test("the billing card shows the free week and links to the customer in Stripe", async ({ page }) => {
  await stub(page, () => tenant(), () => {});
  await page.goto(`/admin/tenants/${T}`);

  const card = page.getByTestId("billing-card");
  await expect(card.getByTestId("billing-status")).toHaveText("Free week");
  await expect(card).toContainText("Free week ends");
  await expect(card).toContainText("$750.00 · on the first Stripe invoice");
  // What a founding customer actually pays, not the list price.
  await expect(card.getByTestId("founding-until")).toContainText("$199.00/month founding price until");
  await expect(card.getByTestId("founding-until")).toContainText("then $299.00/month");
  await expect(card.getByTestId("stripe-link")).toHaveAttribute(
    "href",
    "https://dashboard.stripe.com/test/customers/cus_test_1",
  );
});

test("changing plan explains what will happen, then changes only on confirm", async ({ page }) => {
  let tier = "starter";
  const sent: unknown[] = [];
  await stub(
    page,
    () =>
      tier === "starter"
        ? tenant()
        : tenant({
            tier_code: "growth",
            tier_name: "Growth",
            tier_monthly_price: "399.00",
            tier_promo_monthly_price: "249.00",
            tier_document_allowance: 1000,
          }),
    (body) => {
      sent.push(body);
      tier = "growth";
    },
  );
  await page.goto(`/admin/tenants/${T}`);

  // The choices show the founding price a founding customer would pay.
  await page.getByTestId("plan-change-open").click();
  await expect(page.getByRole("radio", { name: /Growth .* \$249\.00\/month founding price, then \$399\.00/ })).toBeVisible();
  // The current plan can't be "changed" to itself.
  await page.getByRole("radio", { name: /Starter/ }).check();
  await expect(page.getByTestId("plan-change-confirm")).toBeDisabled();

  await page.getByRole("radio", { name: /Growth/ }).check();
  const preview = page.getByTestId("plan-change-preview");
  await expect(preview).toContainText(
    "From Starter ($199.00/month founding, 300 documents) to Growth ($249.00/month founding, then $399.00, 1,000 documents)",
  );
  await expect(preview).toContainText("prorates the price difference onto the next invoice");
  await expect(preview).toContainText("founding price for whatever is left of their 90 days");
  expect(sent).toEqual([]); // nothing has happened yet

  await page.getByTestId("plan-change-confirm").click();
  await expect(page.getByTestId("plan-change-done")).toContainText("Moved to Growth.");
  await expect(page.getByTestId("plan-change-done")).toContainText("Founding price kept for 2 more months.");
  expect(sent).toEqual([{ tier: "growth" }]);
  await expect(page.getByTestId("billing-card")).toContainText("Growth (v1) — $249.00/month founding price");
  // The deal agreed at go-live is not rewritten by the plan change.
  await expect(page.getByTestId("deal-summary")).toContainText("Starter at");
});

test("a tenant that isn't live has no plan-change button", async ({ page }) => {
  await stub(
    page,
    () =>
      tenant({
        onboarding_status: "test_batch_complete",
        stripe_subscription_status: null,
        billing: {
          subscription_id: null,
          status: null,
          current_period_end: null,
          first_past_due_at: null,
          trial_ends_at: null,
          stripe_dashboard_url: "https://dashboard.stripe.com/test/customers/cus_test_1",
        },
      }),
    () => {},
  );
  await page.goto(`/admin/tenants/${T}`);
  await expect(page.getByRole("heading", { name: "Acme Test Billing" })).toBeVisible();
  await expect(page.getByTestId("plan-change-open")).toHaveCount(0);
});
