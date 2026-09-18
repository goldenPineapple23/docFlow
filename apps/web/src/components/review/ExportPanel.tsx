"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ReviewApiError,
  createExport,
  getExport,
  listExports,
  type CatalogError,
  type ExportFormat,
  type ExportRecord,
} from "@/lib/review";

/**
 * Export an approved order as a file (CLAUDE.md Section 7.4, Phase 4).
 *
 * The file is built by the worker from the approved snapshot and checked
 * against it before it can be downloaded, so a click asks for the file, then
 * polls until it is ready or has a catalog-coded reason it could not be
 * made. Every download fetches a fresh short-lived link rather than reusing
 * one, so a link is never older than the click that uses it.
 *
 * There is no ERP connection anywhere here, by design (Section 3): the
 * customer downloads the file and imports it themselves.
 */

const FORMATS: Array<{ format: ExportFormat; label: string; hint: string }> = [
  { format: "csv", label: "CSV", hint: "One row per line item, order details on every row." },
  { format: "xlsx", label: "Excel", hint: "The same layout as the CSV, as a spreadsheet." },
  { format: "json", label: "JSON", hint: "For importing into your own software." },
  {
    format: "iif",
    label: "QuickBooks (IIF)",
    hint: "An estimate for QuickBooks Desktop. It posts nothing until you convert it.",
  },
];

const POLL_INTERVAL_MS = 600;
const POLL_LIMIT = 50; // ~30 seconds; an order file normally takes under one.

type Notice =
  | { kind: "error"; catalog: CatalogError }
  | { kind: "info"; text: string }
  | null;

function triggerDownload(url: string) {
  // The API answers with Content-Disposition: attachment, so following the
  // link saves the file and leaves this page where it is.
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatBytes(size: number | null): string {
  if (size === null) return "";
  return size < 1024 ? `${size} bytes` : `${(size / 1024).toFixed(1)} KB`;
}

export function ExportPanel({
  documentId,
  exportable,
  onExported,
}: {
  documentId: string;
  exportable: boolean;
  // The first file moves the order to "exported"; the page re-reads it.
  onExported?: () => void;
}) {
  const [history, setHistory] = useState<ExportRecord[] | null>(null);
  const [working, setWorking] = useState<ExportFormat | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  const refresh = useCallback(async () => {
    try {
      setHistory((await listExports(documentId)).exports);
    } catch {
      // The history is a convenience; a failure to load it never blocks an
      // export, and the export itself reports its own errors.
    }
  }, [documentId]);

  useEffect(() => {
    let cancelled = false;
    listExports(documentId)
      .then(({ exports }) => {
        if (!cancelled) setHistory(exports);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [documentId, exportable]);

  const download = useCallback(async (exportId: string) => {
    const result = await getExport(exportId);
    if (result.download) triggerDownload(result.download.url);
  }, []);

  const run = useCallback(
    async (format: ExportFormat) => {
      if (working) return;
      setWorking(format);
      setNotice(null);
      try {
        const { export: created } = await createExport(documentId, format);
        let current = created;
        for (let attempt = 0; attempt < POLL_LIMIT && current.status === "pending"; attempt++) {
          await sleep(POLL_INTERVAL_MS);
          const result = await getExport(created.id);
          current = result.export;
          if (current.status === "ready" && result.download) {
            triggerDownload(result.download.url);
          }
        }
        if (current.status === "ready") onExported?.();
        if (current.status === "failed" && current.error) {
          setNotice({ kind: "error", catalog: current.error });
        } else if (current.status === "pending") {
          setNotice({
            kind: "info",
            text: "Still being prepared. It will appear in the list below when it's ready.",
          });
        }
      } catch (e) {
        if (e instanceof ReviewApiError) setNotice({ kind: "error", catalog: e.catalog });
      } finally {
        setWorking(null);
        await refresh();
      }
    },
    [documentId, working, refresh, onExported],
  );

  if (!exportable && (history === null || history.length === 0)) return null;

  return (
    <section
      aria-labelledby="export-heading"
      data-testid="export-panel"
      className="space-y-3 rounded-xl border border-gray-200 p-5"
    >
      <h2
        id="export-heading"
        className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500"
      >
        Export
      </h2>

      {exportable ? (
        <>
          <p className="text-sm text-gray-600">
            Download this approved order to import into your own system.
          </p>
          <div className="flex flex-wrap gap-2">
            {FORMATS.map(({ format, label, hint }) => (
              <button
                key={format}
                type="button"
                title={hint}
                onClick={() => void run(format)}
                disabled={working !== null}
                data-testid={`export-${format}`}
                className="rounded-full bg-slate-800 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {working === format ? "Preparing…" : label}
              </button>
            ))}
          </div>
        </>
      ) : (
        <p className="text-sm text-gray-600">
          This order isn&apos;t approved right now, so it can&apos;t be exported. Files made from
          an earlier approval are listed below.
        </p>
      )}

      {notice?.kind === "error" ? (
        <div role="alert" data-testid="export-error" className="rounded border border-red-300 bg-red-50 p-3 text-sm">
          <p className="font-medium">{notice.catalog.title}</p>
          <p className="mt-1">{notice.catalog.message}</p>
          <p className="mt-1 text-gray-700">{notice.catalog.action}</p>
        </div>
      ) : null}
      {notice?.kind === "info" ? <p className="text-sm text-gray-600">{notice.text}</p> : null}

      {history && history.length > 0 ? (
        <ul data-testid="export-history" className="divide-y divide-gray-100 text-sm">
          {history.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-800">{row.format_label}</span>
                <span className="text-gray-500">
                  {row.requested_at ? new Date(row.requested_at).toLocaleString() : ""} ·{" "}
                  {row.by_docflow_support ? "DocFlow support" : row.generated_by}
                </span>
                {!row.is_current_snapshot ? (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                    Earlier approval
                  </span>
                ) : null}
              </span>
              {row.status === "ready" ? (
                <button
                  type="button"
                  onClick={() => void download(row.id)}
                  className="text-blue-700 hover:underline"
                >
                  Download <span className="text-gray-400">({formatBytes(row.byte_size)})</span>
                </button>
              ) : row.status === "failed" ? (
                <span className="text-red-700">{row.error?.title ?? "Couldn't be made"}</span>
              ) : (
                <span className="text-gray-500">Preparing…</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
