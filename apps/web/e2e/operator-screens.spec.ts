import { expect, test, type Page } from "@playwright/test";

/**
 * Slice 5.4's operator screens (D-119), driven in the browser against a
 * stubbed API: the founder picks which customer to keep and confirms before
 * anything merges; rules show who taught them and can be switched off.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const T = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const OLD = "11111111-1111-1111-1111-111111111111";
const NEW = "22222222-2222-2222-2222-222222222222";

const side = (id: string, name: string, documents: number, email: string | null) => ({
  id,
  name,
  contact_email: email,
  external_account_number: null,
  created_at: "2026-09-18T10:00:00Z",
  documents,
  rules: 0,
});

async function stubCommon(page: Page) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants/${T}/overview`, (route) =>
    route.fulfill({ json: { tenant: { id: T, name: "Acme Test Gamma" } } }),
  );
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
}

test("merging asks which customer to keep, confirms, then reports what moved", async ({ page }) => {
  await stubCommon(page);
  let merged: unknown = null;
  await page.route(`${API}/admin/tenants/${T}/buyer-merges`, (route) =>
    route.fulfill({
      json: merged
        ? { candidates: [], history: [] }
        : {
            candidates: [
              {
                id: "cand-1",
                similarity_score: "0.9412",
                created_at: "2026-09-19T10:00:00Z",
                detected_from_document_id: null,
                buyers: [side(OLD, "Bella's Test Coffee", 4, null), side(NEW, "Bellas Test Coffee LLC", 1, "orders@bellas.example")],
              },
            ],
            history: [],
          },
    }),
  );
  await page.route(`${API}/admin/tenants/${T}/buyer-merges/cand-1/merge`, (route) => {
    merged = route.request().postDataJSON();
    return route.fulfill({ json: { merge_id: "m1", documents_moved: 4, rules_moved: 0, fields_filled: ["contact_email"] } });
  });

  await page.goto(`/admin/tenants/${T}/merges`);
  await expect(page.getByRole("heading", { name: "Possible duplicate customers — Acme Test Gamma" })).toBeVisible();
  await expect(page.getByText("Names 94% alike")).toBeVisible();

  // The older customer is the default keeper; choose the newer one instead.
  const keep = page.getByTestId("merge-keep");
  await expect(keep.first()).toBeChecked();
  await keep.nth(1).check();

  await page.getByTestId("merge-start").click();
  await expect(page.getByTestId("merge-confirm-box")).toContainText(
    "Merge “Bella's Test Coffee” into “Bellas Test Coffee LLC”? 4 orders and 0 rules move",
  );
  expect(merged).toBeNull(); // nothing sent before confirming
  await page.getByTestId("merge-confirm").click();

  await expect(page.getByTestId("merge-notice")).toHaveText(
    "Merged “Bella's Test Coffee” into “Bellas Test Coffee LLC”: 4 orders and 0 rules moved.",
  );
  expect(merged).toEqual({ keep_buyer_id: NEW });
  await expect(page.getByTestId("merge-empty")).toBeVisible();
});

test("rules show who taught them, and switching one off calls the API", async ({ page }) => {
  await stubCommon(page);
  let status: "active" | "disabled" = "active";
  const calls: string[] = [];
  await page.route(`${API}/admin/tenants/${T}/rules`, (route) =>
    route.fulfill({
      json: {
        rules: [
          {
            id: "r1",
            rule_type: "sku_mapping",
            match_key: "test widget blue",
            match_value: { raw_description: "Test Widget, Blue", sku: "TEST-2002" },
            status,
            times_applied: 7,
            created_at: "2026-09-18T10:00:00Z",
            updated_at: "2026-09-18T10:00:00Z",
            buyer_id: OLD,
            buyer_name: "Acme Test Buyer",
            confirmed_by_email: null,
            by_docflow_support: true,
            source_document_id: "d1",
            source_po_number: "TEST-PO-7",
            item_sku: "TEST-2002",
            item_description: "Test widget",
            item_retired: true,
          },
        ],
      },
    }),
  );
  await page.route(/\/admin\/tenants\/[^/]+\/rules\/r1\/(disable|enable|delete)$/, (route) => {
    const action = route.request().url().split("/").pop()!;
    calls.push(action);
    status = action === "disable" ? "disabled" : "active";
    return route.fulfill({ json: { change: { before: "active", after: status } } });
  });

  await page.goto(`/admin/tenants/${T}/rules`);
  const row = page.getByTestId("rule-row");
  await expect(row).toContainText("Product wording → SKU");
  await expect(row).toContainText("“Test Widget, Blue”");
  await expect(row).toContainText("TEST-2002 · Test widget");
  await expect(row).toContainText("SKU retired");
  await expect(row).toContainText("DocFlow support");
  await expect(row.getByRole("link", { name: "PO TEST-PO-7" })).toHaveAttribute(
    "href",
    `/admin/tenants/${T}/review/d1`,
  );

  await page.getByTestId("rule-toggle").click();
  await expect(page.getByTestId("rule-toggle")).toHaveText("Switch on");
  await page.getByTestId("rule-delete").click();
  expect(calls).toEqual(["disable"]); // delete asks first
  await expect(page.getByTestId("rule-delete-confirm")).toBeVisible();
});
