"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  cancelTenant,
  getLifecycle,
  getTenantOverview,
  previewCancel,
  reactivateTenant,
  type CancellationReason,
  type CancelPreview,
  type LifecycleStatus,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

const REASONS: { value: CancellationReason; label: string }[] = [
  { value: "customer_requested", label: "Customer requested" },
  { value: "non_payment", label: "Non-payment" },
  { value: "for_cause", label: "For cause" },
];

/**
 * Section 7.15.4's "Cancel tenant" form and the mirrored Reactivate action.
 * The effective date is computed from the reason and shown before
 * confirmation, along with the rule that produced it, exactly as the
 * section describes -- never typed in, only (optionally) pushed later.
 */
export default function TenantLifecyclePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [status, setStatus] = useState<LifecycleStatus | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [t, s] = await Promise.all([getTenantOverview(id), getLifecycle(id)]);
    setTenantName(t.tenant.name);
    setStatus(s);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    load().catch((e) => {
      if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function announce(message: string) {
    setError(null);
    setNotice(message);
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
        Lifecycle{tenantName ? ` — ${tenantName}` : ""}
      </h1>

      {notice ? <p className="mt-4 text-sm text-green-800">{notice}</p> : null}
      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}

      {status === null && !error ? <p className="mt-4 text-sm text-gray-500">Loading…</p> : null}

      {status ? (
        <>
          <StatusCard status={status} />

          {status.status === "active" ? (
            <CancelForm
              tenantId={id}
              onCancelled={(effectiveAt) =>
                announce(`Cancellation scheduled, effective ${new Date(effectiveAt).toLocaleDateString()}.`)
              }
              onError={setError}
            />
          ) : null}

          {status.status === "suspended" || status.status === "pending_deletion" ? (
            <section className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-5">
              <h2 className="text-sm font-semibold">Reactivate</h2>
              <p className="mt-1 text-sm text-gray-700">
                Resumes billing and intake, returns the tenant to active. No re-onboarding, no data
                loss — every order, catalog item and learned rule is exactly as it was.
              </p>
              <ReactivateButton tenantId={id} onDone={() => announce("Reactivated.")} />
            </section>
          ) : null}
        </>
      ) : null}
    </>
  );
}

function StatusCard({ status }: { status: LifecycleStatus }) {
  return (
    <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-xl border border-gray-200 bg-white p-5 text-sm">
      <dt className="text-gray-500">Status</dt>
      <dd className="font-medium">{status.status}</dd>
      {status.cancellation_reason ? (
        <>
          <dt className="text-gray-500">Cancellation reason</dt>
          <dd>{status.cancellation_reason}</dd>
        </>
      ) : null}
      {status.cancellation_effective_at ? (
        <>
          <dt className="text-gray-500">Effective date</dt>
          <dd>{new Date(status.cancellation_effective_at).toLocaleString()}</dd>
        </>
      ) : null}
      {status.deletion_scheduled_at ? (
        <>
          <dt className="text-gray-500">Deletion date</dt>
          <dd>{new Date(status.deletion_scheduled_at).toLocaleString()}</dd>
        </>
      ) : null}
      <dt className="text-gray-500">Stripe subscription</dt>
      <dd>{status.stripe_subscription_status ?? "none"}</dd>
      {status.first_past_due_at ? (
        <>
          <dt className="text-gray-500">First past-due notice</dt>
          <dd>{new Date(status.first_past_due_at).toLocaleString()}</dd>
        </>
      ) : null}
    </dl>
  );
}

function CancelForm({
  tenantId,
  onCancelled,
  onError,
}: {
  tenantId: string;
  onCancelled: (effectiveAt: string) => void;
  onError: (e: CatalogError) => void;
}) {
  const [reason, setReason] = useState<CancellationReason>("customer_requested");
  const [note, setNote] = useState("");
  const [override, setOverride] = useState("");
  const [preview, setPreview] = useState<CancelPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPreview(null);
    setConfirming(false);
    setPreviewing(true);
    previewCancel(tenantId, reason)
      .then((p) => {
        if (!cancelled) setPreview(p);
      })
      .catch((e) => {
        if (!cancelled) onError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      })
      .finally(() => {
        if (!cancelled) setPreviewing(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, reason]);

  async function submit() {
    setBusy(true);
    try {
      const result = await cancelTenant(tenantId, {
        reason,
        note: note || undefined,
        // <input type="datetime-local"> has no offset -- convert via the
        // browser's own local-time interpretation to an unambiguous ISO
        // instant before it reaches the backend's aware-datetime compare.
        override_effective_at: override ? new Date(override).toISOString() : undefined,
      });
      onCancelled(result.cancellation_effective_at);
    } catch (e) {
      onError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
      <h2 className="text-sm font-semibold">Cancel tenant</h2>

      <label className="mt-3 block text-sm">
        Reason
        <select
          value={reason}
          onChange={(e) => setReason(e.target.value as CancellationReason)}
          data-testid="cancel-reason"
          className="mt-1 block w-full rounded border border-gray-300 px-2 py-1.5"
        >
          {REASONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </label>

      {reason === "for_cause" ? (
        <label className="mt-3 block text-sm">
          Reason for cause (at least 20 characters)
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            data-testid="cancel-note"
            className="mt-1 block w-full rounded border border-gray-300 px-2 py-1.5"
            rows={2}
          />
        </label>
      ) : null}

      {previewing ? <p className="mt-3 text-sm text-gray-500">Computing effective date…</p> : null}
      {preview ? (
        <div data-testid="cancel-preview" className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
          Effective date: <strong>{new Date(preview.effective_at).toLocaleString()}</strong>
          <br />
          Rule: {preview.rule}
          {preview.flagged ? (
            <>
              <br />
              <span className="text-amber-900">
                No billing record to compute this from yet — double-check before confirming.
              </span>
            </>
          ) : null}
        </div>
      ) : null}

      <label className="mt-3 block text-sm">
        Push the effective date later (optional)
        <input
          type="datetime-local"
          value={override}
          onChange={(e) => setOverride(e.target.value)}
          data-testid="cancel-override"
          className="mt-1 block w-full rounded border border-gray-300 px-2 py-1.5"
        />
      </label>

      {!confirming ? (
        <button
          type="button"
          disabled={!preview || (reason === "for_cause" && note.trim().length < 20)}
          data-testid="cancel-start"
          onClick={() => setConfirming(true)}
          className="mt-4 rounded bg-red-800 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          Cancel this tenant…
        </button>
      ) : (
        <div className="mt-4 space-y-2">
          <p className="text-sm text-red-900">
            Everything keeps working until the effective date above. After that, intake stops and
            billing is cancelled, but the tenant can still sign in and export until the deletion date.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              data-testid="cancel-confirm"
              onClick={() => void submit()}
              className="rounded bg-red-800 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {busy ? "Scheduling…" : "Confirm cancellation"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
              className="rounded border border-gray-300 px-4 py-2 text-sm"
            >
              Back
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function ReactivateButton({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      data-testid="reactivate"
      onClick={async () => {
        setBusy(true);
        try {
          await reactivateTenant(tenantId);
          onDone();
        } finally {
          setBusy(false);
        }
      }}
      className="mt-3 rounded bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
    >
      {busy ? "Reactivating…" : "Reactivate"}
    </button>
  );
}
