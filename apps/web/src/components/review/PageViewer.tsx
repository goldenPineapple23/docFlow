"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { originalDocumentUrl, type CatalogError, type DocumentPage } from "@/lib/review";
import { API_BASE_URL, CatalogMessage, DownloadOriginal, Panel } from "./viewerParts";
import { midpoint, pagePointUnder, scrollToKeep, type Point } from "./zoomAnchor";

/**
 * A document's pages, as the parse job drew them (A2, A3), shown in the page
 * with DocFlow's own fit-to-width, zoom and pan (D-228 items 2 and 11; D-233).
 *
 * A page image is DocFlow's output, not the sender's bytes, so it is an
 * `<img>` in the page rather than a framed original (CLAUDE.md 7.12's
 * clarification). It is loaded across sites, and needs no CORS for that: an
 * `<img>` without `crossorigin` never asks for it, and nothing here reads its
 * pixels -- later highlight boxes are laid over it.
 *
 * **One retry per visit, never a loop.** A page whose image fails to load
 * mints the document's links once more and tries once again; failing that, it
 * shows the catalog's words for it (DOC-032). The budget belongs to one visit
 * to the page: a page that failed gets one more try only when the reviewer
 * returns to it (founder, 2026-11-04; D-233 item 6). The fresh links replace
 * every page's, so later pages use the new token.
 *
 * **Zoom keeps its centre (A5; D-234).** A pinch zooms about the point between
 * the fingers, and the buttons about the middle of the box, never the page's
 * top-left corner. At fit to width a one-finger swipe scrolls the page as on
 * any page; zoomed in, one finger pans the page image.
 *
 * **Fresh links before a page turn near expiry (A5; D-234).** The links last
 * `PAGE_TOKEN_LIFETIME_SECONDS`. A move to another page more than
 * `REMINT_MARGIN_SECONDS` before they run out asks for fresh ones first, timed
 * on this browser's own clock from when the links arrived (the answer carries
 * no server time). No timer: nothing is asked for unless the reviewer turns a
 * page, and that re-mint never spends the visit's retry.
 */

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;
const ZOOM_STEP = 1.25;

/** The page token's lifetime: `signed_urls.DEFAULT_TTL_SECONDS` (packages/core/tests/test_ci_guards.py keeps them equal). */
export const PAGE_TOKEN_LIFETIME_SECONDS = 300;
/** How long before the links run out a page turn asks for fresh ones (founder, 2026-11-05). */
export const REMINT_MARGIN_SECONDS = 60;

function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

