import { expect, test, type Page } from "@playwright/test";

/**
 * The import screen must only ever show the tenant it is on. The founder
 * saw the same "Earlier imports" under three tenants when only two had any
 * (D-110). The API was proven correct against the real database; this
 * drives the browser path the founder takes between tenants.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const NAMES: Record<string, string> = { [A]: "Acme Test Alpha", [B]: "Acme Test Beta" };

function overview(id: string) {
  return {
    id,
    name: NAMES[id],
    primary_currency: "USD",
    timezone: "America/New_York",
    status: "active",
    onboarding_status: "tenant_created",
    went_live_at: null,
    invite_sent_at: null,
    intake_address_active: false,
    stripe_customer_id: null,
    stripe_subscription_status: null,
    created_at: "2026-09-18T10:00:00Z",
    onboarding_intake_id: null,
    tier_code: null,
    tier_name: null,
    tier_version: null,
    tier_monthly_price: null,
    tier_document_allowance: null,
    owner: null,
    intake_address: null,
  };
}

async function stubApi(page: Page) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants`, (route) =>
    route.fulfill({
      json: [A, B].map((id) => ({
        id,
        name: NAMES[id],
        status: "active",
        onboarding_status: "tenant_created",
        created_at: "2026-09-18T10:00:00Z",
        went_live_at: null,
      })),
    }),
  );
  await page.route(/\/admin\/outbox/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { emails: [] } }) : route.continue(),
  );
  for (const id of [A, B]) {
    await page.route(`${API}/admin/tenants/${id}/overview`, (route) => route.fulfill({ json: { tenant: overview(id) } }));
    await page.route(`${API}/admin/tenants/${id}/intake-files`, (route) => route.fulfill({ json: { files: [] } }));
    await page.route(new RegExp(`${API}/admin/tenants/${id}/imports\\?`), (route) =>
      route.fulfill({
        json: {
          imports:
            id === A
              ? [
                  {
                    id: "11111111-1111-1111-1111-111111111111",
                    kind: "catalog",
                    status: "committed",
                    original_filename: "alpha-only-catalog.csv",
                    file_type: "csv",
                    row_count: 3,
                    error_code: null,
                    summary: null,
                    created_at: "2026-09-18T10:00:00Z",
                    committed_at: "2026-09-18T10:01:00Z",
                  },
                ]
              : [],
        },
      }),
    );
  }
}

test("moving from one tenant's catalog to another's never shows the first tenant's imports", async ({ page }) => {
  await stubApi(page);

  await page.goto(`/admin/tenants/${A}/catalog`);
  await expect(page.getByTestId("import-tenant-name")).toContainText("Acme Test Alpha");
  await expect(page.getByTestId("import-history")).toContainText("alpha-only-catalog.csv");

  // The founder's path: back to the tenant, to the tenant list, into B, to B's catalog.
  await page.getByRole("link", { name: "← Tenant" }).click();
  await page.getByRole("link", { name: /Tenants/ }).first().click();
  await page.getByRole("link", { name: "Acme Test Beta" }).click();
  await page.getByRole("link", { name: /Catalog/ }).first().click();

  await expect(page.getByTestId("import-tenant-name")).toContainText("Acme Test Beta");
  await expect(page.getByTestId("import-history")).toHaveCount(0);
  await expect(page.getByText("alpha-only-catalog.csv")).toHaveCount(0);
});
