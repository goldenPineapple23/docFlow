"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  getFieldSchema,
  getTenantOverview,
  saveFieldSchema,
  type FieldSchemaVersion,
  type FieldSetting,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Per-tenant field settings (Section 7.13; D-120). Which fields this customer
 * is checked on, which are simply shown, and which they never see.
 *
 * Hiding a field changes what DocFlow shows and checks, never what the model
 * is asked for -- the value is still extracted and stored, so un-hiding it
 * later shows the data that was there all along.
 *
 * Every save is a new version. The founder configures this; customers don't.
 */

const STATES = [
  { value: "required", label: "Required", hint: "Checked for, and counts towards confidence" },
  { value: "optional", label: "Optional", hint: "Shown, but absent is fine" },
  { value: "hidden", label: "Hidden", hint: "Never shown to this customer" },
] as const;

export default function FieldsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldSetting[] | null>(null);
  const [version, setVersion] = useState(0);
  const [states, setStates] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [recheck, setRecheck] = useState(true);
  const [history, setHistory] = useState<FieldSchemaVersion[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getTenantOverview(id), getFieldSchema(id)])
      .then(([t, s]) => {
        if (cancelled) return;
        setTenantName(t.tenant.name);
        setFields(s.schema.fields);
        setVersion(s.schema.version);
        setStates(Object.fromEntries(s.schema.fields.map((f) => [`${f.level}.${f.name}`, f.state])));
        setHistory(s.history);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const dirty = useMemo(
    () => (fields ?? []).some((f) => states[`${f.level}.${f.name}`] !== f.state),
    [fields, states],
  );

  async function save() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const body = { header: {} as Record<string, string>, line: {} as Record<string, string> };
      for (const f of fields ?? []) body[f.level][f.name] = states[`${f.level}.${f.name}`] ?? f.state;
      const result = await saveFieldSchema(id, {
        fields: body,
        note: note.trim() || null,
        apply_to_open_documents: recheck,
      });
      setFields(result.schema.fields);
      setVersion(result.schema.version);
      setNote("");
      setNotice(
        `Saved as version ${result.schema.version}.` +
          (result.rechecked_documents
            ? ` ${result.rechecked_documents} order${result.rechecked_documents === 1 ? "" : "s"} still awaiting review ${
                result.rechecked_documents === 1 ? "was" : "were"
              } checked again.`
            : ""),
      );
      const fresh = await getFieldSchema(id);
      setHistory(fresh.history);
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Link href={`/admin/tenants/${id}`} className="text-sm text-blue-700 underline">
        ← Tenant
      </Link>
      <h1 className="mt-1 text-xl font-semibold">Field settings{tenantName ? ` — ${tenantName}` : ""}</h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        What this customer is checked on. <strong>Required</strong> fields are checked for on every
        order and set the order&apos;s confidence score. <strong>Optional</strong> fields are read
        and shown, but an order without one raises nothing. <strong>Hidden</strong> fields never
        reach this customer&apos;s review screen — DocFlow still reads and stores them, so
        un-hiding one later shows the data that was there.
      </p>
      <p className="mt-1 text-sm text-gray-500">
        Currently version {version || "—"} {version ? "" : "(DocFlow's defaults)"}.
      </p>

      {error ? (
        <div className="mt-4">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}
      {notice ? (
        <p data-testid="fields-notice" className="mt-4 text-sm text-green-800">
          {notice}
        </p>
      ) : null}

      {fields === null ? (
        !error ? <p className="mt-5 text-sm text-gray-500">Loading…</p> : null
      ) : (
        <>
          {(["header", "line"] as const).map((level) => (
            <section key={level} className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
              <h2 className="text-sm font-semibold">
                {level === "header" ? "Order details" : "Line items"}
              </h2>
              <table className="mt-2 w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="py-1">Field</th>
                    {STATES.map((s) => (
                      <th key={s.value} className="py-1 pl-3" title={s.hint}>
                        {s.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {fields
                    .filter((f) => f.level === level)
                    .map((f) => {
                      const key = `${f.level}.${f.name}`;
                      return (
                        <tr key={key} data-testid={`field-row-${f.name}`}>
                          <td className="py-2">
                            {f.label}
                            {f.locked ? (
                              <span className="ml-2 text-xs text-gray-500">always required</span>
                            ) : null}
                          </td>
                          {STATES.map((s) => (
                            <td key={s.value} className="py-2 pl-3">
                              <input
                                type="radio"
                                name={key}
                                checked={(states[key] ?? f.state) === s.value}
                                disabled={f.locked}
                                aria-label={`${f.label}: ${s.label}`}
                                data-testid={`field-${f.name}-${s.value}`}
                                onChange={() => setStates((prev) => ({ ...prev, [key]: s.value }))}
                              />
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </section>
          ))}

          <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
            <label className="block text-sm">
              <span className="font-medium">Why (optional)</span>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={500}
                data-testid="fields-note"
                placeholder="e.g. they never put payment terms on a PO"
                className="mt-1 w-full rounded border px-3 py-2"
              />
            </label>
            <label className="mt-3 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={recheck}
                data-testid="fields-recheck"
                onChange={(e) => setRecheck(e.target.checked)}
                className="mt-1"
              />
              <span>
                Check orders still awaiting review against the new settings. Approved orders are
                never touched — they keep what they were approved with.
              </span>
            </label>
            <button
              type="button"
              disabled={busy || !dirty}
              data-testid="fields-save"
              onClick={() => void save()}
              className="mt-4 rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {busy ? "Saving…" : dirty ? "Save new version" : "No changes"}
            </button>
          </section>

          {history.length > 0 ? (
            <section className="mt-6">
              <h2 className="text-sm font-semibold">Versions</h2>
              <ul data-testid="fields-history" className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm">
                {history.map((h) => (
                  <li key={h.version} className="px-4 py-2">
                    v{h.version} · {new Date(h.created_at).toLocaleString()} ·{" "}
                    <span className="text-gray-500">
                      {h.acting_as_tenant_id ? "DocFlow support" : (h.created_by_email ?? "—")}
                      {h.note ? ` · ${h.note}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      )}
    </>
  );
}
