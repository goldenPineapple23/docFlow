import { type CDPSession, type Page } from "@playwright/test";
import { expect, test } from "./networkGuard";
import { PHONE, openReview, pagePixelAt, stubReview } from "./fixtures/review375";

/**
 * The page image's gestures at 375 px (A5; D-234): a swipe at fit scrolls the
 * page, a pinch zooms about the pinch's centre, a drag pans once zoomed.
 *
 * Chromium only, and never skipped: the touches go through CDP, which only
 * Chromium has (Playwright's own Touchscreen only taps), so the `webkit-375`
 * project doesn't match this file (playwright.config.ts). Chromium's touch
 * emulation isn't iOS Safari; a real iPhone checks these at A6.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

test.use({ viewport: PHONE, isMobile: true, hasTouch: true });

type Finger = { x: number; y: number };

async function touch(cdp: CDPSession, type: "touchStart" | "touchMove" | "touchEnd", fingers: Finger[]) {
  await cdp.send("Input.dispatchTouchEvent", {
    type,
    touchPoints: fingers.map(({ x, y }, id) => ({ x: Math.round(x), y: Math.round(y), id })),
  });
}

/** Moves the fingers from where they are to where they end, in steps, then lifts them. */
async function gesture(cdp: CDPSession, from: Finger[], to: Finger[], steps = 10) {
  await touch(cdp, "touchStart", from);
  for (let step = 1; step <= steps; step++) {
    await touch(
      cdp,
      "touchMove",
      from.map((f, i) => ({ x: f.x + ((to[i].x - f.x) * step) / steps, y: f.y + ((to[i].y - f.y) * step) / steps })),
    );
  }
  await touch(cdp, "touchEnd", []);
}

async function openPages(page: Page) {
  await stubReview(page);
  await openReview(page, "pages");
  const cdp = await page.context().newCDPSession(page);
  // The page image at the top of the screen, so the fingers land on it and
  // not on the action bar fixed along the bottom.
  await page.getByTestId("page-scroller").evaluate((el) => el.scrollIntoView({ block: "start" }));
  return cdp;
}

/**
 * The part of the page image a finger can touch, in screen coordinates: on
 * screen, inside its box, and above the action bar.
 */
async function shownImage(page: Page) {
  const shown = await page.evaluate(() => {
    const image = document.querySelector("[data-testid=page-image]")!.getBoundingClientRect();
    const scroller = document.querySelector("[data-testid=page-scroller]")!.getBoundingClientRect();
    // The bar, where it sits along the bottom; anywhere else it covers nothing.
    const bar = document.querySelector("[data-testid=action-bar]")?.getBoundingClientRect();
    const barTop = bar && bar.bottom >= window.innerHeight - 1 ? bar.top : window.innerHeight;
    const left = Math.max(image.left, scroller.left, 0);
    const top = Math.max(image.top, scroller.top, 0);
    const right = Math.min(image.right, scroller.right, window.innerWidth);
    const bottom = Math.min(image.bottom, scroller.bottom, window.innerHeight, barTop);
    return { left, top, width: right - left, height: bottom - top };
  });
  expect(shown.height, "enough of the page image is clear of the bar to touch").toBeGreaterThanOrEqual(150);
  // Touches land in the visual viewport. A page wider than the screen lets a
  // mobile browser move or zoom that viewport on its own (main did, by
  // 196 px), and then a touch lands somewhere else than these coordinates.
  const viewport = await page.evaluate(() => {
    const v = window.visualViewport!;
    return { scale: v.scale, left: v.offsetLeft, top: v.offsetTop };
  });
  expect(viewport, "the visual viewport is the page's, unmoved and unzoomed").toEqual({ scale: 1, left: 0, top: 0 });
  return shown;
}

/** What is under a point: the page image, or something over it. */
function hitsTheImage(page: Page, at: Finger): Promise<boolean> {
  return page.evaluate(
    ({ x, y }) => document.elementFromPoint(x, y)?.getAttribute("data-testid") === "page-image",
    at,
  );
}

test("a swipe over the page at fit to width scrolls the page", async ({ page }) => {
  const cdp = await openPages(page);
  const shown = await shownImage(page);
  const start = { x: shown.left + shown.width / 2, y: shown.top + shown.height * 0.8 };
  expect(await hitsTheImage(page, start), "the swipe starts on the page image").toBe(true);
  const scroller = page.getByTestId("page-scroller");
  const read = () => scroller.evaluate((el) => ({ box: el.scrollTop, room: el.scrollHeight - el.clientHeight, page: window.scrollY }));
  const before = await read();

  // As in any scroll box on any page: a swipe scrolls the box until it ends
  // (at fit the page image is a little taller than its box), and the next
  // swipe scrolls the page. Nothing is trapped.
  await gesture(cdp, [start], [{ x: start.x, y: start.y - 250 }]);
  await page.waitForTimeout(300);
  const first = await read();
  expect(first.box - before.box, "the first swipe scrolled the page image's box to its end").toBe(before.room - before.box);

  await gesture(cdp, [start], [{ x: start.x, y: start.y - 250 }]);
  await page.waitForTimeout(300);
  const second = await read();
  expect(second.page - before.page, "the next swipe scrolled the page").toBeGreaterThanOrEqual(100);
  await expect(page.getByTestId("zoom-level")).toHaveText("100%");
});

test("a pinch zooms about the pinch's centre, not the page's corner", async ({ page }) => {
  const cdp = await openPages(page);
  const shown = await shownImage(page);
  // An interior point, away from every edge, so no scroll limit can move it.
  const centre = { x: shown.left + shown.width * 0.75, y: shown.top + shown.height * 0.6 };
  expect(await hitsTheImage(page, centre), "the pinch is on the page image").toBe(true);
  const before = await pagePixelAt(page, centre.x, centre.y);

  await gesture(
    cdp,
    [
      { x: centre.x - 30, y: centre.y },
      { x: centre.x + 30, y: centre.y },
    ],
    [
      { x: centre.x - 60, y: centre.y },
      { x: centre.x + 60, y: centre.y },
    ],
  );
  await expect(page.getByTestId("zoom-level")).toHaveText("200%");

  const after = await pagePixelAt(page, centre.x, centre.y);
  expect(Math.abs(after.px - before.px), "the pinch centre stayed put across").toBeLessThanOrEqual(2);
  expect(Math.abs(after.py - before.py), "the pinch centre stayed put down").toBeLessThanOrEqual(2);
});

test("once zoomed in, one finger pans the page image and leaves the page where it is", async ({ page }) => {
  const cdp = await openPages(page);
  await page.getByTestId("zoom-in").click();
  await page.getByTestId("zoom-in").click();
  await page.getByTestId("zoom-in").click();
  await expect(page.getByTestId("zoom-level")).toHaveText("195%");

  const shown = await shownImage(page);
  const start = { x: shown.left + shown.width * 0.7, y: shown.top + shown.height * 0.5 };
  expect(await hitsTheImage(page, start), "the drag starts on the page image").toBe(true);
  const scroller = page.getByTestId("page-scroller");
  const before = await scroller.evaluate((el) => ({ left: el.scrollLeft, page: window.scrollY }));

  await gesture(cdp, [start], [{ x: start.x - 120, y: start.y }]);
  await page.waitForTimeout(200);

  const after = await scroller.evaluate((el) => ({ left: el.scrollLeft, page: window.scrollY }));
  expect(after.left - before.left, "the page image panned").toBeGreaterThanOrEqual(60);
  expect(Math.abs(after.page - before.page), "the page itself didn't move").toBeLessThanOrEqual(2);
});
