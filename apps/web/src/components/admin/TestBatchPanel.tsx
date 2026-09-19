"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  completeTestBatch,
  getTestBatch,
  runTestBatch,
  uploadTestBatch,
  type TestBatchDocument,
  type TestBatchUploadResult,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { StatusBadge } from "@/components/StatusBadge";

/**
 * Onboarding Steps 6-8 (CLAUDE.md Section 7.15.2) on the tenant page:
 *
 *   6. Upload the prospect's sample orders. Same upload path and file checks
 *      as a customer's own upload; each file waits, unread, as "staged".
 *   7. Run extraction: the normal pipeline, at interactive priority. Status
 *      and cost appear here as each one finishes.
 *   8. Review each one in the normal review screen, as DocFlow support, then
 *      mark the batch complete -- which unlocks go-live.
 */

const IN_FLIGHT = new Set(["pending", "processing"]);
const DONE = new Set(["approved", "exported"]);
const OPEN_STATUSES = new Set(["catalog_loaded", "test_batch_uploaded", "test_batch_running"]);

export function TestBatchPanel({
  tenantId,
  onboardingStatus,
  onChanged,
}: {
  tenantId: string;
  onboardingStatus: string;
  onChanged: () => Promise<void>;
}) {
  const [documents, setDocuments] = useState<TestBatchDocument[] | null>(null);
  const [results, setResults] = useState<TestBatchUploadResult[]>([]);
  const [error, setError] = useState<CatalogError | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  const load = useCallback(async () => {
    const { documents } = await getTestBatch(tenantId);
    setDocuments(documents);
  }, [tenantId]);

  useEffect(() => {
    let cancelled = false;
    getTestBatch(tenantId)
      .then(({ documents }) => {
        if (!cancelled) setDocuments(documents);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  // While anything is being read, check back every few seconds.
  const inFlight = documents?.some((d) => IN_FLIGHT.has(d.status)) ?? false;
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => void load().catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [inFlight, load]);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      await load();
      await onChanged();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  function upload(files: File[]) {
    if (files.length === 0) return;
    void act(async () => {
      const { results } = await uploadTestBatch(tenantId, files);
      setResults(results);
    });
  }

  const catalogMissing = onboardingStatus === "tenant_created";
  const open = OPEN_STATUSES.has(onboardingStatus);
  const complete = onboardingStatus === "test_batch_complete" || onboardingStatus === "live";
  const staged = documents?.filter((d) => d.status === "staged").length ?? 0;
  const total = documents?.length ?? 0;
  const approved = documents?.filter((d) => DONE.has(d.status)).length ?? 0;
  // Summed in ten-thousandths of a dollar (the column's precision), never as
  // floating-point dollars (Section 7: money is never a float).
  const costTenThousandths = (documents ?? []).reduce(
    (sum, d) => sum + Math.round(Number(d.est_cost_usd ?? 0) * 10000),
    0,
  );
  const cost = `${Math.floor(costTenThousandths / 10000)}.${String(costTenThousandths % 10000).padStart(4, "0")}`;
  const canComplete = onboardingStatus === "test_batch_running" && total > 0 && approved === total;

  return (
    <section data-testid="test-batch" className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
      <h2 className="text-sm font-semibold">Test batch</h2>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        Upload the prospect&apos;s 5–10 sample orders, run them through the normal pipeline, and
        review each one as DocFlow support. They stay in the customer&apos;s history, labelled as
        the setup batch, and never count toward their allowance or the dashboard.
      </p>

      {error ? (
        <div className="mt-3">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}

      {catalogMissing ? (
        <p className="mt-3 text-sm text-amber-800">Load the catalog first (step 4) — the test batch is matched against it.</p>
      ) : null}

      {open && !catalogMissing ? (
        <label
          htmlFor="test-batch-picker"
          data-testid="test-batch-drop-zone"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            upload(Array.from(e.dataTransfer.files));
          }}
          className={[
            "mt-3 flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-5 text-center",
            dragging ? "border-slate-700 bg-slate-100" : "border-slate-300 bg-slate-50 hover:bg-slate-100",
          ].join(" ")}
        >
          <span className="rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white">
            {busy ? "Working…" : "Upload sample orders…"}
          </span>
          <span className="text-sm text-gray-600">or drag them here — several at once is fine</span>
          <input
            id="test-batch-picker"
            type="file"
            multiple
            disabled={busy}
            data-testid="test-batch-input"
            className="sr-only"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              upload(files);
            }}
          />
        </label>
      ) : null}

      {results.some((r) => r.error) ? (
        <ul data-testid="test-batch-rejections" className="mt-3 space-y-2">
          {results
            .filter((r) => r.error)
            .map((r, i) => (
              <li key={`${r.filename}-${i}`} className="text-sm">
                <p className="font-medium">{r.filename}</p>
                <CatalogErrorBox error={r.error!} />
              </li>
            ))}
        </ul>
      ) : null}

      {documents && documents.length > 0 ? (
        <div className="mt-4 overflow-x-auto">
          <table data-testid="test-batch-documents" className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-2 py-1.5">File</th>
                <th className="px-2 py-1.5">Status</th>
                <th className="px-2 py-1.5">PO / buyer</th>
                <th className="px-2 py-1.5 text-right">Confidence</th>
                <th className="px-2 py-1.5 text-right">AI cost</th>
                <th className="px-2 py-1.5"></th>
              </tr>
            </thead>
            <tbody>
              {documents.map((d) => (
                <tr key={d.id} className="border-t border-gray-100">
                  <td className="px-2 py-1.5">{d.original_filename}</td>
                  <td className="px-2 py-1.5">
                    <StatusBadge status={d.status} />
                  </td>
                  <td className="px-2 py-1.5 text-gray-700">
                    {d.po_number ?? "—"}
                    {d.buyer_name ? <span className="text-gray-500"> · {d.buyer_name}</span> : null}
                  </td>
                  <td className="numeric px-2 py-1.5 text-right">
                    {d.overall_confidence !== null ? `${Math.round(Number(d.overall_confidence) * 100)}%` : "—"}
                  </td>
                  <td className="numeric px-2 py-1.5 text-right">
                    {d.est_cost_usd !== null ? `$${d.est_cost_usd}` : "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {d.status !== "staged" && !IN_FLIGHT.has(d.status) ? (
                      <Link
                        href={`/admin/tenants/${tenantId}/review/${d.id}`}
                        className="font-medium text-blue-700 hover:underline"
                      >
                        {DONE.has(d.status) ? "Open" : "Review →"}
                      </Link>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-gray-600">
            {approved} of {total} approved · AI cost so far ${cost}
            {inFlight ? " · reading… this updates by itself" : ""}
          </p>
        </div>
      ) : documents ? (
        <p className="mt-3 text-sm text-gray-500">No sample orders uploaded yet.</p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        {open ? (
          <button
            type="button"
            data-testid="test-batch-run"
            disabled={busy || staged === 0}
            onClick={() => void act(() => runTestBatch(tenantId))}
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-300"
          >
            Run extraction{staged > 0 ? ` (${staged} waiting)` : ""}
          </button>
        ) : null}
        {!complete ? (
          <button
            type="button"
            data-testid="test-batch-complete"
            disabled={busy || !canComplete}
            onClick={() => void act(() => completeTestBatch(tenantId))}
            className="rounded border border-gray-300 px-4 py-2 text-sm font-medium disabled:text-gray-400"
          >
            Mark test batch complete
          </button>
        ) : (
          <p data-testid="test-batch-done" className="text-sm text-green-800">
            Test batch complete.
          </p>
        )}
        {!complete && total > 0 && !canComplete && !inFlight && staged === 0 ? (
          <span className="text-sm text-gray-600">Approve every order in the batch to finish it.</span>
        ) : null}
      </div>
    </section>
  );
}
