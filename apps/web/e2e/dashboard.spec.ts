import { expect, test, type Page } from "@playwright/test";

/**
 * The Console home (Section 7.15.3; D-121) in the browser, against a stubbed
 * API: the four regions in order, the staleness warning, and a tenant list
 * that sorts and filters.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const KPIS = {
  documents_received: 120,
  documents_failed: 1,
  documents_approved: 100,
  documents_exported: 90,
  zero_edit_approvals: 75,
  edited_actions: 30,
  line_items: 600,
  matched_lines: 500,
  learned_rule_lines: 250,
  review_within_target: 80,
  review_sessions: 100,
  review_sessions_excluded: 3,
  est_cost_usd: "12.3456",
  mean_confidence: "0.9100",
  review_within_target_share: "0.8000",
  zero_edit_share: "0.7500",
  mapping_reuse_share: "0.5000",
  corrections_per_100_lines: "5.00",
  median_hours_to_approval: "3.5000",
  mean_cost_per_document: "0.1029",
  p95_cost_per_document: "0.2200",
};

const TENANTS = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Acme Test Distributor",
    status: "active",
    onboarding_status: "live",
    created_at: "2026-06-01T10:00:00Z",
    went_live_at: "2026-06-10T10:00:00Z",
    stripe_subscription_status: "active",
    tier_code: "growth",
    tier_name: "Growth",
    tier_monthly_price: "399.00",
    tier_document_allowance: 1000,
    documents_this_month: 1200,
    last_document_at: new Date().toISOString(),
    ai_cost_this_month: "42.50",
    needs_review: 7,
    needs_review_oldest_days: "4.2",
    mean_confidence_30: "0.9300",
    mean_confidence_7: "0.8700",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Northwind Test Supply",
    status: "active",
    onboarding_status: "test_batch_complete",
    created_at: "2026-09-01T10:00:00Z",
    went_live_at: null,
    stripe_subscription_status: null,
    tier_code: "starter",
    tier_name: "Starter",
    tier_monthly_price: "299.00",
    tier_document_allowance: 300,
    documents_this_month: 12,
    last_document_at: null,
    ai_cost_this_month: "1.10",
    needs_review: 0,
    needs_review_oldest_days: null,
    mean_confidence_30: null,
    mean_confidence_7: null,
  },
];

async function stub(page: Page, over: { stale?: boolean } = {}) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
  await page.route(`${API}/admin/tenants`, (route) => route.fulfill({ json: TENANTS }));
  await page.route(/\/admin\/dashboard/, (route) =>
    route.fulfill({
      json: {
        days: 30,
        kpis: KPIS,
        previous: { ...KPIS, zero_edit_share: "0.6000", mean_cost_per_document: "0.0900" },
        health: {
          pending: 2,
          processing: 1,
          oldest_waiting_minutes: "12.5",
          documents_today: 34,
          needs_review: 7,
          model_calls_hour: 50,
          model_failures_hour: 1,
          spend_today: "1.2300",
          spend_yesterday: "2.3400",
          last_document_processed_at: new Date().toISOString(),
        },
        money: {
          mrr: "1097.00",
          live_tenants: 3,
          live_60: 2,
          retained_60: 2,
          live_90: 1,
          retained_90: 0,
          ai_cost_this_month: "120.00",
        },
        rollup: over.stale
          ? { id: "r1", started_at: "2026-09-16T03:15:00Z", finished_at: "2026-09-16T03:16:00Z", trigger: "nightly", tenants: 3, rows_written: 6, ok: true, error: null }
          : { id: "r1", started_at: "2026-09-19T03:15:00Z", finished_at: new Date().toISOString(), trigger: "nightly", tenants: 3, rows_written: 6, ok: true, error: null },
        rollup_stale_hours: 36,
        rollup_is_stale: Boolean(over.stale),
        queues: { interactive: 0, bulk: 2 },
        worker: over.stale ? null : "celery@TESTPC",
      },
    }),
  );
}

test("the home page answers healthy, who needs attention, and how business is doing", async ({ page }) => {
  await stub(page);
  await page.goto("/admin");

  await expect(page.getByTestId("no-alerts")).toBeVisible();

  const strip = page.getByTestId("health-strip");
  await expect(strip).toContainText("Waiting");
  await expect(strip).toContainText("3"); // 2 pending + 1 processing
  await expect(strip).toContainText("13 min"); // oldest waiting, rounded
  await expect(strip).toContainText("answering");
  await expect(strip).toContainText("2%"); // 1 failure in 50 calls
  await expect(strip).toContainText("$1.23");
  await expect(strip.getByRole("link", { name: "Sentry" })).toBeVisible();
  await expect(page.getByTestId("rollup-state")).not.toContainText("may be behind");

  // The section's KPI cards.
  const cards = page.getByTestId("kpi-cards");
  await expect(cards).toContainText("80%"); // reviewed within 2 minutes
  await expect(cards).toContainText("3.5 h"); // median receipt -> approval
  await expect(cards).toContainText("75%"); // zero-edit approvals
  await expect(cards).toContainText("50%"); // learned-rule matches
  await expect(cards).toContainText("5.00"); // corrections per 100 lines
  await expect(cards).toContainText("$0.103"); // mean cost per document
  await expect(cards).toContainText("$1097"); // MRR
  await expect(cards).toContainText("100% / 0%"); // 60 / 90 day retention
});

test("the tenant list shows usage against allowance, backlog age and drift", async ({ page }) => {
  await stub(page);
  await page.goto("/admin");

  const rows = page.getByTestId("tenant-row");
  await expect(rows).toHaveCount(2);
  const acme = rows.filter({ hasText: "Acme Test Distributor" });
  await expect(acme).toContainText("1200 / 1000"); // over its allowance
  await expect(acme).toContainText("oldest 4d");
  await expect(acme).toContainText("93%"); // 30-day confidence
  await expect(acme).toContainText("$42.50");
  await expect(acme).toContainText("today");

  await page.getByTestId("tenant-filter").fill("northwind");
  await expect(page.getByTestId("tenant-row")).toHaveCount(1);
  await expect(page.getByTestId("tenant-row")).toContainText("not live yet");
});

test("a rollup that hasn't run says so, and recompute can be asked for", async ({ page }) => {
  let recomputed: unknown = null;
  await stub(page, { stale: true });
  await page.route(`${API}/admin/rollup/recompute`, (route) => {
    recomputed = route.request().postDataJSON();
    return route.fulfill({ json: { queued: true, days: 2 } });
  });
  await page.goto("/admin");

  await expect(page.getByTestId("rollup-state")).toContainText("more than 36h ago");
  await expect(page.getByTestId("health-strip")).toContainText("silent"); // no worker answered
  await page.getByTestId("recompute").click();
  await expect.poll(() => recomputed).toEqual({ days: 2 });
});
