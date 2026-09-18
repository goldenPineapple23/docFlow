"use client";

import { useCallback, useEffect, useState } from "react";
import {
  commitImport,
  discardImport,
  fixImportRow,
  getImport,
  importFromIntake,
  listImports,
  listTenantIntakeFiles,
  setImportMapping,
  uploadImport,
  type ImportFinding,
  type ImportKind,
  type ImportPreview,
  type ImportSummaryRow,
  type TenantIntakeFile,
} from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * The one import screen, for catalogs and customer lists alike (CLAUDE.md
 * Section 7.15.2 Steps 4-5: "Same component and flow"; D-108).
 *
 *   1. Start: upload a file, or use one from the tenant's intake.
 *   2. Columns: auto-detected, adjustable; remembered for next time.
 *   3. Check: the validation report, with the rows it is about. Rows that
 *      block the import can be fixed right here.
 *   4. Changes: what committing would add, update, reinstate and retire.
 *   5. Commit -- enabled only when nothing blocks.
 *
 * Every value shown came from a prospect's file and is rendered as text.
 */

const TABLE_TYPES = new Set(["csv", "xlsx", "xlsm", "xls", "txt"]);

const SEVERITY_STYLE: Record<ImportFinding["severity"], string> = {
  blocker: "border-red-300 bg-red-50",
  warning: "border-amber-300 bg-amber-50",
  info: "border-sky-200 bg-sky-50",
};
const SEVERITY_LABEL: Record<ImportFinding["severity"], string> = {
  blocker: "Must fix",
  warning: "Check",
  info: "FYI",
};

const COPY: Record<ImportKind, { title: string; intro: string; noun: string }> = {
  catalog: {
    title: "Catalog",
    intro:
      "Step 4 of onboarding. Every purchase-order line is matched against this list, so it has to be clean before anything else.",
    noun: "items",
  },
  buyers: {
    title: "Customer list",
    intro:
      "Step 5 of onboarding, and optional: customers are also created automatically from their first purchase order. Near-duplicate names are flagged for you to merge, never merged automatically.",
    noun: "customers",
  },
};

