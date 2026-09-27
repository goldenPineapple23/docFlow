import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { SEED_FILE } from "../playwright.live.config";

/**
 * After an edit, the screen says what the checks now are (CLAUDE.md
 * Sections 7.3 and 7.7).
 *
 * The defect these are written from, found in the Stage 1 walkthrough on
 * staging: a reviewer changed a unit price from 47.50 to 4750 and saved.
 * The API did everything right -- it re-ran validation in the same
 * transaction and raised a new check, and the screen even read it back. But
 * the screen showed a green "Saved · Your changes are recorded", greyed out
 * Approve, and put the new check in a panel below the line table that is
 * never on screen at the same time as the button it controls. Every ticked
 * check was silently un-ticked too. From the reviewer's seat: Approve died
 * and nothing said why.
 *
 * Nothing is stubbed here. The stubbed suite (D-086) decides for itself
 * what the API returns, so it cannot fail on this; it passed throughout.
 * These drive the real API over HTTP against real Postgres, because the
 * thing being asserted is precisely that the screen agrees with the server
 * after a write.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

type Seed = {
  email: string;
  password: string;
  clean_document_id: string;
  flagged_document_id: string;
  ack_document_id: string;
};

const seed: Seed = JSON.parse(readFileSync(SEED_FILE, "utf8"));

// A normal laptop window. The size matters: the bug was invisible precisely
// because the checks panel sits below it.
test.use({ viewport: { width: 1440, height: 900 } });

async function signIn(page: Page) {
  // Only what would explain a failure. A sign-in that breaks in CI is
  // otherwise just "save-button not found", with the cause three layers
  // down; these three lines are what identified D-167.
  page.on("console", (m) => {
    if (m.type() === "error") console.log("[browser error]", m.text().slice(0, 300));
  });
  page.on("requestfailed", (r) =>
    console.log("[request failed]", r.method(), r.url(), r.failure()?.errorText),
  );
  page.on("response", (r) => {
    if (!r.ok()) console.log("[http]", r.status(), r.request().method(), r.url());
  });
  await page.goto("/login");
  await page.locator("#email").fill(seed.email);
  await page.locator("#password").fill(seed.password);
  await page.getByRole("button", { name: /sign in/i }).click();
  // Generous, and deliberately so: the first sign-in of a run pays for the
  // web server's cold start and the API's first database connection. A
  // browser suite that goes flaky is a browser suite someone switches off
  // (D-086), and Section 10 forbids getting a merge through that way.
  await page.waitForURL(/\/review/, { timeout: 60_000 });
}

async function openOrder(page: Page, documentId: string) {
  await page.goto(`/review/${documentId}`);
  await expect(page.getByTestId("save-button")).toBeVisible();
  // The detail read has landed once the checks panel has rendered.
  await expect(page.locator("#warnings-heading")).toBeVisible();
}

/** Saves, and waits for the write and the re-read that follows it. */
async function save(page: Page) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await Promise.all([
    page.waitForResponse((r) => r.request().method() === "PATCH" && r.status() === 200),
    page.getByTestId("save-button").click(),
  ]);
  await expect(page.getByTestId("save-button")).toBeDisabled();
}

/** True when an element is inside the window the reviewer is looking at. */
async function inViewport(page: Page, testId: string): Promise<boolean> {
  return page.getByTestId(testId).evaluate((element) => {
    const box = element.getBoundingClientRect();
    return box.top >= 0 && box.bottom <= window.innerHeight;
  });
}

