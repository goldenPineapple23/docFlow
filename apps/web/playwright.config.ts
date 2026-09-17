import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests for the review flow (CLAUDE.md Section 6, Phase 3).
 *
 * The Phase 3 exit criterion is "a non-technical person corrects and
 * approves the golden fixture in under 2 minutes." A person has to do that
 * one; this suite proves the flow they would follow actually works in a real
 * browser — the fields edit, the approval gate holds, and the trail records
 * what changed.
 *
 * The API is stubbed at the network boundary rather than run for real. That
 * is deliberate: the API's own behaviour is already proven against real
 * Postgres and real RLS in apps/api/tests/test_review_api.py, and a browser
 * suite that also needed a database, a worker and a Redis would be the kind
 * of test that gets disabled the first time it goes flaky. What these
 * exercise is the part only a browser can: routing, rendering, focus,
 * keyboard shortcuts, and the state machine the reviewer actually drives.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run start -- --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
