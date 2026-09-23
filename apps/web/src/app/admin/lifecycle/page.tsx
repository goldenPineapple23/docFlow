"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  deleteTenant,
  getReadyToDeleteQueue,
  getWindDownQueue,
  type ReadyToDeleteTenant,
  type WindDownTenant,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Section 7.15.4: "Wind-down queue and ready-to-delete queue. Two filtered
 * views of the tenant list ... Nothing here runs on its own." Every tenant
 * shown is already in `pending_deletion` (Section 7.14 / D-123: suspended
 * and pending_deletion happen in the same tick, so a tenant is never
 * observably suspended-but-not-counting-down).
 */
export default function LifecyclePage() {
  const [windDown, setWindDown] = useState<WindDownTenant[] | null>(null);
  const [ready, setReady] = useState<ReadyToDeleteTenant[] | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);
  // Bumping this refetches. The fetch lives in the effect (setState only
  // inside its promise callbacks), which is what react-hooks/set-state-in-effect
  // wants, and a delete just bumps the counter.
  const [refresh, setRefresh] = useState(0);
  const reload = useCallback(() => setRefresh((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getWindDownQueue(), getReadyToDeleteQueue()])
      .then(([w, r]) => {
        if (cancelled) return;
        setWindDown(w.tenants);
        setReady(r.tenants);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  return (
    <>
      <h1 className="text-xl font-semibold">Lifecycle</h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        Tenants winding down. Reactivating, and reviewing a cancellation, happens on each tenant&apos;s
        own Lifecycle tab — this page is only the two queues Section 7.15.4 asks for.
      </p>

      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}

      <section className="mt-6">
        <h2 className="text-sm font-semibold">Ready to delete</h2>
        <p className="mt-1 text-sm text-gray-600">
          Past their deletion window. Deleting is irreversible and requires typing the tenant&apos;s
          name.
        </p>
        {ready === null && !error ? <p className="mt-3 text-sm text-gray-500">Loading…</p> : null}
        {ready && ready.length === 0 ? (
          <p className="mt-3 rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-600">
            Nothing is ready to delete right now.
          </p>
        ) : null}
        {ready && ready.length > 0 ? (
          <ul className="mt-3 space-y-3">
            {ready.map((t) => (
              <ReadyRow key={t.id} tenant={t} onDeleted={reload} />
            ))}
          </ul>
        ) : null}
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-semibold">Wind-down queue</h2>
        <p className="mt-1 text-sm text-gray-600">
          Suspended tenants whose data is scheduled for deletion. Full read and export access stays
          on for them until the date below.
        </p>
        {windDown === null && !error ? <p className="mt-3 text-sm text-gray-500">Loading…</p> : null}
        {windDown && windDown.length === 0 ? (
          <p className="mt-3 rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-600">
            No tenant is winding down right now.
          </p>
        ) : null}
        {windDown && windDown.length > 0 ? (
          <ul className="mt-3 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm">
            {windDown.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-4 px-4 py-3">
                <div>
                  <Link href={`/admin/tenants/${t.id}/lifecycle`} className="font-medium text-blue-700 underline">
                    {t.name}
                  </Link>
                  <p className="text-xs text-gray-500">
                    {t.cancellation_reason ?? "—"} · deletes {new Date(t.deletion_scheduled_at).toLocaleDateString()}
                  </p>
                </div>
                <span className="shrink-0 text-xs text-gray-600">
                  {t.days_remaining} {t.days_remaining === 1 ? "day" : "days"} left
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </>
  );
}

function ReadyRow({ tenant, onDeleted }: { tenant: ReadyToDeleteTenant; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);

  async function confirmDelete() {
    setBusy(true);
    setError(null);
    try {
      await deleteTenant(tenant.id, { confirm_name: name, reason });
      onDeleted();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-xl border border-red-200 bg-red-50 p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="font-medium">{tenant.name}</p>
          <p className="text-xs text-gray-600">
            {tenant.cancellation_reason ?? "—"} · window elapsed{" "}
            {new Date(tenant.deletion_scheduled_at).toLocaleDateString()}
          </p>
        </div>
        {!confirming ? (
          <button
            type="button"
            data-testid="delete-start"
            onClick={() => setConfirming(true)}
            className="shrink-0 rounded border border-red-700 px-3 py-1.5 text-sm font-medium text-red-800 hover:bg-red-100"
          >
            Delete…
          </button>
        ) : null}
      </div>

      {confirming ? (
        <div className="mt-3 space-y-2 border-t border-red-200 pt-3">
          <p className="text-sm text-red-900">
            This permanently deletes {tenant.name}&apos;s business data and stored files. It cannot be
            undone. Type the tenant&apos;s name exactly to confirm.
          </p>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tenant.name}
            data-testid="delete-confirm-name"
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
          />
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why (at least 10 characters)"
            data-testid="delete-reason"
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
            rows={2}
          />
          {error ? <CatalogErrorBox error={error} /> : null}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || name !== tenant.name || reason.trim().length < 10}
              data-testid="delete-confirm"
              onClick={() => void confirmDelete()}
              className="rounded bg-red-800 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              {busy ? "Deleting…" : "Permanently delete"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                setName("");
                setReason("");
                setError(null);
              }}
              className="rounded border border-gray-300 px-4 py-2 text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </li>
  );
}
