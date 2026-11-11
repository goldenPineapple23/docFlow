import { type Locator, type Page } from "@playwright/test";
import { expect, test } from "./networkGuard";
import { DOCUMENT_ID, LINE_ID, WARNING_ID } from "./fixtures/review";
import {
  API,
  PHONE,
  VIEWS,
  VIEW_ELEMENT,
  openReview,
  pagePixelAt,
  sideways,
  stubReview,
  type View,
} from "./fixtures/review375";

/**
 * The review screen at 375 px (A5; D-234): layout only, so it runs in
 * Chromium and in WebKit (playwright.config.ts, `webkit-375`). The gestures
 * need CDP, which only Chromium has, and are in review-375-touch.spec.ts.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

test.use({ viewport: PHONE, isMobile: true, hasTouch: true });

/** Smallest box a control may have, in CSS pixels (D-234's minimums). */
const PRIMARY = 48;
const TARGET = 44;

async function box(locator: Locator) {
  const found = await locator.boundingBox();
  expect(found, "the control is on the page").not.toBeNull();
  return found!;
}

async function inViewport(page: Page, locator: Locator) {
  const { y, height } = await box(locator);
  const viewport = page.viewportSize()!;
  return y >= 0 && y + height <= viewport.height;
}

for (const who of ["owner", "unanswered"] as const) {
  for (const view of VIEWS) {
    test(`the page never scrolls sideways at 375 or 768 (${who}, ${view} view)`, async ({ page }) => {
      await stubReview(page, { view, who });
      for (const width of [375, 768]) {
        await page.setViewportSize({ width, height: PHONE.height });
        await openReview(page, view);
        expect(await sideways(page), `sideways scroll at ${width}px`).toBeLessThanOrEqual(0);
      }
    });
  }
}

// The smallest desktop width. From 1280 the line table's scroll box isn't
// positioned (`max-xl:relative`), so the desktop draws as on 014f6c8 (D-234
// item 11); this guards that its hidden labels still don't push the page
// sideways there.
test.describe("at 1280, in a desktop browser", () => {
  test.use({ viewport: { width: 1280, height: 900 }, isMobile: false, hasTouch: false });
  for (const who of ["owner", "unanswered"] as const) {
    for (const view of VIEWS) {
      test(`the page never scrolls sideways at 1280 (${who}, ${view} view)`, async ({ page }) => {
        await stubReview(page, { view, who });
        await openReview(page, view);
        expect(await sideways(page), "sideways scroll at 1280px").toBeLessThanOrEqual(0);
      });
    }
  }
});

for (const view of VIEWS) {
  test(`the document has a box of its own, and the order follows it (${view} view)`, async ({ page }) => {
    await stubReview(page, { view });
    await openReview(page, view);

    const shown = await box(page.getByTestId(VIEW_ELEMENT[view]));
    // The box is min(60svh, 560px), at least 320 (487 px at 812 tall). The
    // text view's label line takes up to 40 px of it.
    expect(shown.height, "the view's height").toBeGreaterThanOrEqual(280);
    expect(shown.height, "the view's height").toBeLessThanOrEqual(560);
    const details = await box(page.locator("#header-heading"));
    expect(details.y - (shown.y + shown.height), "the order details start right after the document").toBeLessThan(200);
  });
}

test("Save and Approve stay on screen wherever the page is scrolled, and are big enough to tap", async ({ page }) => {
  await stubReview(page);
  await openReview(page, "pages");

  const save = page.getByTestId("save-button");
  const approve = page.getByTestId("approve-button");
  for (const where of ["top", "line-1", "warnings-heading", "bottom"]) {
    if (where === "line-1") await page.getByTestId("line-1").scrollIntoViewIfNeeded();
    if (where === "warnings-heading") await page.locator("#warnings-heading").scrollIntoViewIfNeeded();
    if (where === "bottom") await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    expect(await inViewport(page, save), `Save on screen (${where})`).toBe(true);
    expect(await inViewport(page, approve), `Approve on screen (${where})`).toBe(true);
  }
  expect((await box(save)).height).toBeGreaterThanOrEqual(PRIMARY);
  expect((await box(approve)).height).toBeGreaterThanOrEqual(PRIMARY);
});

test("the bar's next check takes the reviewer to it, and Approve works from the bar", async ({ page }) => {
  const state = await stubReview(page);
  await openReview(page, "pages");

  const next = page.getByTestId("action-bar-next-check");
  await expect(next).toBeVisible();
  await expect(page.getByTestId("action-bar")).toContainText("1 check left");
  await next.click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe(`ack-${WARNING_ID}`);

  await page.getByTestId(`ack-${WARNING_ID}`).check();
  await expect(next).toHaveCount(0);
  await page.getByTestId("approve-button").click();
  await expect(page.getByTestId("banner-success")).toContainText("Approved");
  expect(state.approved).toBe(true);
});

