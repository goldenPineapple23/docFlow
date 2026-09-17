"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ReviewApiError, listDocuments, type QueueDocument } from "@/lib/review";
import { ConfidenceBadge } from "@/components/review/confidence";
import { AppHeader } from "@/components/AppHeader";

/**
 * The review queue (CLAUDE.md Section 7.3, Phase 3).
 *
 * Oldest first, because the oldest unreviewed order is the one a buyer is
 * waiting on. The flags that change how a reviewer should approach a
 * document — held-for-review signals, a possible duplicate, a possible
 * change order, a suspected injection — are on the row, not behind a click.
 */

const FILTERS: Array<{ value: string; label: string }> = [
  { value: "needs_review", label: "Needs review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "exported", label: "Exported" },
  { value: "failed", label: "Failed" },
  { value: "", label: "All" },
];

export default function ReviewQueuePage() {
  const [status, setStatus] = useState("needs_review");
  // The loaded filter travels WITH the rows, so "are we showing stale data
  // for a filter the user just changed" is derived rather than a second
  // piece of state reset synchronously inside the effect.
  const [loaded, setLoaded] = useState<{ status: string; documents: QueueDocument[] } | null>(null);
  const [error, setError] = useState<ReviewApiError | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDocuments(status || undefined)
      .then(({ documents }) => {
        if (cancelled) return;
        setLoaded({ status, documents });
        setError(null);
      })
      .catch((e: ReviewApiError) => {
        if (!cancelled) setError(e);
      });
    return () => {
      cancelled = true;
    };
  }, [status]);

  const stale = loaded === null || loaded.status !== status;
  const documents = stale ? null : loaded.documents;

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-6xl p-6">
      <h1 className="text-xl font-semibold">Orders to review</h1>

      <div className="mt-4 flex flex-wrap gap-2">
        {FILTERS.map((filter) => (
          <button
            key={filter.value}
            type="button"
            onClick={() => setStatus(filter.value)}
            aria-pressed={status === filter.value}
            className={[
              "rounded border px-3 py-1 text-sm",
              status === filter.value
                ? "border-blue-500 bg-blue-50 text-blue-900"
                : "border-gray-300",
            ].join(" ")}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {error ? (
        <div role="alert" className="mt-6 rounded border border-red-300 bg-red-50 p-4">
          <p className="font-medium">{error.catalog.title}</p>
          <p className="mt-1 text-sm">{error.catalog.message}</p>
          <p className="mt-1 text-sm text-gray-700">{error.catalog.action}</p>
        </div>
      ) : null}

      {documents === null && !error ? (
        <p className="mt-6 text-sm text-gray-500">Loading…</p>
      ) : null}

      {documents !== null && documents.length === 0 ? (
        <p data-testid="queue-empty" className="mt-6 text-sm text-gray-600">
          Nothing here right now.
        </p>
      ) : null}

      {documents !== null && documents.length > 0 ? (
        <table className="mt-6 w-full border-collapse text-sm">
          <caption className="sr-only">Orders waiting to be reviewed, oldest first</caption>
          <thead>
            <tr className="border-b border-gray-300 text-left">
              <th scope="col" className="py-2 pr-3">PO number</th>
              <th scope="col" className="py-2 pr-3">Buyer</th>
              <th scope="col" className="py-2 pr-3">Total</th>
              <th scope="col" className="py-2 pr-3">Received</th>
              <th scope="col" className="py-2 pr-3">Confidence</th>
              <th scope="col" className="py-2 pr-3">Checks</th>
              <th scope="col" className="py-2">Flags</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr key={doc.id} data-testid={`queue-row-${doc.id}`} className="border-b border-gray-200">
                <td className="py-2 pr-3">
                  <Link href={`/review/${doc.id}`} className="text-blue-700 underline">
                    {doc.po_number ?? doc.original_filename}
                  </Link>
                </td>
                <td className="py-2 pr-3">{doc.buyer_name ?? "—"}</td>
                <td className="py-2 pr-3 font-mono">
                  {doc.order_total ?? "—"} {doc.currency ?? ""}
                </td>
                <td className="py-2 pr-3">
                  {doc.created_at ? new Date(doc.created_at).toLocaleDateString() : "—"}
                </td>
                <td className="py-2 pr-3">
                  <ConfidenceBadge value={doc.overall_confidence} />
                </td>
                <td className="py-2 pr-3">
                  {doc.open_warnings > 0 ? (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-900">
                      {doc.open_warnings} to look at
                    </span>
                  ) : (
                    <span className="text-xs text-gray-500">clear</span>
                  )}
                </td>
                <td className="py-2 text-xs">
                  <Flags doc={doc} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      </main>
    </>
  );
}

function Flags({ doc }: { doc: QueueDocument }) {
  const flags: string[] = [];
  if (doc.injection_suspected) flags.push("Embedded instruction");
  if (doc.is_possible_duplicate) flags.push("Possible duplicate");
  if (doc.is_possible_change_order) flags.push("Possible change order");
  if (doc.is_test_batch) flags.push("Setup batch");

  if (flags.length === 0) return <span className="text-gray-400">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {flags.map((flag) => (
        <span key={flag} className="rounded bg-gray-100 px-1.5 py-0.5">
          {flag}
        </span>
      ))}
    </span>
  );
}
