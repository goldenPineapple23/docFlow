import { test as base, expect } from "@playwright/test";
import { watchCsp } from "../e2e/cspGuard";

/**
 * The live suite's `test`: real API, real Supabase, and the web app's
 * Content-Security-Policy on (D-237). Any violation on the web app's own
 * pages fails the test, so a page image or frame the policy would block
 * across sites is caught here against the real API, not a stub. Unlike the
 * stubbed suite there is no network guard: this suite reaches staging on
 * purpose (RUNBOOK 1.5).
 */
export const test = base.extend<{ cspViolations: string[] }>({
  cspViolations: [
    async ({ context }, use, testInfo) => {
      const seen = await watchCsp(context, testInfo);
      await use(seen);
      expect(seen, "this test's page broke the web app's Content-Security-Policy").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