export function PageViewer({
  documentId,
  filename,
  initialPages,
  pageUnavailable,
}: {
  documentId: string;
  filename: string;
  initialPages: DocumentPage[];
  pageUnavailable: CatalogError;
}) {
  const [pages, setPages] = useState(initialPages);
  const [current, setCurrent] = useState(0);
  // 1 is the page's width fitted to the panel's.
  const [zoom, setZoom] = useState(1);
  // Each move to a page is a new visit, with its own budget: the sets below
  // hold visits, not pages, so a re-mint still running when the reviewer
  // leaves can't spend the next visit's.
  const [visit, setVisit] = useState(0);
  const [retried, setRetried] = useState<ReadonlySet<number>>(new Set());
  const [failed, setFailed] = useState<ReadonlySet<number>>(new Set());
  // Moves on only when fresh links arrive, so the image loads again then,
  // and not on its old link while the new ones are being minted.
  const [generation, setGeneration] = useState(0);
  // True while a page turn waits for fresh links.
  const [refreshing, setRefreshing] = useState(false);

  const scroller = useRef<HTMLDivElement>(null);
  const pointers = useRef(new Map<number, Point>());
  const drag = useRef<{ start: Point; left: number; top: number } | null>(null);
  const pinch = useRef<{ distance: number; zoom: number; pagePoint: Point } | null>(null);
  // Where the next layout puts the scroll, so a point stays put as the zoom changes.
  const keep = useRef<{ pagePoint: Point; at: Point } | null>(null);
  // When the links in use arrived, on this browser's monotonic clock: set as
  // the viewer opens with them, and again with every fresh set.
  const linksArrived = useRef<number | null>(null);
  const remint = useRef<Promise<void> | null>(null);

  useEffect(() => {
    linksArrived.current = performance.now();
  }, []);

  const total = pages.length;
  const page = pages[current];
  const label = `Page ${current + 1} of ${total}`;

  // Applied after the new width is laid out: set before it, the browser
  // would clamp the scroll to the old, smaller page.
  useLayoutEffect(() => {
    const box = scroller.current;
    const target = keep.current;
    keep.current = null;
    if (!box || !target) return;
    const scroll = scrollToKeep(target.pagePoint, target.at, zoom);
    box.scrollLeft = scroll.x;
    box.scrollTop = scroll.y;
  }, [zoom]);

  function freshLinks(next: DocumentPage[]) {
    setPages(next);
    setGeneration((g) => g + 1);
    linksArrived.current = performance.now();
  }

  function markFailed(onVisit: number) {
    setFailed((before) => new Set(before).add(onVisit));
  }

  async function imageFailed(onVisit: number) {
    if (retried.has(onVisit)) {
      markFailed(onVisit);
      return;
    }
    setRetried((before) => new Set(before).add(onVisit));
    try {
      const fresh = await originalDocumentUrl(documentId);
      if (fresh.view === "pages" && fresh.pages && fresh.pages.length === total) {
        freshLinks(fresh.pages);
      } else {
        markFailed(onVisit);
      }
    } catch {
      markFailed(onVisit);
    }
  }

  /** Fresh links before the page loads, if the ones in use are near the end of their life. */
  function remintIfNearExpiry() {
    if (linksArrived.current === null) return;
    const age = (performance.now() - linksArrived.current) / 1000;
    if (age <= PAGE_TOKEN_LIFETIME_SECONDS - REMINT_MARGIN_SECONDS || remint.current) return;
    setRefreshing(true);
    remint.current = originalDocumentUrl(documentId)
      .then((fresh) => {
        if (fresh.view === "pages" && fresh.pages && fresh.pages.length === total) freshLinks(fresh.pages);
      })
      // A failure here loads the page on the links it has; its own retry is untouched.
      .catch(() => undefined)
      .finally(() => {
        remint.current = null;
        setRefreshing(false);
      });
  }

  function goTo(index: number) {
    setCurrent(index);
    setVisit((v) => v + 1);
    if (scroller.current) {
      scroller.current.scrollLeft = 0;
      scroller.current.scrollTop = 0;
    }
    remintIfNearExpiry();
  }

  /** A point on the screen, measured inside the scroll box's border. */
  function inBox(point: Point): Point {
    const box = scroller.current!;
    const rect = box.getBoundingClientRect();
    return { x: point.x - rect.left - box.clientLeft, y: point.y - rect.top - box.clientTop };
  }

  function scrollOf(box: HTMLDivElement): Point {
    return { x: box.scrollLeft, y: box.scrollTop };
  }

  /** Zoom to `next`, keeping the page point under `at` (a point inside the box) where it is. */
  function zoomAbout(next: number, at: Point) {
    const box = scroller.current;
    const clamped = clampZoom(next);
    if (box) keep.current = { pagePoint: pagePointUnder(scrollOf(box), at, zoom), at };
    setZoom(clamped);
  }

  /** The buttons zoom about the middle of the box. */
  function zoomByButton(next: number) {
    const box = scroller.current;
    const at = box ? { x: box.clientWidth / 2, y: box.clientHeight / 2 } : { x: 0, y: 0 };
    zoomAbout(next, at);
  }

  function fitToWidth() {
    const box = scroller.current;
    if (box) {
      // Keep the point in the middle of the box at the same height; back to the left edge.
      const at = { x: 0, y: box.clientHeight / 2 };
      const point = pagePointUnder(scrollOf(box), { x: box.clientWidth / 2, y: at.y }, zoom);
      keep.current = { pagePoint: { x: 0, y: point.y }, at };
    }
    setZoom(1);
  }

  function startDrag(point: Point) {
    const box = scroller.current;
    drag.current = box ? { start: point, left: box.scrollLeft, top: box.scrollTop } : null;
  }

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const active = [...pointers.current.values()];
    if (active.length === 1) {
      // At fit to width a finger scrolls the page, as on any page; a mouse
      // still drags.
      if (event.pointerType !== "touch" || zoom > 1) startDrag(active[0]);
    } else if (active.length === 2) {
      drag.current = null;
      const box = scroller.current;
      const centre = midpoint(active[0], active[1]);
      pinch.current = {
        distance: distanceOf(active[0], active[1]),
        zoom,
        pagePoint: box ? pagePointUnder(scrollOf(box), inBox(centre), zoom) : { x: 0, y: 0 },
      };
    }
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const active = [...pointers.current.values()];
    if (active.length === 2 && pinch.current && pinch.current.distance > 0) {
      const next = clampZoom((pinch.current.zoom * distanceOf(active[0], active[1])) / pinch.current.distance);
      // The page point first under the fingers follows their centre as it moves.
      if (scroller.current) keep.current = { pagePoint: pinch.current.pagePoint, at: inBox(midpoint(active[0], active[1])) };
      setZoom(next);
    } else if (active.length === 1 && drag.current && scroller.current) {
      scroller.current.scrollLeft = drag.current.left - (active[0].x - drag.current.start.x);
      scroller.current.scrollTop = drag.current.top - (active[0].y - drag.current.start.y);
    }
  }

  function onPointerEnd(event: React.PointerEvent<HTMLDivElement>) {
    pointers.current.delete(event.pointerId);
    pinch.current = null;
    const active = [...pointers.current.values()];
    drag.current = null;
    if (active.length === 1 && (event.pointerType !== "touch" || zoom > 1)) startDrag(active[0]);
  }

  const showable = page !== undefined && page.drawn && !failed.has(visit);

  return (
    <div className="flex h-full flex-col gap-2" data-testid="page-viewer">
      <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
        <button
          type="button"
          data-testid="page-prev"
          onClick={() => goTo(current - 1)}
          disabled={current === 0}
          className={BUTTON}
        >
          Previous page
        </button>
        <span data-testid="page-position">{label}</span>
        <button
          type="button"
          data-testid="page-next"
          onClick={() => goTo(current + 1)}
          disabled={current >= total - 1}
          className={BUTTON}
        >
          Next page
        </button>
        <span className="ml-auto flex items-center gap-2">
          <button
            type="button"
            data-testid="zoom-out"
            aria-label="Zoom out"
            onClick={() => zoomByButton(zoom / ZOOM_STEP)}
            disabled={zoom <= MIN_ZOOM}
            className={BUTTON}
          >
            −
          </button>
          <span data-testid="zoom-level" className="w-12 text-center tabular-nums">
            {`${Math.round(zoom * 100)}%`}
          </span>
          <button
            type="button"
            data-testid="zoom-in"
            aria-label="Zoom in"
            onClick={() => zoomByButton(zoom * ZOOM_STEP)}
            disabled={zoom >= MAX_ZOOM}
            className={BUTTON}
          >
            +
          </button>
          <button type="button" data-testid="zoom-fit" onClick={fitToWidth} className={BUTTON}>
            Fit to width
          </button>
        </span>
      </div>
      {showable ? (
        <div
          ref={scroller}
          data-testid="page-scroller"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerEnd}
          // Zoomed in, the page's own gestures: drag to pan, two fingers to
          // zoom. At fit to width (or smaller) a vertical swipe is the
          // browser's, so the page scrolls; two fingers still zoom. The
          // buttons above do the same without a gesture.
          style={{ touchAction: zoom > 1 ? "none" : "pan-y" }}
          className="min-h-0 w-full flex-1 cursor-grab overflow-auto rounded-xl border border-gray-200 bg-gray-100 active:cursor-grabbing"
        >
          {refreshing ? null : (
            <>
              {/* A plain img on purpose: next/image would fetch the signed link
                  through Next's own image server. */}
              {/* eslint-disable-next-line @next/next/no-img-element -- see above */}
              <img
                // Remounted on each visit and when fresh links arrive, so it loads
                // again even if the fresh link happens to read the same.
                key={`${visit}:${generation}`}
                data-testid="page-image"
                src={`${API_BASE_URL}${page.url}`}
                alt={label}
                width={page.width ?? undefined}
                height={page.height ?? undefined}
                referrerPolicy="no-referrer"
                draggable={false}
                onError={() => void imageFailed(visit)}
                style={{ width: `${zoom * 100}%`, maxWidth: "none", height: "auto" }}
                className="block select-none bg-white"
              />
            </>
          )}
        </div>
      ) : (
        <Panel testId="page-unavailable">
          <CatalogMessage entry={pageUnavailable} />
          <DownloadOriginal documentId={documentId} filename={filename} />
        </Panel>
      )}
    </div>
  );
}

// 44 x 44 wherever the layout stacks (below 1280): tapped between reads of the document (D-234).
const BUTTON =
  "rounded-md border border-gray-300 bg-white px-2.5 py-1 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-40 max-xl:min-h-11 max-xl:min-w-11";

function distanceOf(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}
