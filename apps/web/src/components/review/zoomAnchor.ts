/**
 * Zooming about a point (A5; D-234): the point of the page under the pinch's
 * centre, or under the middle of the box for the buttons, stays under it.
 *
 * Positions are in CSS pixels. `at` is a point measured from the scroll
 * box's top-left inside its border; `scroll` is the box's scroll position.
 * The page image starts at the box's content origin and scales with the
 * zoom, so a point on it is `(scroll + at) / zoom` in unzoomed pixels.
 */

export type Point = { x: number; y: number };

/** The page point under `at`, in unzoomed pixels. */
export function pagePointUnder(scroll: Point, at: Point, zoom: number): Point {
  return { x: (scroll.x + at.x) / zoom, y: (scroll.y + at.y) / zoom };
}

/** The scroll position that puts `pagePoint` under `at` at `zoom`. */
export function scrollToKeep(pagePoint: Point, at: Point, zoom: number): Point {
  return { x: pagePoint.x * zoom - at.x, y: pagePoint.y * zoom - at.y };
}

/** The middle of two points: the centre of a pinch. */
export function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}
