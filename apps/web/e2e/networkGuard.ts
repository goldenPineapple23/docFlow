import { test as base, expect } from "@playwright/test";

/**
 * Every e2e test runs against a stubbed API and must never reach a real
 * server. One test once did -- the browser sent an enrolment to the real
 * Supabase project in .env, and the test depended on staging answering --
 * so this fails any test whose browser makes a request to a host other than
 * this machine.
 *
 * A request a test stubs with page.route is answered in the browser and
 * never leaves it, so it is not caught here (page routes run before this
 * context route). Anything unstubbed is aborted -- it never reaches the
 * host -- and recorded, and the test fails naming it.
 *
 * Every spec imports `test` and `expect` from here, not from
 * @playwright/test; e2e/network-guard.spec.ts fails the suite if one doesn't.
 * The live suite (e2e-live/) talks to staging on purpose and does not use it.
 */

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

function leavesThisMachine(url: URL): boolean {
  return ["http:", "https:", "ws:", "wss:"].includes(url.protocol) && !LOOPBACK.has(url.hostname);
}

export const test = base.extend<{ networkGuard: void }>({
  networkGuard: [
    async ({ context }, use) => {
      const escaped: string[] = [];
      await context.route(leavesThisMachine, async (route) => {
        const url = new URL(route.request().url());
        escaped.push(`${route.request().method()} ${url.protocol}//${url.host}${url.pathname}`);
        await route.abort("blockedbyclient");
      });
      await use();
      expect(escaped, "this test's browser tried to reach a host other than this machine").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