test("an edit that raises a check says so, names it, and Approve says which one blocks it", async ({
  page,
}) => {
  await signIn(page);
  await openOrder(page, seed.clean_document_id);

  // Nothing to tick: this order reconciles, so Approve is live.
  await expect(page.getByTestId("no-warnings")).toBeVisible();
  await expect(page.getByTestId("approve-button")).toBeEnabled();
  await expect(page.getByTestId("approve-blocked")).toHaveCount(0);

  // The founder's edit. 12 × 4750 is not 570.00, so Section 7.7's
  // line-total rule must raise a check -- and it is the API that decides
  // that, not this test.
  await page.getByTestId("line-1-unit_price").fill("4750");
  await save(page);

  // 1. The screen says a check appeared, where the reviewer is looking.
  const banner = page.getByTestId("banner-warning");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/1 new check to look at/i);
  await expect(banner).toContainText(/Line 1/);
  expect(await inViewport(page, "banner-warning")).toBe(true);

  // 2. Approve is off, and the screen names the check that is holding it.
  await expect(page.getByTestId("approve-button")).toBeDisabled();
  const blocked = page.getByTestId("approve-blocked");
  await expect(blocked).toBeVisible();
  await expect(blocked).toContainText(/Line 1/);
  expect(await inViewport(page, "approve-blocked")).toBe(true);

  // 3. One click reaches the check itself, wherever the panel happens to be.
  await page.getByTestId("approve-blocked-show").click();
  const row = page.getByTestId("warning-VAL-001");
  await expect(row).toBeVisible();
  await expect(row).toContainText(/New/);
  await expect(row.getByRole("checkbox")).toBeFocused();

  // 4. Ticking it -- and only it -- releases Approve.
  await row.getByRole("checkbox").check();
  await expect(page.getByTestId("approve-button")).toBeEnabled();

  // 5. Putting the number back clears the check, and the screen says that too.
  await page.getByTestId("line-1-unit_price").fill("47.50");
  await save(page);
  await expect(page.getByTestId("warning-VAL-001")).toHaveCount(0);
  await expect(page.getByTestId("no-warnings")).toBeVisible();
  await expect(page.getByTestId("banner-success")).toContainText(/no longer applies/i);
  await expect(page.getByTestId("approve-button")).toBeEnabled();
});

test("an unrelated edit keeps the checks a reviewer has already ticked", async ({ page }) => {
  await signIn(page);
  await openOrder(page, seed.flagged_document_id);

  // This order's total disagrees with its lines, so it arrives with a check.
  const row = page.getByTestId("warning-VAL-002");
  await expect(row).toBeVisible();
  await expect(page.getByTestId("approve-button")).toBeDisabled();

  await row.getByRole("checkbox").check();
  await expect(page.getByTestId("approve-button")).toBeEnabled();

  // An edit that has nothing to do with that check. The check is still the
  // same statement about the same two numbers, so the tick stands -- making
  // the reviewer tick it again taught them only that Approve turns off for
  // no reason (D-166).
  await page.getByTestId("line-1-description").fill("Colombian Whole Bean 5lb (case)");
  await save(page);

  await expect(page.getByTestId("warning-VAL-002").getByRole("checkbox")).toBeChecked();
  await expect(page.getByTestId("approve-button")).toBeEnabled();
  await expect(page.getByTestId("approve-blocked")).toHaveCount(0);
  await expect(page.getByTestId("banner-success")).toBeVisible();
});

test("an edit to the very number a ticked check is about clears that tick", async ({ page }) => {
  await signIn(page);
  await openOrder(page, seed.ack_document_id);

  // The check is "the order total doesn't match the line items": 900.00
  // against 570.00 of lines.
  const row = () => page.getByTestId("warning-VAL-002");
  await expect(row()).toBeVisible();
  const firstText = await row().textContent();
  expect(firstText).toContain("900.00");

  await row().getByRole("checkbox").check();
  await expect(page.getByTestId("approve-button")).toBeEnabled();

  // Change the one number that check is about. This is the case the ticks
  // must NOT survive: "out by 330" and "out by 3,430" are different
  // statements, and a person who agreed to the first has not looked at the
  // second. The server gives it a new fingerprint and a new id (D-074), so
  // it arrives here unticked -- this asserts the screen honours that rather
  // than carrying the tick across by field or by code.
  await page.getByTestId("header-input-order_total").fill("4000.00");
  await save(page);

  const after = row();
  await expect(after).toBeVisible();
  await expect(after.getByRole("checkbox")).not.toBeChecked();
  await expect(after).toContainText("4000.00");
  await expect(after).toContainText(/New/);
  await expect(page.getByTestId("approve-button")).toBeDisabled();
  await expect(page.getByTestId("approve-blocked")).toBeVisible();
  await expect(page.getByTestId("banner-warning")).toContainText(/1 new check to look at/i);

  // And back again. The check that returns is a new row, not the one that
  // was ticked two edits ago, so the reviewer confirms it once more -- they
  // last looked at a document that has changed twice since (D-168).
  await page.getByTestId("header-input-order_total").fill("900.00");
  await save(page);

  const returned = row();
  await expect(returned).toBeVisible();
  await expect(returned).toContainText("900.00");
  await expect(returned.getByRole("checkbox")).not.toBeChecked();
  await expect(returned).toContainText(/New/);
  await expect(page.getByTestId("approve-button")).toBeDisabled();
});
