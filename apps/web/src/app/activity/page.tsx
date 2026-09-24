"use client";

import { useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { ActivityList, ACTIVITY_VERBS } from "@/components/ActivityList";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { getActivity, type ActivityPage } from "@/lib/home";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";

/**
 * The account's activity trail (Section 6's Phase 5 line: "audit log view").
 * Slice 5.8b, D-130.
 *
 * Reviewers see it as well as admins: the people doing the work are the ones
 * who need to know what a colleague already did. It reads the same rows as the
 * dashboard's short list, filtered and paged, so the two cannot disagree.
 */

const PAGE_SIZE = 50;

const KIND_LABELS: Record<string, string> = {
  edited: "Edited",
  approved: "Approved",
  rejected: "Rejected",
  reopened: "Reopened",
  exported: "Exported",
  released: "Released from hold",
  invited: "Invited",
  removed: "Removed from team",
};

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-3 py-1 text-sm transition-colors ${
        active
          ? "border-slate-900 bg-slate-900 font-medium text-white"
          : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
      }`}
    >
      {label}
    </button>
  );
}

export default function ActivityPageScreen() {
  const [page, setPage] = useState<ActivityPage | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);
  const [kinds, setKinds] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);

  // The effect only ever sets state from an async callback: React's
  // `set-state-in-effect` rule refuses a synchronous one, and rightly -- the
  // "loading" state here is simply "no page yet", which the handlers below
  // arrange by clearing it before they change the query.
  useEffect(() => {
    let cancelled = false;
    getActivity({ kinds, limit: PAGE_SIZE, offset })
      .then((data) => {
        if (cancelled) return;
        setPage(data);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [kinds, offset]);

  // One filter at a time (the founder's call in the 5.8d walkthrough): a chip
  // shows only that kind, and choosing the chip already on goes back to
  // everything. Choosing starts again at the newest row: staying on page four
  // of a list you just narrowed shows an empty screen for no reason.
  function choose(kind: string) {
    setPage(null);
    setOffset(0);
    setKinds((prev) => (prev.length === 1 && prev[0] === kind ? [] : [kind]));
  }

  function go(next: number) {
    setPage(null);
    setOffset(next);
  }

  const total = page?.total ?? 0;
  const items = page?.items ?? [];
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + items.length;
  const available = page?.kinds ?? (Object.keys(KIND_LABELS) as (keyof typeof ACTIVITY_VERBS)[]);

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-4xl p-6">
        <h1 className="text-xl font-semibold">Activity</h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-600">
          Everything that has happened on this account: who changed which order, who joined or
          left the team, and when. It never shows what a value was changed to — open the order
          for that.
        </p>

        <div data-testid="activity-filters" className="mt-4 flex flex-wrap gap-2">
          <Chip
            label="Everything"
            active={kinds.length === 0}
            onClick={() => {
              go(0);
              setKinds([]);
            }}
          />
          {available.map((kind) => (
            <Chip
              key={kind}
              label={KIND_LABELS[kind] ?? kind}
              active={kinds.includes(kind)}
              onClick={() => choose(kind)}
            />
          ))}
        </div>

        {error ? (
          <div className="mt-4 max-w-2xl">
            <CatalogErrorBox error={error} />
          </div>
        ) : null}

        {!error && page === null ? (
          <p className="mt-4 text-sm text-gray-500">Loading…</p>
        ) : null}

        {!error && page ? (
          items.length === 0 ? (
            <p
              data-testid="activity-empty"
              className="mt-4 rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-600"
            >
              {kinds.length === 0
                ? "Nothing has happened on this account yet."
                : "Nothing of that kind has happened on this account."}
            </p>
          ) : (
            <>
              <ActivityList items={items} testId="activity" showDate />
              <div className="mt-3 flex items-center justify-between text-sm">
                <p data-testid="activity-count" className="text-gray-600">
                  Showing {from}–{to} of {total}
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    data-testid="newer"
                    disabled={offset === 0}
                    onClick={() => go(Math.max(0, offset - PAGE_SIZE))}
                    className="rounded-md border border-gray-300 bg-white px-3 py-1 font-medium text-gray-700 disabled:opacity-40"
                  >
                    ← Newer
                  </button>
                  <button
                    type="button"
                    data-testid="older"
                    disabled={to >= total}
                    onClick={() => go(offset + PAGE_SIZE)}
                    className="rounded-md border border-gray-300 bg-white px-3 py-1 font-medium text-gray-700 disabled:opacity-40"
                  >
                    Older →
                  </button>
                </div>
              </div>
            </>
          )
        ) : null}
      </main>
    </>
  );
}
