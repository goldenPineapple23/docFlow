"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  dismissBuyerMerge,
  getBuyerMerges,
  getTenantOverview,
  mergeBuyers,
  type MergeCandidate,
  type MergeHistoryRow,
  type MergeSide,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Possible duplicate customers (Section 7.6; D-119). DocFlow flags names that
 * look alike and never merges on its own; this is where the founder decides.
 * Merging moves the other customer's orders and rules to the one kept, and
 * remembers the other name so future orders under it go to the right place.
 */
export default function MergesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<MergeCandidate[] | null>(null);
  const [history, setHistory] = useState<MergeHistoryRow[]>([]);
  const [error, setError] = useState<CatalogError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const data = await getBuyerMerges(id);
    setCandidates(data.candidates);
    setHistory(data.history);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getTenantOverview(id), getBuyerMerges(id)])
      .then(([t, data]) => {
        if (cancelled) return;
        setTenantName(t.tenant.name);
        setCandidates(data.candidates);
        setHistory(data.history);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function act(run: () => Promise<string>) {
    setError(null);
    setNotice(null);
    try {
      setNotice(await run());
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    }
    try {
      await load();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    }
  }

  return (
    <>
      <Link href={`/admin/tenants/${id}`} className="text-sm text-blue-700 underline">
        ← Tenant
      </Link>
      <h1 className="mt-1 text-xl font-semibold">
        Possible duplicate customers{tenantName ? ` — ${tenantName}` : ""}
      </h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        DocFlow flags customers whose names look alike, and never merges them itself. Merging moves
        the other customer&apos;s orders and learned rules to the one you keep, and remembers the
        other name, so future orders under it go to the kept customer. Approved orders keep the
        name they were approved with.
      </p>

      {notice ? (
        <p data-testid="merge-notice" className="mt-4 text-sm text-green-800">
          {notice}
        </p>
      ) : null}
      {error ? (
        <div className="mt-4">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}

      {candidates === null ? (
        !error ? <p className="mt-5 text-sm text-gray-500">Loading…</p> : null
      ) : candidates.length === 0 ? (
        <p data-testid="merge-empty" className="mt-5 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600">
          Nothing waiting. New look-alike names appear here as orders arrive.
        </p>
      ) : (
        <ul className="mt-5 space-y-4">
          {candidates.map((c) => (
            <CandidateCard
              key={c.id}
              candidate={c}
              onMerge={(keep, gone) =>
                act(async () => {
                  const r = await mergeBuyers(id, c.id, keep.id);
                  return `Merged “${gone.name}” into “${keep.name}”: ${plural(r.documents_moved, "order")} and ${plural(
                    r.rules_moved,
                    "rule",
                  )} moved.`;
                })
              }
              onDismiss={() =>
                act(async () => {
                  await dismissBuyerMerge(id, c.id);
                  return "Kept as two separate customers.";
                })
              }
            />
          ))}
        </ul>
      )}

      {history.length > 0 ? (
        <section className="mt-8">
          <h2 className="text-sm font-semibold">Recent merges</h2>
          <ul data-testid="merge-history" className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm">
            {history.map((h) => (
              <li key={h.id} className="px-4 py-2">
                “{h.merged_name}” → “{h.kept_name}” · {plural(h.documents_moved, "order")},{" "}
                {plural(h.rules_moved, "rule")} moved ·{" "}
                <span className="text-gray-500">
                  {h.by_docflow_support ? "DocFlow support" : (h.merged_by_email ?? "—")},{" "}
                  {new Date(h.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function CandidateCard({
  candidate,
  onMerge,
  onDismiss,
}: {
  candidate: MergeCandidate;
  onMerge: (keep: MergeSide, gone: MergeSide) => Promise<void>;
  onDismiss: () => Promise<void>;
}) {
  // Default: keep the customer that existed first.
  const [keepId, setKeepId] = useState(candidate.buyers[0].id);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const keep = candidate.buyers.find((b) => b.id === keepId) ?? candidate.buyers[0];
  const gone = candidate.buyers.find((b) => b.id !== keep.id) ?? candidate.buyers[1];
  const score = Math.round(Number(candidate.similarity_score) * 100); // display only

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <li data-testid="merge-candidate" className="rounded-xl border border-gray-200 bg-white p-5">
      <p className="text-xs uppercase tracking-wide text-gray-500">Names {score}% alike</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        {candidate.buyers.map((b) => (
          <label
            key={b.id}
            className={`block cursor-pointer rounded-lg border p-3 text-sm ${
              b.id === keepId ? "border-slate-900 ring-1 ring-slate-900" : "border-gray-200"
            }`}
          >
            <span className="flex items-center gap-2">
              <input
                type="radio"
                name={`keep-${candidate.id}`}
                checked={b.id === keepId}
                data-testid="merge-keep"
                onChange={() => {
                  setKeepId(b.id);
                  setConfirming(false);
                }}
              />
              <span className="font-medium">{b.name}</span>
              {b.id === keepId ? <span className="text-xs text-gray-500">keep</span> : null}
            </span>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-gray-600">
              <dt>Email</dt>
              <dd className="break-all">{b.contact_email ?? "—"}</dd>
              <dt>Account</dt>
              <dd>{b.external_account_number ?? "—"}</dd>
              <dt>Orders</dt>
              <dd>{b.documents}</dd>
              <dt>Rules</dt>
              <dd>{b.rules}</dd>
              <dt>Since</dt>
              <dd>{new Date(b.created_at).toLocaleDateString()}</dd>
            </dl>
          </label>
        ))}
      </div>

      {confirming ? (
        <div data-testid="merge-confirm-box" className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
          Merge “{gone.name}” into “{keep.name}”? {plural(gone.documents, "order")} and {plural(gone.rules, "rule")} move
          to “{keep.name}”, which also takes any email or account number it doesn&apos;t have. “{gone.name}” is kept on
          record as a merged name.
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-3">
        {!confirming ? (
          <button
            type="button"
            disabled={busy}
            data-testid="merge-start"
            onClick={() => setConfirming(true)}
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Merge…
          </button>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              data-testid="merge-confirm"
              onClick={() => void run(() => onMerge(keep, gone))}
              className="rounded bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {busy ? "Merging…" : "Confirm merge"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
              className="rounded border border-gray-300 px-4 py-2 text-sm"
            >
              Cancel
            </button>
          </>
        )}
        <button
          type="button"
          disabled={busy}
          data-testid="merge-dismiss"
          onClick={() => void run(onDismiss)}
          className="rounded border border-gray-300 px-4 py-2 text-sm"
        >
          Not the same customer
        </button>
      </div>
    </li>
  );
}