export function ImportWorkbench({ tenantId, kind }: { tenantId: string; kind: ImportKind }) {
  const [history, setHistory] = useState<ImportSummaryRow[]>([]);
  const [intakeFiles, setIntakeFiles] = useState<TenantIntakeFile[]>([]);
  const [current, setCurrent] = useState<ImportPreview | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  const refreshLists = useCallback(async () => {
    const [h, f] = await Promise.all([listImports(tenantId, kind), listTenantIntakeFiles(tenantId)]);
    setHistory(h.imports);
    setIntakeFiles(f.files.filter((file) => TABLE_TYPES.has(file.detected_type)));
  }, [tenantId, kind]);

  const load = useCallback(
    async (importId: string) => {
      const result = await getImport(tenantId, importId);
      setCurrent(result.import);
      return result.import;
    },
    [tenantId],
  );

  // First load: the lists, and the newest import that isn't finished yet.
  useEffect(() => {
    let cancelled = false;
    Promise.all([listImports(tenantId, kind), listTenantIntakeFiles(tenantId)])
      .then(async ([h, f]) => {
        if (cancelled) return;
        setHistory(h.imports);
        setIntakeFiles(f.files.filter((file) => TABLE_TYPES.has(file.detected_type)));
        const open = h.imports.find((row) => row.status === "parsing" || row.status === "parsed");
        if (open) {
          const result = await getImport(tenantId, open.id);
          if (!cancelled) setCurrent(result.import);
        }
      })
      .catch((e) => {
        if (!cancelled && e instanceof ReviewApiError) setError(e.catalog);
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId, kind]);

  // While the worker is reading the file, check back every second.
  useEffect(() => {
    if (current?.status !== "parsing") return;
    const timer = setInterval(() => {
      getImport(tenantId, current.id)
        .then(({ import: next }) => {
          if (next.status !== "parsing") {
            setCurrent(next);
            void refreshLists();
          }
        })
        .catch(() => {});
    }, 1000);
    return () => clearInterval(timer);
  }, [current, tenantId, refreshLists]);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      if (e instanceof ReviewApiError) setError(e.catalog);
    } finally {
      setBusy(false);
    }
  }

  async function start(file: File) {
    await act(async () => {
      const { import_id } = await uploadImport(tenantId, kind, file);
      await load(import_id);
      await refreshLists();
    });
  }

  const copy = COPY[kind];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">{copy.title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-600">{copy.intro}</p>
      </div>

      {error ? <CatalogErrorBox error={error} /> : null}

      {/* 1. Start */}
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-semibold">1. Choose the file</h2>
        {intakeFiles.length > 0 ? (
          <div className="mt-3">
            <p className="text-sm text-gray-600">From the prospect&apos;s intake:</p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {intakeFiles.map((file) => (
                <li key={file.id}>
                  <button
                    type="button"
                    disabled={busy}
                    data-testid={`import-intake-${file.id}`}
                    onClick={() =>
                      void act(async () => {
                        const { import_id } = await importFromIntake(tenantId, kind, file.id);
                        await load(import_id);
                        await refreshLists();
                      })
                    }
                    className="rounded-full border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
                  >
                    Use {file.original_filename}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <label
          htmlFor={`import-picker-${kind}`}
          data-testid="import-drop-zone"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files[0];
            if (file) void start(file);
          }}
          className={[
            "mt-3 flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-6 text-center",
            dragging ? "border-slate-700 bg-slate-100" : "border-slate-300 bg-slate-50 hover:bg-slate-100",
          ].join(" ")}
        >
          <span className="rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white">
            {busy ? "Working…" : "Upload a CSV or Excel file…"}
          </span>
          <span className="text-sm text-gray-600">or drag it here</span>
          <input
            id={`import-picker-${kind}`}
            type="file"
            accept=".csv,.xlsx,.xlsm,.xls,.txt"
            disabled={busy}
            data-testid="import-file-input"
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void start(file);
            }}
          />
        </label>
      </section>

      {current ? (
        <CurrentImport
          tenantId={tenantId}
          preview={current}
          busy={busy}
          noun={copy.noun}
          onChange={(work) =>
            void act(async () => {
              await work();
              await load(current.id);
            })
          }
          onFinished={(work) =>
            void act(async () => {
              await work();
              await load(current.id);
              await refreshLists();
            })
          }
        />
      ) : null}

      {history.length > 0 ? (
        <section className="rounded-xl border border-gray-200 bg-white p-5">
          <h2 className="text-sm font-semibold">Earlier imports</h2>
          <ul data-testid="import-history" className="mt-2 divide-y divide-gray-100 text-sm">
            {history.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-medium">{row.original_filename}</span>{" "}
                  <span className="text-gray-500">
                    · {new Date(row.created_at).toLocaleString()} · {row.status}
                    {row.row_count !== null ? ` · ${row.row_count} rows` : ""}
                  </span>
                </span>
                <button
                  type="button"
                  className="text-blue-700 hover:underline"
                  onClick={() => void act(() => load(row.id))}
                >
                  Open
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function CurrentImport({
  tenantId,
  preview,
  busy,
  noun,
  onChange,
  onFinished,
}: {
  tenantId: string;
  preview: ImportPreview;
  busy: boolean;
  noun: string;
  onChange: (work: () => Promise<unknown>) => void;
  onFinished: (work: () => Promise<unknown>) => void;
}) {
  if (preview.status === "parsing") {
    return (
      <section data-testid="import-reading" className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600">
        Reading {preview.original_filename}…
      </section>
    );
  }
  if (preview.status === "failed") {
    return (
      <section data-testid="import-failed" className="space-y-2">
        <p className="text-sm font-medium">{preview.original_filename}</p>
        {preview.error ? <CatalogErrorBox error={preview.error} /> : null}
      </section>
    );
  }
  if (preview.status === "committed" || preview.status === "discarded") {
    const summary = (preview.summary ?? {}) as Record<string, number>;
    return (
      <section data-testid="import-done" className="rounded-xl border border-green-200 bg-green-50 p-5 text-sm">
        <p className="font-medium">
          {preview.original_filename} — {preview.status === "committed" ? "committed" : "discarded"}
        </p>
        {preview.status === "committed" ? (
          <p className="mt-1">
            {summary.insert ?? 0} new, {summary.update ?? 0} updated, {summary.reinstate ?? 0} brought back,{" "}
            {summary.retire ?? 0} retired, {summary.unchanged ?? 0} unchanged {noun}.
          </p>
        ) : null}
      </section>
    );
  }

  const columns = preview.columns ?? [];
  const mapping = preview.mapping ?? {};
  const report = preview.report ?? [];
  const blockerFields = new Map<number, Set<string>>();
  for (const finding of report) {
    if (finding.severity !== "blocker" || !finding.field) continue;
    for (const row of finding.rows) {
      if (!blockerFields.has(row)) blockerFields.set(row, new Set());
      blockerFields.get(row)!.add(finding.field);
    }
  }
  const blockers = report.filter((f) => f.severity === "blocker").length;

  return (
    <>
      {/* 2. Columns */}
      <section className="rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-semibold">
          2. Match the columns{" "}
          <span className="font-normal text-gray-500">
            — {preview.original_filename}, {preview.row_count} rows
          </span>
        </h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {preview.fields.map((field) => (
            <label key={field.name} className="text-sm">
              <span className="font-medium">
                {field.label}
                {field.required ? <span className="text-red-700"> *</span> : null}
              </span>
              <select
                value={mapping[field.name] ?? ""}
                disabled={busy}
                data-testid={`map-${field.name}`}
                onChange={(e) => {
                  const next = { ...mapping, [field.name]: e.target.value === "" ? null : Number(e.target.value) };
                  // A column can feed one field only: taking it frees it elsewhere.
                  for (const other of Object.keys(next)) {
                    if (other !== field.name && next[other] === next[field.name]) next[other] = null;
                  }
                  onChange(() => setImportMapping(tenantId, preview.id, next));
                }}
                className="mt-1 w-full rounded border px-2 py-1.5"
              >
                <option value="">— not in this file —</option>
                {columns.map((column, index) => (
                  <option key={index} value={index}>
                    {column || `(column ${index + 1}, no heading)`}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        {preview.missing_required && preview.missing_required.length > 0 ? (
          <p data-testid="import-missing" className="mt-3 text-sm text-red-700">
            Choose a column for:{" "}
            {preview.missing_required
              .map((name) => preview.fields.find((f) => f.name === name)?.label ?? name)
              .join(", ")}
            .
          </p>
        ) : null}
      </section>

      {preview.diff ? (
        <>
          {/* 3. Check */}
          <section className="rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold">3. Check the file</h2>
            {report.length === 0 ? (
              <p data-testid="import-clean" className="mt-2 text-sm text-green-800">
                No problems found.
              </p>
            ) : (
              <ul data-testid="import-report" className="mt-3 space-y-2">
                {report.map((finding) => (
                  <li key={finding.code} className={`rounded-lg border p-3 text-sm ${SEVERITY_STYLE[finding.severity]}`}>
                    <p className="font-medium">
                      {SEVERITY_LABEL[finding.severity]} · {finding.title}{" "}
                      <span className="font-normal text-gray-600">({finding.count})</span>
                    </p>
                    <p className="mt-1 text-gray-700">{finding.message}</p>
                    <p className="mt-1 text-gray-700">{finding.action}</p>
                    {finding.rows.length > 0 ? (
                      <p className="mt-1 text-xs text-gray-600">
                        Rows: {finding.rows.join(", ")}
                        {finding.count > finding.rows.length ? " …" : ""}
                      </p>
                    ) : null}
                    {finding.keys.length > 0 ? (
                      <p className="mt-1 text-xs text-gray-600">{finding.keys.join(", ")}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-4 overflow-x-auto">
              <table data-testid="import-preview" className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="px-2 py-1.5">Row</th>
                    {preview.fields.map((field) => (
                      <th key={field.name} className="px-2 py-1.5">
                        {field.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(preview.preview ?? []).map((row) => {
                    const flagged = blockerFields.get(row.row_number);
                    return (
                      <tr key={row.row_number} className={flagged ? "bg-red-50" : "border-t border-gray-100"}>
                        <td className="px-2 py-1 text-gray-500">{row.row_number}</td>
                        {preview.fields.map((field) => (
                          <td key={field.name} className="px-2 py-1">
                            {flagged?.has(field.name) ? (
                              <input
                                defaultValue={row.values[field.name] ?? ""}
                                aria-label={`${field.label}, row ${row.row_number}`}
                                data-testid={`fix-${row.row_number}-${field.name}`}
                                onBlur={(e) => {
                                  if (e.target.value !== (row.values[field.name] ?? "")) {
                                    const value = e.target.value;
                                    onChange(() => fixImportRow(tenantId, preview.id, row.row_number, field.name, value));
                                  }
                                }}
                                className="w-full rounded border border-red-300 bg-white px-1.5 py-0.5"
                              />
                            ) : (
                              row.values[field.name] ?? <span className="text-gray-400">—</span>
                            )}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-1 text-xs text-gray-500">
                Showing the rows with problems first, then the start of the file. Fix a red cell and click
                away to re-check.
              </p>
            </div>
          </section>

          {/* 4. Changes and 5. Commit */}
          <section className="rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold">4. What committing will do</h2>
            <dl data-testid="import-diff" className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
              <Count label="New" value={preview.diff.insert} />
              <Count label="Updated" value={preview.diff.update} />
              <Count label="Brought back" value={preview.diff.reinstate} />
              <Count label="Unchanged" value={preview.diff.unchanged} />
              <Count label="Retired" value={preview.diff.retire} />
            </dl>
            {preview.retiring && preview.retiring.length > 0 ? (
              <p className="mt-2 text-xs text-gray-600">
                Not in this file, so retired (kept, never deleted): {preview.retiring.join(", ")}
              </p>
            ) : null}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || !preview.can_commit}
                data-testid="import-commit"
                onClick={() => onFinished(() => commitImport(tenantId, preview.id))}
                className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-300"
              >
                Commit
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => onFinished(() => discardImport(tenantId, preview.id))}
                className="rounded border border-gray-300 px-4 py-2 text-sm"
              >
                Discard this import
              </button>
              {!preview.can_commit ? (
                <span className="text-sm text-red-700">
                  {blockers} problem{blockers === 1 ? "" : "s"} to fix before committing.
                </span>
              ) : null}
            </div>
          </section>
        </>
      ) : null}
    </>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <dt className="text-xs uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold">{value}</dd>
    </div>
  );
}
