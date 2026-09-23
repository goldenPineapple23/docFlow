"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  clearQuarantine,
  getQuarantine,
  getTenantOverview,
  putSenderSettings,
  releaseQuarantine,
  rotateIntakeAddress,
  type QuarantineView,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * The Console's held-documents screen for one tenant (CLAUDE.md Section
 * 7.16.3, 7.16.4; D-126): what is held and why, with the sender, subject,
 * authentication results, file type and hash the founder needs to decide;
 * bulk release, and bulk clear with type-to-confirm; the opt-in approved-sender
 * list; and replacing the intake address.
 *
 * The founder may release any hold -- including one whose ceiling is still
 * tripped -- and the screen says so. Every action is one audited request.
 */
export default function TenantQuarantinePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [view, setView] = useState<QuarantineView | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<CatalogError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Bumping this refetches; the fetch lives in the effect, with setState only
  // in its promise callbacks (react-hooks/set-state-in-effect).
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getTenantOverview(id), getQuarantine(id)])
      .then(([t, q]) => {
        if (cancelled) return;
        setTenantName(t.tenant.name);
        setView(q);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [id, refresh]);

  async function act(work: () => Promise<string>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await work());
      setSelected(new Set());
      setRefresh((n) => n + 1);
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  function toggle(docId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }

  const ids = [...selected];

  return (
    <>
      <Link href={`/admin/tenants/${id}`} className="text-sm text-blue-700 underline">
        ← Tenant
      </Link>
      <h1 className="mt-1 text-xl font-semibold">
        Held documents{tenantName ? ` — ${tenantName}` : ""}
      </h1>

      {notice ? <p className="mt-4 text-sm text-green-800">{notice}</p> : null}
      {error ? (
        <div className="mt-4">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}
      {view === null && !error ? <p className="mt-4 text-sm text-gray-500">Loading…</p> : null}

      {view ? (
        <>
          <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <dt className="text-gray-500">This month</dt>
              <dd data-testid="usage">
                {view.usage.used}
                {view.usage.allowance !== null ? ` / ${view.usage.allowance}` : ""} documents
                {view.usage.tier ? ` (${view.usage.tier})` : " (no tier set)"}
              </dd>
              <dt className="text-gray-500">Held now</dt>
              <dd>{view.documents.length}</dd>
            </dl>
            {view.expired_held > 0 ? (
              <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-amber-950">
                {view.expired_held} held {view.expired_held === 1 ? "document is" : "documents are"} past
                the retention period. Release or clear {view.expired_held === 1 ? "it" : "them"} — nothing is
                deleted automatically.
              </p>
            ) : null}
          </section>

          <section className="mt-5">
            <h2 className="text-sm font-semibold">Held documents</h2>
            <p className="mt-1 max-w-3xl text-sm text-gray-600">
              Stored, never sent to the model, and not counted against the allowance until released.
              Releasing sends them through the normal pipeline in the order they were received. You can
              release any hold, including one whose limit is still tripped; that is recorded under your
              name.
            </p>
            {view.groups.length > 0 ? (
              <ul className="mt-2 list-disc pl-5 text-sm text-gray-700">
                {view.groups.map((g) => (
                  <li key={g.reason}>
                    <strong>{g.count}</strong> {g.reason}: {g.message}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-600">
                Nothing is held for this tenant.
              </p>
            )}

            {view.documents.length > 0 ? (
              <>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={busy || ids.length === 0}
                    data-testid="release"
                    onClick={() =>
                      void act(async () => {
                        const r = await releaseQuarantine(id, ids);
                        return `Released ${r.released.length}. They are being processed now, oldest first.`;
                      })
                    }
                    className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                  >
                    Release selected ({ids.length})
                  </button>
                  <button
                    type="button"
                    className="text-sm text-blue-700 underline"
                    onClick={() => setSelected(new Set(view.documents.map((d) => d.id)))}
                  >
                    Select all
                  </button>
                  <button type="button" className="text-sm text-blue-700 underline" onClick={() => setSelected(new Set())}>
                    Select none
                  </button>
                </div>
                <div className="mt-2 overflow-x-auto">
                  <table className="min-w-[56rem] w-full text-left text-sm">
                    <thead className="text-xs text-gray-500">
                      <tr>
                        <th className="w-8 py-1" />
                        <th className="py-1">Received</th>
                        <th className="py-1">Reason</th>
                        <th className="py-1">Sender / subject</th>
                        <th className="py-1">File</th>
                        <th className="py-1">SPF / DKIM / DMARC</th>
                        <th className="py-1">SHA-256</th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.documents.map((d) => (
                        <tr key={d.id} className="border-t border-gray-100 align-top">
                          <td className="py-1.5">
                            <input
                              type="checkbox"
                              aria-label={`Select ${d.original_filename}`}
                              checked={selected.has(d.id)}
                              onChange={() => toggle(d.id)}
                            />
                          </td>
                          <td className="py-1.5">{new Date(d.created_at).toLocaleString()}</td>
                          <td className="py-1.5">{d.quarantine_reason}</td>
                          <td className="py-1.5">
                            {d.sender_email ?? "—"}
                            {d.subject ? <span className="block text-gray-500">{d.subject}</span> : null}
                          </td>
                          <td className="py-1.5">
                            {d.original_filename}
                            {d.file_type ? <span className="block text-gray-500">.{d.file_type}</span> : null}
                          </td>
                          <td className="py-1.5 text-gray-700">
                            {d.spf_result ?? "—"} / {d.dkim_result ?? "—"} / {d.dmarc_result ?? "—"}
                          </td>
                          <td className="py-1.5 font-mono text-xs text-gray-500">{d.content_sha256.slice(0, 12)}…</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <ClearBox
                  tenantName={tenantName}
                  count={ids.length}
                  busy={busy}
                  onClear={(name) =>
                    void act(async () => {
                      const r = await clearQuarantine(id, ids, name);
                      return `Cleared ${r.cleared.length}. They are hidden, not destroyed, until the retention period ends.`;
                    })
                  }
                />
              </>
            ) : null}
          </section>

          <SenderSettings
            key={JSON.stringify(view.sender_settings)}
            settings={view.sender_settings}
            busy={busy}
            onSave={(strict, list) =>
              void act(async () => {
                await putSenderSettings(id, strict, list);
                return strict ? "Approved-sender list is on." : "Approved-sender list is off.";
              })
            }
          />

          <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5 text-sm">
            <h2 className="text-sm font-semibold">Replace the intake address</h2>
            <p className="mt-1 max-w-3xl text-gray-600">
              Issues a new address and emails the owner. The old address keeps replying &ldquo;this
              address has changed&rdquo; to anyone who uses it for 30 days, then stops working. Use this if
              the address has been shared too widely or is being abused.
            </p>
            <button
              type="button"
              disabled={busy}
              data-testid="rotate"
              onClick={() => {
                if (!window.confirm("Issue a new intake address for this tenant?")) return;
                void act(async () => {
                  const r = await rotateIntakeAddress(id);
                  return `New address issued: ${r.address}. The old one replies until ${new Date(r.grace_ends_at).toLocaleDateString()}.`;
                });
              }}
              className="mt-3 rounded border border-gray-300 px-4 py-2 font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-50"
            >
              Replace address…
            </button>
          </section>
        </>
      ) : null}
    </>
  );
}

function ClearBox({
  tenantName,
  count,
  busy,
  onClear,
}: {
  tenantName: string | null;
  count: number;
  busy: boolean;
  onClear: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  if (!open) {
    return (
      <button
        type="button"
        disabled={count === 0}
        onClick={() => setOpen(true)}
        className="mt-3 rounded border border-red-700 px-3 py-1.5 text-sm font-medium text-red-800 hover:bg-red-50 disabled:opacity-40"
      >
        Clear selected ({count})…
      </button>
    );
  }
  return (
    <div className="mt-3 space-y-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
      <p className="text-red-900">
        Clearing hides these {count} from the tenant and from this list. It doesn&apos;t destroy them
        until the retention period ends. Type the tenant&apos;s name exactly to confirm.
      </p>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={tenantName ?? ""}
        data-testid="clear-confirm-name"
        className="w-full rounded border border-gray-300 px-2 py-1.5"
      />
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || name !== tenantName || count === 0}
          data-testid="clear-confirm"
          onClick={() => {
            onClear(name);
            setOpen(false);
            setName("");
          }}
          className="rounded bg-red-800 px-4 py-2 font-medium text-white disabled:opacity-40"
        >
          Clear
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            setName("");
          }}
          className="rounded border border-gray-300 px-4 py-2"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function SenderSettings({
  settings,
  busy,
  onSave,
}: {
  settings: { strict_sender_mode: boolean; sender_allowlist: string[] };
  busy: boolean;
  onSave: (strict: boolean, list: string[]) => void;
}) {
  const [strict, setStrict] = useState(settings.strict_sender_mode);
  const [text, setText] = useState(settings.sender_allowlist.join("\n"));
  return (
    <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5 text-sm">
      <h2 className="text-sm font-semibold">Approved senders (optional)</h2>
      <p className="mt-1 max-w-3xl text-gray-600">
        Off by default, because a new buyer&apos;s first order from an address nobody has seen is
        exactly what DocFlow is for. When on, mail from anyone not listed is <strong>held</strong> for
        the tenant to release — never rejected — so a real order from a new buyer waits until someone
        confirms it.
      </p>
      <label className="mt-3 flex items-center gap-2">
        <input type="checkbox" checked={strict} onChange={(e) => setStrict(e.target.checked)} data-testid="strict-toggle" />
        Only accept mail from the senders below
      </label>
      <label className="mt-3 block">
        Addresses or domains, one per line
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          data-testid="allowlist"
          placeholder={"buyer@example.com\nexample.org"}
          className="mt-1 block w-full max-w-md rounded border border-gray-300 px-2 py-1.5"
        />
      </label>
      <button
        type="button"
        disabled={busy}
        data-testid="save-senders"
        onClick={() => onSave(strict, text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean))}
        className="mt-3 rounded bg-slate-900 px-4 py-2 font-medium text-white disabled:opacity-50"
      >
        Save
      </button>
    </section>
  );
}
