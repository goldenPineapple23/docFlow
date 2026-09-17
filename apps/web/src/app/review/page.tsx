"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ReviewApiError, listDocuments, type QueueDocument } from "@/lib/review";
import { ConfidenceBadge } from "@/components/review/confidence";
import { PILL, StatusBadge } from "@/components/StatusBadge";
import { AppHeader } from "@/components/AppHeader";

/**
 * The review queue (CLAUDE.md Section 7.3, Phase 3).
 *
 * Oldest first, because the oldest unreviewed order is the one a buyer is
 * waiting on. The flags that change how a reviewer should approach a
 * document — held-for-review signals, a possible duplicate, a possible
 * change order, a suspected injection — are on the row, not behind a click.
 */

/**
 * Tab labels are what a warehouse or office person reads, not what the
 * database column says.
 *
 * "Exported" was read by the first walkthrough tester as possibly meaning
 * *imported* -- the two words swap easily when you are moving quickly. It is
 * now "Exported to file", which names the thing that happened and cannot be
 * confused with intake. Each tab also carries a one-line explanation,
 * because the tester's first question was what this page even was.
 */
const FILTERS: Array<{ value: string; label: string; blurb: string }> = [
  {
    value: "needs_review",
    label: "Needs review",
    blurb:
      "Every order lands here first. A person checks each one before it counts — DocFlow never approves an order by itself.",
  },
  {
    value: "approved",
    label: "Approved",
    blurb: "Checked by a person and ready to hand to your accounting or ERP system.",
  },
  { value: "rejected", label: "Rejected", blurb: "Set aside by a reviewer, with the reason recorded." },
  {
    value: "exported",
    label: "Exported to file",
    blurb: "Approved orders that have already been downloaded as a file.",
  },
  { value: "failed", label: "Couldn't be read", blurb: "DocFlow couldn't read these — the sender may need to resend." },
  { value: "", label: "All", blurb: "Every order on this account." },
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
      <h1 className="text-xl font-semibold">Purchase orders</h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        Every purchase order that arrives — by email or upload — is read by DocFlow and then
        shown to a person before it counts. Nothing is approved automatically, so this list is
        the whole account, not just the ones that looked wrong.
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        {FILTERS.map((filter) => (
          <button
            key={filter.value}
            type="button"
            onClick={() => setStatus(filter.value)}
            aria-pressed={status === filter.value}
            title={filter.blurb}
            className={[
              "rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors",
              status === filter.value
                ? "bg-slate-800 text-white shadow-sm"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200",
            ].join(" ")}
          >
            {filter.label}
          </button>
        ))}
      </div>

      <p data-testid="tab-blurb" className="mt-2 text-sm text-gray-600">
        {FILTERS.find((f) => f.value === status)?.blurb}
      </p>

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
        <div className="mt-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.06),0_8px_24px_-12px_rgba(15,23,42,0.25)]">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Purchase orders, oldest first</caption>
          <thead>
            <tr className="bg-slate-800 text-left text-[11px] uppercase tracking-[0.06em] text-slate-200">
              <th scope="col" className="px-4 py-3 font-semibold">PO number</th>
              <th scope="col" className="px-4 py-3 font-semibold">Buyer</th>
              <th scope="col" className="px-4 py-3 text-right font-semibold">Total</th>
              <th scope="col" className="px-4 py-3 font-semibold">Received</th>
              <th scope="col" className="px-4 py-3 font-semibold">Status</th>
              <th scope="col" className="px-4 py-3 font-semibold">Confidence</th>
              <th scope="col" className="px-4 py-3 font-semibold">Checks</th>
              <th scope="col" className="px-4 py-3 font-semibold">Flags</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr
                key={doc.id}
                data-testid={`queue-row-${doc.id}`}
                className="border-b border-slate-100 transition-colors last:border-0 odd:bg-white even:bg-violet-50/50 hover:bg-violet-100/60"
              >
                <td className="px-4 py-3">
                  <Link
                    href={`/review/${doc.id}`}
                    className="font-medium text-blue-700 hover:underline"
                  >
                    {doc.po_number ?? doc.original_filename}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-700">{doc.buyer_name ?? "—"}</td>
                <td className="numeric px-4 py-3 text-right text-slate-900">
                  {doc.order_total ?? "—"} {doc.currency ?? ""}
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {doc.created_at ? new Date(doc.created_at).toLocaleDateString() : "—"}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={doc.status} />
                </td>
                <td className="px-4 py-3">
                  {/*
                    Confidence describes how sure DocFlow was when it read
                    the document. Once a person has checked and approved it,
                    that number is history -- leaving "Low confidence" on an
                    approved order made a walkthrough tester think something
                    was still wrong with it.
                  */}
                  {doc.status === "approved" || doc.status === "exported" ? (
                    <span className="text-xs text-green-800">Checked by a person</span>
                  ) : (
                    <ConfidenceBadge value={doc.overall_confidence} />
                  )}
                </td>
                <td className="px-4 py-3">
                  {doc.open_warnings > 0 ? (
                    <span className={`${PILL} bg-amber-100 text-amber-800`}>
                      {doc.open_warnings} to look at
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">clear</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs">
                  <Flags doc={doc} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
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
        <span key={flag} className={`${PILL} bg-slate-100 text-slate-600`}>
          {flag}
        </span>
      ))}
    </span>
  );
}
