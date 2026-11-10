import { describe, expect, it } from "vitest";
import { midpoint, pagePointUnder, scrollToKeep } from "./zoomAnchor";

/**
 * The arithmetic of zooming about a point (A5; D-234). A browser has to lay
 * the page out for the scroll to take; that is proven in a real browser by
 * e2e/review-375-touch.spec.ts and e2e/review-375.spec.ts.
 */
describe("zooming about a point", () => {
  it("keeps the page point under a pinch where it was", () => {
    const at = { x: 255, y: 266 };
    const point = pagePointUnder({ x: 0, y: 0 }, at, 1);
    const scroll = scrollToKeep(point, at, 2);

    expect(scroll).toEqual({ x: 255, y: 266 });
    // Under `at` after the zoom: the same page point.
    expect(pagePointUnder(scroll, at, 2)).toEqual(point);
  });

  it("is not the old top-left behaviour", () => {
    const at = { x: 255, y: 266 };
    const point = pagePointUnder({ x: 0, y: 0 }, at, 1);
    // Zooming without moving the scroll halves the point's distance from the corner.
    expect(pagePointUnder({ x: 0, y: 0 }, at, 2)).toEqual({ x: point.x / 2, y: point.y / 2 });
  });

  it("follows the fingers' centre when it moves during the pinch", () => {
    const point = pagePointUnder({ x: 40, y: 10 }, { x: 100, y: 100 }, 1.5);
    const movedTo = { x: 130, y: 90 };
    const scroll = scrollToKeep(point, movedTo, 2.5);

    expect(pagePointUnder(scroll, movedTo, 2.5).x).toBeCloseTo(point.x, 10);
    expect(pagePointUnder(scroll, movedTo, 2.5).y).toBeCloseTo(point.y, 10);
  });

  it("works when zooming out too", () => {
    const at = { x: 170, y: 190 };
    const point = pagePointUnder({ x: 120, y: 300 }, at, 1.5625);
    const scroll = scrollToKeep(point, at, 1.25);

    expect(pagePointUnder(scroll, at, 1.25).x).toBeCloseTo(point.x, 10);
    expect(pagePointUnder(scroll, at, 1.25).y).toBeCloseTo(point.y, 10);
  });

  it("finds a pinch's centre", () => {
    expect(midpoint({ x: 100, y: 100 }, { x: 200, y: 140 })).toEqual({ x: 150, y: 120 });
  });
});
