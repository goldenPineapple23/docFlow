import { expect, test, type Page } from "@playwright/test";

/**
 * Slice 5.10's Console switch for approved-example prompting (Section 7.13;
 * D-141), against a stubbed API: which customers qualify, the golden-run
 * confirmation that switching on needs (EXM-001 otherwise), and the note on
 * the review screen when an order was read with examples.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const T = "dddddddd-dddd-dddd-dddd-dddddddddddd";

const overview = (enabled: boolean) => ({
  enabled,
  min_approved: 10,
  max_examples: 3,
  buyers: [
    { buyer_id: "b1", name: "Bella's Test Coffee", approved: 12, with_text: 11, qualifies: true },
    { buyer_id: "b2", name: "Acme Test Bakery", approved: 4, with_text: 4, qualifies: false },
  ],
  this_month: {
    runs_with_examples: 7,
    example_input_tokens: 18011,
    example_cost_usd: "0.0360",
    routing_runs: 2,
    routing_cost_usd: "0.0022",
  },
});

async function stubCommon(page: Page) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants/${T}/overview`, (route) =>
    route.fulfill({ json: { tenant: { id: T, name: "Acme Test Delta" } } }),
  );
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
}

test("switching example prompting on needs the golden-run confirmation", async ({ page }) => {
  await stubCommon(page);
  let enabled = false;
  const sent: unknown[] = [];
  await page.route(`${API}/admin/tenants/${T}/example-prompting`, async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { enabled: boolean; golden_run_confirmed: boolean };
      sent.push(body);
      if (body.enabled && !body.golden_run_confirmed) {
        return route.fulfill({
          status: 422,
          json: {
            detail: {
              code: "EXM-001",
              title: "Confirm the live golden check first",
              message: "The confirmation wasn't ticked, so nothing was changed.",
              action: "Run the live check, tick the confirmation and switch it on again.",
              severity: "warning",
            },
          },
        });
      }
      enabled = body.enabled;
    }
    return route.fulfill({ json: overview(enabled) });
  });

  await page.goto(`/admin/tenants/${T}/examples`);
  await expect(page.getByRole("heading", { name: "Example prompting — Acme Test Delta" })).toBeVisible();
  await expect(page.getByTestId("examples-state")).toContainText("Currently off. 1 customer would get examples.");
  await expect(page.getByTestId("examples-buyer-b2")).toContainText("Not yet (6 more)");
  await expect(page.getByTestId("examples-runs")).toHaveText("7");

  await page.getByTestId("examples-on").click();
  await expect(page.getByText("Confirm the live golden check first")).toBeVisible();
  await expect(page.getByTestId("examples-state")).toContainText("Currently off");

  await page.getByTestId("examples-confirm").check();
  await page.getByTestId("examples-on").click();
  await expect(page.getByTestId("examples-state")).toContainText("Currently on");
  await expect(page.getByTestId("examples-notice")).toContainText("applies from the next order");

  await page.getByTestId("examples-off").click();
  await expect(page.getByTestId("examples-state")).toContainText("Currently off");
  expect(sent).toEqual([
    { enabled: true, golden_run_confirmed: false },
    { enabled: true, golden_run_confirmed: true },
    { enabled: false, golden_run_confirmed: false },
  ]);
});

test("the Audit tab lists changes in plain English and shows page views only when asked", async ({ page }) => {
  // Section 7.15.3's "Audit" tab (D-143). Values are rendered as text.
  await stubCommon(page);
  const calls: string[] = [];
  await page.route(new RegExp(`${API}/admin/tenants/${T}/audit.*`), (route) => {
    const url = route.request().url();
    calls.push(url);
    const views = url.includes("include_views=true");
    const change = {
      at: "2026-09-25T10:00:00Z",
      source: "lifecycle",
      event: "example_prompting_enabled",
      actor_email: "founder@example.test",
      actor_is_docflow: true,
      payload: { golden_run_confirmed: true },
      constants: {},
    };
    const view = { ...change, source: "console", event: "read", payload: { note: "<b>not html</b>" } };
    return route.fulfill({ json: { total: views ? 2 : 1, entries: views ? [view, change] : [change] } });
  });

  await page.goto(`/admin/tenants/${T}/audit`);
  await expect(page.getByRole("heading", { name: "Audit — Acme Test Delta" })).toBeVisible();
  await expect(page.getByTestId("audit-row")).toHaveCount(1);
  await expect(page.getByTestId("audit-row").first()).toContainText("Example prompting turned on");
  await expect(page.getByTestId("audit-row").first()).toContainText("DocFlow support");
  await expect(page.getByTestId("audit-row").first()).toContainText("golden run confirmed: true");

  await page.getByTestId("audit-include-views").check();
  await expect(page.getByTestId("audit-row")).toHaveCount(2);
  await expect(page.getByTestId("audit-row").first()).toContainText("Viewed");
  // Payload text is shown as text, never rendered as HTML (Section 7.12).
  await expect(page.getByTestId("audit-row").first()).toContainText("<b>not html</b>");
  expect(calls.some((u) => u.includes("include_views=true"))).toBe(true);
});
