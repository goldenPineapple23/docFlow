import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * The live end-to-end suite: a real browser, the real Next build, the real
 * FastAPI API over HTTP, real Postgres, real RLS and a real sign-in. Nothing
 * is stubbed (DECISIONS.md D-166).
 *
 * This is deliberately a second configuration rather than more specs in
 * `e2e/`. D-086 stubs the API at the network boundary there, and that is
 * still the right call for what that suite covers -- routing, rendering,
 * focus, keyboard handling. But a stub decides for itself what the API
 * returns, so no test in that suite can prove that what the screen shows
 * after a write matches what the server now holds. That seam is where the
 * Stage 1 walkthrough found a reviewer being shown a dead Approve button and
 * no reason for it: the API had raised a new check, the screen had read it,
 * and it rendered a thousand pixels below the fold with a green "Saved"
 * banner above it.
 *
 * What it costs to keep honest: a database and two servers. Which is why it
 * is one file with two specs about one seam, not a second copy of `e2e/`.
 * Anything provable with a stub belongs in `e2e/`; anything provable without
 * a browser belongs in `apps/api/tests`.
 *
 * Its data is a throwaway tenant, created by `scripts/seed_live_e2e.py`
 * before the run and deleted after it, so a run shares no row with any other
 * (D-160).
 */

const PORT = 3101;

// The interpreter that has `docflow_core` installed. CI sets it; on the
// founder's machine the API's virtualenv is the one that does.
export const PYTHON =
  process.env.DOCFLOW_PYTHON ??
  (process.platform === "win32" ? path.resolve(__dirname, "../api/.venv/Scripts/python.exe") : "python");

export const SEED_FILE = path.resolve(__dirname, "e2e-live/.seed.json");

export default defineConfig({
  testDir: "./e2e-live",
  // One tenant, one reviewer, two documents: the specs edit them, so they
  // run one at a time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // A real stack, a real sign-in and a real database: the first test of a
  // run waits for all three to warm up.
  timeout: 90_000,
  reporter: process.env.CI ? "github" : "list",
  globalSetup: "./e2e-live/global-setup.ts",
  globalTeardown: "./e2e-live/global-teardown.ts",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // `npm run build` must already have run with the NEXT_PUBLIC_* values
    // for the stack under test -- Next compiles them into the bundle.
    command: `npm run start -- --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    // Never reuse a server already on this port, unlike the stubbed suite.
    // A `next start` serves the build that was on disk when it started, so
    // reusing one after a rebuild hands the browser a page whose script
    // chunks have been renamed: every asset 404s, the page renders nothing,
    // and the suite fails with "save-button not found" -- which reads like
    // the app being broken rather than the server being stale. Cost of
    // always starting fresh: a few seconds. Cost of the alternative: an
    // afternoon, once, which is how this comment came to be written.
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
