import { expect, test, type Page } from "@playwright/test";
import { DOCUMENT_ID, detail } from "./fixtures/review";

/**
 * The founder reviewing a tenant's order from the Console (CLAUDE.md Section
 * 7.15.1 / 7.15.2 Step 8; D-111): the SAME review screen, calling the Console's
 * acting-as routes -- never the tenant routes -- under a banner that says
 * whose data it is.
 *
 * The API is stubbed at the network boundary; the gate itself is proven
 * against the real database in apps/api/tests/test_acting_as.py.
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TENANT = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const ACT = `${API}/admin/tenants/${TENANT}/act/review`;

async function stubConsole(page: Page) {
  const apiPaths: string[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith(API)) apiPaths.push(new URL(request.url()).pathname);
  });
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants/${TENANT}/overview`, (route) =>
    route.fulfill({ json: { tenant: { id: TENANT, name: "Acme Test Prospect" } } }),
  );
  const state = { detail: detail({ document: { ...detail().document, is_test_batch: true } }) };
  await page.route(`${ACT}/documents?*`, (route) =>
    route.fulfill({
      json: {
        documents: [
          {
            id: DOCUMENT_ID,
            original_filename: "po.pdf",
            status: "needs_review",
            source: "upload",
            created_at: "2026-09-18T10:00:00Z",
            overall_confidence: "0.91",
            is_test_batch: true,
            injection_suspected: false,
            is_possible_duplicate: false,
            is_possible_change_order: false,
            review_started_at: null,
            approved_at: null,
            po_number: "BCH-2291",
            buyer_name: "Bella's Test Coffee House",
            order_total: "570.00",
            currency: "USD",
            open_warnings: 0,
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      },
    }),
  );
  await page.route(`${ACT}/documents/${DOCUMENT_ID}/original`, (route) =>
    route.fulfill({
      json: { url: "/review/documents/x/original/content?token=t", expires_at: 0, previewable: false },
    }),
  );
  await page.route(`${ACT}/documents/${DOCUMENT_ID}/exports`, (route) => route.fulfill({ json: { exports: [] } }));
  await page.route(`${ACT}/documents/${DOCUMENT_ID}`, async (route) => {
    if (route.request().method() === "PATCH") {
      state.detail.version = "version-2";
      await route.fulfill({ json: { review_action_id: "r0", version: "version-2" } });
      return;
    }
    await route.fulfill({ json: state.detail });
  });
  return apiPaths;
}

test("the Console opens a tenant's order in the normal review screen, as DocFlow support", async ({ page }) => {
  const apiPaths = await stubConsole(page);

  await page.goto(`/admin/tenants/${TENANT}/review`);
  await expect(page.getByTestId("support-banner")).toContainText("Acme Test Prospect");
  await page.getByRole("link", { name: "BCH-2291" }).click();

  await expect(page).toHaveURL(new RegExp(`/admin/tenants/${TENANT}/review/${DOCUMENT_ID}$`));
  await expect(page.getByTestId("support-banner")).toContainText("DocFlow support");
  await expect(page.getByRole("heading", { name: "BCH-2291" })).toBeVisible();

  // Every call went to the Console's acting-as routes; none to the tenant's own.
  expect(apiPaths.filter((p) => p.startsWith("/review/"))).toEqual([]);
  expect(apiPaths).toContain(`/admin/tenants/${TENANT}/act/review/documents/${DOCUMENT_ID}`);

  // The way back leads to the tenant's onboarding page, where the founder
  // carries on -- not the tenant surface, and not a detour via the order list.
  await expect(page.getByTestId("review-back")).toHaveText("← Tenant");
  await expect(page.getByTestId("review-back")).toHaveAttribute("href", `/admin/tenants/${TENANT}`);
});
