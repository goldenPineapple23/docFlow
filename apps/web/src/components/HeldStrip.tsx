"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getAllowance, getHeld, type Allowance, type Held } from "@/lib/held";

/**
 * The two non-blocking notices a tenant may see on the Purchase orders list
 * (CLAUDE.md Section 7.16.1, 7.16.4; D-126, D-129):
 *
 *   - the allowance notice, which the API sends only once the plan is nearly
 *     spent ("You've used 312 of 300 documents included in Starter this month
 *     ..."). The month's running numbers belong on the admin's dashboard; a
 *     reviewer is interrupted here only near the limit. And
 *   - a note that some documents are being held, with the reason in plain
 *     English and a way to the list.
 *
 * Neither ever stops the person doing their work, and neither writes its own
 * wording: both render catalog entries the API supplies (7.16.5). If the
 * numbers can't be fetched the strip simply shows nothing.
 *
 * Each has its own X. A dismissal is remembered in this browser (a per-person
 * convenience, never something the server relies on) and holds only until the
 * notice changes: a higher allowance threshold, a new month, or a different set
 * of held documents brings it back. It is deliberately shown on the list only,
 * never while a person is reviewing an order.
 */

const STORAGE_KEY = "docflow.dismissed-notices";

function readDismissed(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return []; // storage blocked or unavailable: the notice just shows
  }
}

function remember(id: string): void {
  try {
    const next = [...new Set([...readDismissed(), id])].slice(-50);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Not remembered; it will show again next time.
  }
}

function DismissButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="absolute right-2 top-2 grid h-6 w-6 place-items-center rounded text-lg leading-none text-current opacity-60 hover:bg-black/5 hover:opacity-100"
    >
      ×
    </button>
  );
}

export function HeldStrip() {
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  const [held, setHeld] = useState<Held | null>(null);
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);

  useEffect(() => {
    let cancelled = false;
    getAllowance()
      .then((a) => {
        if (!cancelled) setAllowance(a);
      })
      .catch(() => {});
    getHeld()
      .then((h) => {
        if (!cancelled) setHeld(h);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  function dismiss(id: string) {
    remember(id);
    setDismissed((prev) => [...prev, id]);
  }

  const banner = allowance?.banner ?? null;
  const bannerId = banner && allowance ? `allowance:${allowance.month}:${banner.threshold_pct}` : null;
  const groups = held?.groups ?? [];
  const heldId = groups.length > 0 ? `held:${groups.map((g) => `${g.reason}=${g.count}`).join("|")}` : null;

  const showBanner = banner !== null && bannerId !== null && !dismissed.includes(bannerId);
  const showHeld = heldId !== null && !dismissed.includes(heldId);
  if (!showBanner && !showHeld) return null;

  return (
    <div className="mt-4 space-y-2">
      {showBanner && banner && bannerId ? (
        <div
          data-testid="allowance-banner"
          className="relative rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 pr-10 text-sm text-sky-950"
        >
          <DismissButton label="Hide this message" onClick={() => dismiss(bannerId)} />
          <p className="font-medium">{banner.title}</p>
          <p className="mt-0.5">{banner.message}</p>
          <p className="mt-0.5 text-sky-900">{banner.action}</p>
        </div>
      ) : null}
      {showHeld && heldId ? (
        <div
          data-testid="held-strip"
          className="relative rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 pr-10 text-sm text-amber-950"
        >
          <DismissButton label="Hide this message" onClick={() => dismiss(heldId)} />
          <p className="font-medium">
            {held?.total} {held?.total === 1 ? "document is" : "documents are"} being held
          </p>
          {groups.map((g) => (
            <p key={g.reason} className="mt-0.5">
              {g.count} — {g.message}
            </p>
          ))}
          <Link href="/held" className="mt-1 inline-block font-medium underline">
            Open Held for review →
          </Link>
        </div>
      ) : null}
    </div>
  );
}
