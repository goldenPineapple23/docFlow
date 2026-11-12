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
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    // The review screen's phone layout in WebKit, the engine an iPhone uses
    // (founder, 2026-11-05; D-234, amending D-228 item 10). Layout only: the
    // gestures need CDP, which WebKit hasn't got, so review-375-touch.spec.ts
    // is Chromium's alone. Linux and Windows WebKit aren't iOS Safari; a real
    // iPhone checks the screen at A6.
    {
      name: "webkit-375",
      use: { ...devices["Desktop Safari"], viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true },
      testMatch: /review-375\.spec\.ts/,
    },
    // The viewer and the headers in WebKit at desktop size (founder,
    // 2026-11-12; D-237): the page image, the sandboxed frame, the stored
    // text and the CSP, each with the CSP guard on. Before this, WebKit ran
    // only the 375 px layout, which loads neither a page image nor the frame.
    //
    // Not here: "a PDF is offered as a download". Playwright's WebKit fires no
    // download event for that stubbed attachment, with the CSP switched off as
    // much as on (measured 2026-11-12, D-237), so it is a test-harness gap,
    // not the policy. Chromium runs it; a real Safari download is plan task 9.
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
      testMatch: /(review|headers)\.spec\.ts/,
      grep: /headers:|sandboxed frame|original that can't be read|can't get a link|drawn pages|stored text/,
    },
  ],
  webServer: {
    command: "npm run start -- --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
