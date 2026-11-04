import { expect, type Page } from "@playwright/test";

/**
 * Signing in and opening an order, shared by the live specs (moved word for
 * word from review-warnings.spec.ts for viewer A4, D-233; `signIn` now takes
 * the seeded reviewer rather than reading the seed itself).
 */

export async function signIn(page: Page, who: { email: string; password: string }) {
  // Kept, not discarded, so a failure can be explained by the error itself.
  const noticed: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") noticed.push(`console error: ${m.text().slice(0, 200)}`);
  });
  page.on("requestfailed", (r) =>
    noticed.push(`request failed: ${r.method()} ${r.url()} ${r.failure()?.errorText}`),
  );
  page.on("response", (r) => {
    if (!r.ok()) noticed.push(`http ${r.status()} ${r.request().method()} ${r.url()}`);
  });

  await page.goto("/login");
  await page.locator("#email").fill(who.email);
  await page.locator("#password").fill(who.password);
  await page.getByRole("button", { name: /sign in/i }).click();

  try {
    // Generous, and deliberately so: the first sign-in of a run pays for the
    // web server's cold start and the API's first database connection. A
    // browser suite that goes flaky is a browser suite someone switches off
    // (D-086), and Section 10 forbids getting a merge through that way.
    await page.waitForURL(/\/review/, { timeout: 60_000 });
  } catch {
    // "waitForURL timed out" says nothing about which of the three moving
    // parts failed -- Supabase Auth, our API, or the page's own redirect --
    // and whoever is reading this may not be able to open the CI log at all.
    // So the error carries the answer instead of pointing at a log.
    const visible = (await page.locator("body").innerText().catch(() => "")).
      replace(/\s+/g, " ").slice(0, 400);
    const session = await page.evaluate(() => {
      try {
        const key = Object.keys(localStorage).find((k) => k.includes("auth-token"));
        return key ? "a Supabase session is stored, so Auth accepted the password" : "none stored";
      } catch (e) {
        return `localStorage unreadable: ${String(e).slice(0, 80)}`;
      }
    });
    throw new Error(
      [
        "Signing in never reached the review queue.",
        `still at: ${page.url()}`,
        `session:  ${session}`,
        `page says: ${visible || "(nothing)"}`,
        "what the browser saw:",
        ...(noticed.length ? noticed.slice(-15).map((n) => `  ${n}`) : ["  (nothing failed)"]),
      ].join("\n"),
    );
  }
}

export async function openOrder(page: Page, documentId: string) {
  await page.goto(`/review/${documentId}`);
  await expect(page.getByTestId("save-button")).toBeVisible();
  // The detail read has landed once the checks panel has rendered.
  await expect(page.locator("#warnings-heading")).toBeVisible();
}
