import { expect, test } from "./networkGuard";

/**
 * The Audit tab's Result column (D-178, D-220), against a stubbed API: each
 * Console action shows its recorded outcome, an action with none reads as
 * "Outcome unknown" (never as done), and one from before outcomes were kept
 * says so. Account events show no result.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const T = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";

const row = (over: Record<string, unknown>) => ({
  id: crypto.randomUUID(),
  at: "2026-10-27T01:00:00Z",
  source: "console",
  actor_email: "founder@example.test",
  actor_is_docflow: true,
  payload: { outcome_expected: true },
  constants: {},
  outcome: null,
  outcome_state: null,
  ...over,
});

test("the Result column shows done, failed with its code, unknown, and not recorded", async ({ page }) => {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants/${T}/overview`, (route) =>
    route.fulfill({ json: { tenant: { id: T, name: "Acme Test Echo" } } }),
  );
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
  const at = "2026-10-27T01:00:01Z";
  const entries = [
    row({
      event: "intake_address_rotate",
      outcome_state: "succeeded",
      outcome: { result: "succeeded", code: null, at },
    }),
    row({
      event: "tier_change",
      payload: { tier: "growth", outcome_expected: true },
      outcome_state: "failed",
      outcome: { result: "failed", code: "BIL-005", at },
    }),
    row({ event: "go_live", outcome_state: "unknown" }),
    row({ event: "cancel", payload: {}, outcome_state: "not_recorded" }),
    row({ source: "lifecycle", event: "tier_changed", payload: { note: "an account event" } }),
  ];
  await page.route(new RegExp(`${API}/admin/tenants/${T}/audit.*`), (route) =>
    route.fulfill({ json: { total: entries.length, entries } }),
  );

  await page.goto(`/admin/tenants/${T}/audit`);
  const rows = page.getByTestId("audit-row");
  await expect(rows).toHaveCount(5);
  const result = (n: number) => rows.nth(n).getByTestId("audit-result");

  await expect(result(0)).toHaveText("Done");
  await expect(result(1)).toHaveText("Failed: BIL-005");
  await expect(result(2)).toHaveText(
    "Outcome unknown: no outcome was recorded. Check the tenant's current state before retrying.",
  );
  await expect(result(3)).toHaveText("Outcome not recorded (before D-178)");
  await expect(result(4)).toHaveText("");

  // The bookkeeping flag is the Result column's job, not a detail.
  await expect(rows.nth(1)).toContainText("tier: growth");
  await expect(page.getByText("outcome expected")).toHaveCount(0);
});
