import { expect, test, type Page } from "@playwright/test";

/**
 * Deal terms at Create tenant (D-117): the price agreed before onboarding is
 * recorded here, from the tiers and presets tables, so go-live never asks.
 * Drives the form the founder uses and checks exactly what it sends.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const TIERS = [
  { id: "t1", code: "starter", version: 1, name: "Starter", monthly_price: "299.00", promo_monthly_price: "199.00", promo_days: 90, document_allowance: 300 },
  { id: "t2", code: "growth", version: 1, name: "Growth", monthly_price: "399.00", promo_monthly_price: "249.00", promo_days: 90, document_allowance: 1000 },
  { id: "t3", code: "scale", version: 1, name: "Scale", monthly_price: "599.00", promo_monthly_price: "349.00", promo_days: 90, document_allowance: 3000 },
];

const PRESETS = [
  { id: "p1", code: "founding", version: 1, name: "Founding customer", description: "Early-adopter discount, first cohort", default_amount: "750.00", min_amount: "750.00", max_amount: "750.00", note_required: false },
  { id: "p2", code: "standard", version: 1, name: "Standard", description: "Typical new customer", default_amount: "1500.00", min_amount: "1500.00", max_amount: "1500.00", note_required: false },
  { id: "p3", code: "complex", version: 1, name: "Complex", description: "Large or messy catalog", default_amount: "2000.00", min_amount: "2000.00", max_amount: "2500.00", note_required: false },
  { id: "p4", code: "waived", version: 1, name: "Waived", description: "No setup fee", default_amount: "0.00", min_amount: "0.00", max_amount: "0.00", note_required: true },
  { id: "p5", code: "custom", version: 1, name: "Custom", description: "Any other agreed amount", default_amount: null, min_amount: "0.00", max_amount: null, note_required: true },
];

async function stubApi(page: Page, sent: unknown[]) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tiers`, (route) => route.fulfill({ json: { tiers: TIERS } }));
  await page.route(`${API}/admin/setup-fee-presets`, (route) => route.fulfill({ json: { presets: PRESETS } }));
  await page.route(`${API}/admin/intakes`, (route) => route.fulfill({ json: { intakes: [] } }));
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
  await page.route(`${API}/admin/tenants/new`, (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill({ status: 422, json: { detail: { code: "ONB-012", title: "Stubbed", message: "Stubbed.", action: "Stubbed.", severity: "warning" } } });
  });
}

async function fillBasics(page: Page) {
  await page.getByTestId("tenant-name").fill("Acme Test Distributor");
  await page.getByTestId("tenant-owner-email").fill("owner@example.test");
}

test("ticking Founding customer records the founding price and the $750 founding fee", async ({ page }) => {
  const sent: unknown[] = [];
  await stubApi(page, sent);
  await page.goto("/admin/tenants/new");
  await fillBasics(page);

  await expect(page.getByTestId("deal-preset-standard")).toBeChecked();
  await expect(page.getByText("Complex — $2,000–$2,500")).toBeVisible();
  await page.getByTestId("deal-founding").check();
  await expect(page.getByTestId("deal-preset-founding")).toBeChecked();
  await expect(page.getByText("$249/month for the first 3 months, then $399; $750 setup fee")).toBeVisible();

  await page.getByTestId("tenant-create").click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({
    tier: "growth",
    deal: {
      tier: "growth",
      setup_fee_preset: "founding",
      setup_fee_amount: "750.00",
      setup_fee_billing: "stripe",
      founding_price: true,
    },
  });

  // Unticking goes back to Standard rather than leaving the founding fee behind.
  await page.getByTestId("deal-founding").uncheck();
  await expect(page.getByTestId("deal-preset-standard")).toBeChecked();
});

test("a Complex fee takes an amount in its range; Waived needs a reason and hides billing", async ({ page }) => {
  const sent: unknown[] = [];
  await stubApi(page, sent);
  await page.goto("/admin/tenants/new");
  await fillBasics(page);

  await expect(page.getByTestId("deal-amount")).toHaveCount(0); // fixed presets have no amount box
  await page.getByTestId("deal-preset-complex").check();
  await expect(page.getByText("Between $2,000 and $2,500")).toBeVisible();
  await page.getByTestId("deal-amount").fill("2350");
  await page.getByTestId("tenant-create").click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ deal: { setup_fee_preset: "complex", setup_fee_amount: "2350" } });
  // The server's refusal is shown from the catalog.
  await expect(page.getByText("Stubbed").first()).toBeVisible();

  await page.getByTestId("deal-preset-waived").check();
  await expect(page.getByTestId("deal-manual")).toHaveCount(0);
  await expect(page.getByTestId("deal-note")).toHaveAttribute("required", "");
});