test("every control a reviewer taps is at least 44 px, and Save and Approve 48", async ({ page }) => {
  await stubReview(page);
  await openReview(page, "pages");

  const targets: Array<[string, Locator]> = [
    ["back", page.getByTestId("review-back")],
    ["Reject", page.getByRole("button", { name: "Reject", exact: true })],
    ["Approve & export", page.getByTestId("approve-export-button")],
    ["export format", page.getByTestId("approve-export-format")],
    ["the bar's next check", page.getByTestId("action-bar-next-check")],
    ["Show it", page.getByTestId("approve-blocked-show")],
    ["previous page", page.getByTestId("page-prev")],
    ["next page", page.getByTestId("page-next")],
    ["zoom out", page.getByTestId("zoom-out")],
    ["zoom in", page.getByTestId("zoom-in")],
    ["fit to width", page.getByTestId("zoom-fit")],
    ["the check's tick box", page.getByTestId(`ack-hit-${WARNING_ID}`)],
    ["the line's flag", page.getByTestId("line-1-flag")],
    ["the line's details", page.getByRole("button", { name: "Catalog match and checks for line 1" })],
    ["a candidate", page.getByRole("button", { name: "This one" }).first()],
    ["the catalog search", page.getByTestId("sku-search-1")],
    ["its button", page.getByRole("button", { name: "Search", exact: true }).first()],
    ["the check's Show", page.getByRole("button", { name: /^Show “Line total”/ })],
    ["a header link", page.getByRole("link", { name: "Upload" })],
    ["Sign out", page.getByTestId("sign-out")],
  ];
  for (const [name, locator] of targets) {
    const { width, height } = await box(locator);
    expect.soft(height, `${name}: height`).toBeGreaterThanOrEqual(TARGET);
    expect.soft(width, `${name}: width`).toBeGreaterThanOrEqual(TARGET);
  }

  // Text boxes: 44 tall, and 16 px text, so Safari doesn't zoom the page when one is tapped.
  for (const input of await page.locator(`input[type="text"], input[type="search"], select`).all()) {
    if (!(await input.isVisible())) continue;
    const name = (await input.getAttribute("data-testid")) ?? (await input.getAttribute("id")) ?? "a field";
    expect.soft((await box(input)).height, `${name}: height`).toBeGreaterThanOrEqual(TARGET);
    const font = await input.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect.soft(font, `${name}: font size`).toBeGreaterThanOrEqual(16);
  }
  expect(test.info().errors, "every control meets its minimum").toEqual([]);
});

test("below 640 each line is a card of the same table, labelled, and keeps its table roles", async ({ page }) => {
  await stubReview(page);
  await openReview(page, "pages");

  const table = page.locator("table").first();
  await expect(table).toHaveAttribute("role", "table");
  await expect(page.getByTestId("line-1")).toHaveAttribute("role", "row");
  await expect(page.getByTestId("line-1").locator("td").first()).toHaveAttribute("role", "cell");
  await expect(page.getByRole("columnheader", { name: "Qty" })).toHaveCount(1);

  // One input per value, labelled on screen.
  await expect(page.locator(`#line-${LINE_ID}-quantity`)).toHaveCount(1);
  await expect(page.locator(`label[for="line-${LINE_ID}-quantity"]`)).toBeVisible();
  const card = await box(page.getByTestId("line-1"));
  expect(card.width, "a card fits the screen").toBeLessThanOrEqual(PHONE.width);

  const quantity = page.getByTestId("line-1-quantity");
  await quantity.fill("24.0000");
  await expect(quantity).toHaveAttribute("data-dirty", "true");

  await page.getByTestId("line-1-flag").click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe(`ack-${WARNING_ID}`);
});

test("the zoom buttons keep the middle of what's shown in the middle", async ({ page }) => {
  await stubReview(page);
  await openReview(page, "pages");

  const scroller = page.getByTestId("page-scroller");
  await scroller.scrollIntoViewIfNeeded();
  const area = await box(scroller);
  const middle = { x: area.x + area.width / 2, y: area.y + Math.min(area.height, 400) / 2 };

  for (const button of ["zoom-in", "zoom-in", "zoom-out"]) {
    const before = await pagePixelAt(page, middle.x, middle.y);
    await page.getByTestId(button).click();
    await page.waitForTimeout(100);
    const after = await pagePixelAt(page, middle.x, middle.y);
    expect(Math.abs(after.px - before.px), `${button}: across`).toBeLessThanOrEqual(2);
    expect(Math.abs(after.py - before.py), `${button}: down`).toBeLessThanOrEqual(2);
  }
});

const TENANT = "dddddddd-dddd-dddd-dddd-dddddddddddd";

test("the Console's review of a tenant's order fits 375 too", async ({ page }) => {
  await stubReview(page, { prefix: `/admin/tenants/${TENANT}/act/review` });
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants/${TENANT}/overview`, (route) =>
    route.fulfill({ json: { tenant: { id: TENANT, name: "Acme Test Prospect" } } }),
  );
  await openReview(page, "pages" as View, `/admin/tenants/${TENANT}/review/${DOCUMENT_ID}`);

  await expect(page.getByTestId("support-banner")).toBeVisible();
  expect(await sideways(page)).toBeLessThanOrEqual(0);
  expect(await inViewport(page, page.getByTestId("approve-button"))).toBe(true);
});
