"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getAllowance, getHeld, type Allowance, type Held } from "@/lib/held";

/**
 * The two non-blocking notices a tenant may see under the header (CLAUDE.md
 * Section 7.16.1, 7.16.4; D-126):
 *
 *   - the allowance banner at 80% and 100% ("You've used 312 of 300 documents
 *     included in Starter this month ..."), and
 *   - a note that some documents are being held, with the reason in plain
 *     English and a way to the list.
 *
 * Neither ever stops the person doing their work, and neither writes its own
 * wording: both render catalog entries the API supplies (7.16.5). If the
 * numbers can't be fetched the strip simply shows nothing -- a notice must
 * never get in the way of reviewing an order.
 */
export function HeldStrip() {
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  const [held, setHeld] = useState<Held | null>(null);

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

  const banner = allowance?.banner ?? null;
  const groups = held?.groups ?? [];
  if (!banner && groups.length === 0) return null;

  return (
    <div className="mx-auto max-w-6xl space-y-2 px-6 pt-4">
      {banner ? (
        <div
          data-testid="allowance-banner"
          className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950"
        >
          <p className="font-medium">{banner.title}</p>
          <p className="mt-0.5">{banner.message}</p>
          <p className="mt-0.5 text-sky-900">{banner.action}</p>
        </div>
      ) : null}
      {groups.length > 0 ? (
        <div
          data-testid="held-strip"
          className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950"
        >
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
