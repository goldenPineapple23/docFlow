"use client";

import { useRef, useState } from "react";
import { originalDocumentUrl, type CatalogError, type DocumentPage } from "@/lib/review";
import { API_BASE_URL, CatalogMessage, DownloadOriginal, Panel } from "./viewerParts";

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
 * **One retry, never a loop.** A page whose image fails to load mints the
 * document's links once more and tries once again; failing that, it shows the
 * catalog's words for it (DOC-032). The fresh links replace every page's, so
 * later pages use the new token.
 */

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;
const ZOOM_STEP = 1.25;

function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

type Point = { x: number; y: number };

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
  const [retried, setRetried] = useState<ReadonlySet<number>>(new Set());
  const [failed, setFailed] = useState<ReadonlySet<number>>(new Set());
  // Moves on only when fresh links arrive, so the image loads again then,
  // and not on its old link while the new ones are being minted.
  const [generation, setGeneration] = useState(0);

  const scroller = useRef<HTMLDivElement>(null);
  const pointers = useRef(new Map<number, Point>());
  const drag = useRef<{ start: Point; left: number; top: number } | null>(null);
  const pinch = useRef<{ distance: number; zoom: number } | null>(null);

  const total = pages.length;
  const page = pages[current];
  const label = `Page ${current + 1} of ${total}`;

  function markFailed(index: number) {
    setFailed((before) => new Set(before).add(index));
  }

  async function imageFailed(index: number) {
    if (retried.has(index)) {
      markFailed(index);
      return;
    }
    setRetried((before) => new Set(before).add(index));
    try {
      const fresh = await originalDocumentUrl(documentId);
      if (fresh.view === "pages" && fresh.pages && fresh.pages.length === total) {
        setPages(fresh.pages);
        setGeneration((g) => g + 1);
      } else {
        markFailed(index);
      }
    } catch {
      markFailed(index);
    }
  }

  function goTo(index: number) {
    setCurrent(index);
    if (scroller.current) {
      scroller.current.scrollLeft = 0;
      scroller.current.scrollTop = 0;
    }
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
      startDrag(active[0]);
    } else if (active.length === 2) {
      drag.current = null;
      pinch.current = { distance: distanceOf(active[0], active[1]), zoom };
    }
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const active = [...pointers.current.values()];
    if (active.length === 2 && pinch.current && pinch.current.distance > 0) {
      setZoom(clampZoom((pinch.current.zoom * distanceOf(active[0], active[1])) / pinch.current.distance));
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
    if (active.length === 1) startDrag(active[0]);
  }

  const showable = page !== undefined && page.drawn && !failed.has(current);

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
            onClick={() => setZoom((z) => clampZoom(z / ZOOM_STEP))}
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
            onClick={() => setZoom((z) => clampZoom(z * ZOOM_STEP))}
            disabled={zoom >= MAX_ZOOM}
            className={BUTTON}
          >
            +
          </button>
          <button type="button" data-testid="zoom-fit" onClick={() => setZoom(1)} className={BUTTON}>
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
          // The page's own gestures: drag to pan, two fingers to zoom. The
          // buttons above do the same without a gesture.
          style={{ touchAction: "none" }}
          className="min-h-0 w-full flex-1 cursor-grab overflow-auto rounded-xl border border-gray-200 bg-gray-100 active:cursor-grabbing"
        >
          {/* A plain img on purpose: next/image would fetch the signed link
              through Next's own image server. */}
          {/* eslint-disable-next-line @next/next/no-img-element -- see above */}
          <img
            // Remounted when fresh links arrive, so it loads again even if the
            // fresh link happens to read the same.
            key={`${current}:${generation}`}
            data-testid="page-image"
            src={`${API_BASE_URL}${page.url}`}
            alt={label}
            width={page.width ?? undefined}
            height={page.height ?? undefined}
            referrerPolicy="no-referrer"
            draggable={false}
            onError={() => void imageFailed(current)}
            style={{ width: `${zoom * 100}%`, maxWidth: "none", height: "auto" }}
            className="block select-none bg-white"
          />
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

const BUTTON =
  "rounded-md border border-gray-300 bg-white px-2.5 py-1 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-40";

function distanceOf(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}
