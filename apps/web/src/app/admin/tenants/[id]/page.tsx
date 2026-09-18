"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getTenantOverview, listOutbox, sendInvite, type OutboxEmail, type TenantOverview } from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { OutboxList } from "@/components/admin/OutboxList";

/**
 * A tenant's page (CLAUDE.md Section 7.15.3), Overview tab. The other tabs
 * -- Catalog, Buyers, Test batch, Rules & schema, Documents, Lifecycle,
 * Billing, Audit -- arrive with the slices that build them.
 *
 * Step 3 lives here: "Send invite", usable now or at go-live, re-sendable.
 */
export default function TenantPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenant, setTenant] = useState<TenantOverview | null>(null);
  const [emails, setEmails] = useState<OutboxEmail[]>([]);
  const [error, setError] = useState<CatalogError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [t, o] = await Promise.all([getTenantOverview(id), listOutbox(id)]);
    setTenant(t.tenant);
    setEmails(o.emails);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getTenantOverview(id), listOutbox(id)])
      .then(([t, o]) => {
        if (cancelled) return;
        setTenant(t.tenant);
        setEmails(o.emails);
      })
      .catch((e) => {
        if (!cancelled && e instanceof ReviewApiError) setError(e.catalog);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (tenant === null) {
    return error ? <CatalogErrorBox error={error} /> : <p className="text-sm text-gray-500">Loading…</p>;
  }

  return (
    <>
      <Link href="/admin/tenants" className="text-sm text-blue-700 underline">
        ← Tenants
      </Link>
      <h1 className="mt-1 text-xl font-semibold">{tenant.name}</h1>
      <p className="text-sm text-gray-600">
        {tenant.status} · onboarding: {tenant.onboarding_status.replaceAll("_", " ")}
      </p>

      <dl data-testid="tenant-overview" className="mt-5 grid gap-x-6 gap-y-3 rounded-xl border border-gray-200 bg-white p-5 text-sm sm:grid-cols-2">
        <Field label="Plan">
          {tenant.tier_name ? `${tenant.tier_name} (v${tenant.tier_version}) — $${tenant.tier_monthly_price}/month, ${tenant.tier_document_allowance?.toLocaleString()} documents` : "None"}
        </Field>
        <Field label="Currency / timezone">
          {tenant.primary_currency} · {tenant.timezone}
        </Field>
        <Field label="Owner">
          {tenant.owner ? (
            <>
              {tenant.owner.email}{" "}
              <span className="text-gray-500">
                {tenant.owner.has_login ? "· invited" : "· not invited yet"}
              </span>
            </>
          ) : (
            "None"
          )}
        </Field>
        <Field label="Intake address">
          <span className="font-mono text-xs">{tenant.intake_address}</span>{" "}
          <span className={tenant.intake_address_active ? "text-green-700" : "text-amber-700"}>
            {tenant.intake_address_active ? "· live" : "· not live until go-live"}
          </span>
        </Field>
        <Field label="Stripe customer">
          <span className="font-mono text-xs">{tenant.stripe_customer_id ?? "none"}</span>
        </Field>
        <Field label="Created">{new Date(tenant.created_at).toLocaleString()}</Field>
      </dl>

      <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-semibold">Invite the owner</h2>
        <p className="mt-1 text-sm text-gray-600">
          Step 3. Sends {tenant.owner?.email ?? "the owner"} a link to set a password. You can send
          it now or at go-live, and send it again if it expires.
          {tenant.invite_sent_at ? ` Last sent ${new Date(tenant.invite_sent_at).toLocaleString()}.` : ""}
        </p>
        <button
          type="button"
          disabled={busy}
          data-testid="send-invite"
          onClick={async () => {
            setBusy(true);
            setError(null);
            setNotice(null);
            try {
              const result = await sendInvite(id);
              setNotice(
                result.held
                  ? "Invite created. No email provider is set up yet, so it is held in the outbox below — copy the link from there."
                  : "Invite sent.",
              );
              await refresh();
            } catch (e) {
              if (e instanceof ReviewApiError) setError(e.catalog);
            } finally {
              setBusy(false);
            }
          }}
          className="mt-3 rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {tenant.invite_sent_at ? "Send the invite again" : "Send invite"}
        </button>
        {notice ? <p data-testid="invite-notice" className="mt-2 text-sm text-green-800">{notice}</p> : null}
      </section>

      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}

      <h2 className="mt-6 text-sm font-semibold">Emails to this tenant</h2>
      <OutboxList emails={emails} />
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}
