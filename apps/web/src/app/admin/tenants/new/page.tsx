"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createTenant, listIntakes, listTiers, type IntakeSummary, type Tier } from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Onboarding Step 2 -- create the tenant (CLAUDE.md Section 7.15.2).
 *
 * One form. Submitting it creates, in a single transaction, the tenant on
 * the current version of the chosen tier, its owner (invite pending), its
 * intake address (not live until go-live), the Stripe customer, and moves
 * the linked intake's files out of staging. It calls the same
 * `create_tenant` Phase 0 introduced -- extended, not duplicated.
 */
export default function NewTenantPage() {
  return (
    <Suspense fallback={null}>
      <NewTenantForm />
    </Suspense>
  );
}

function NewTenantForm() {
  const router = useRouter();
  const preselectedIntake = useSearchParams().get("intake");
  const [tiers, setTiers] = useState<Tier[]>([]);
  const [intakes, setIntakes] = useState<IntakeSummary[]>([]);
  const [name, setName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [timezone, setTimezone] = useState("America/New_York");
  const [tier, setTier] = useState("starter");
  const [intakeId, setIntakeId] = useState<string>(preselectedIntake ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listTiers(), listIntakes()])
      .then(([t, i]) => {
        if (cancelled) return;
        setTiers(t.tiers);
        const open = i.intakes.filter((row) => row.linked_tenant_id === null);
        setIntakes(open);
        const chosen = open.find((row) => row.id === preselectedIntake);
        if (chosen) {
          setName((current) => current || chosen.prospect_name);
          setOwnerEmail((current) => current || chosen.contact_email || "");
        }
      })
      .catch((e) => {
        if (!cancelled && e instanceof ReviewApiError) setError(e.catalog);
      });
    return () => {
      cancelled = true;
    };
  }, [preselectedIntake]);

  return (
    <>
      <h1 className="text-xl font-semibold">Create a tenant</h1>
      <p className="mt-1 text-sm text-gray-600">
        Step 2 of onboarding. Creates the tenant, its owner (invite not sent yet), its intake
        address (inactive until go-live) and its Stripe customer, and moves the intake&apos;s files
        in. All or nothing.
      </p>

      <form
        className="mt-5 grid gap-4 rounded-xl border border-gray-200 bg-white p-5 sm:grid-cols-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const result = await createTenant({
              name,
              owner_email: ownerEmail,
              primary_currency: currency,
              timezone,
              tier,
              intake_id: intakeId || null,
            });
            router.push(`/admin/tenants/${result.tenant_id}`);
          } catch (err) {
            if (err instanceof ReviewApiError) setError(err.catalog);
            setBusy(false);
          }
        }}
      >
        <label className="text-sm">
          <span className="font-medium">Company name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} data-testid="tenant-name" className="mt-1 w-full rounded border px-3 py-2" />
        </label>
        <label className="text-sm">
          <span className="font-medium">Owner email</span>
          <input required type="email" value={ownerEmail} onChange={(e) => setOwnerEmail(e.target.value)} data-testid="tenant-owner-email" className="mt-1 w-full rounded border px-3 py-2" />
        </label>
        <label className="text-sm">
          <span className="font-medium">Plan</span>
          <select value={tier} onChange={(e) => setTier(e.target.value)} data-testid="tenant-tier" className="mt-1 w-full rounded border px-3 py-2">
            {tiers.map((t) => (
              <option key={t.code} value={t.code}>
                {t.name} — ${t.monthly_price}/month, {t.document_allowance.toLocaleString()} documents
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="font-medium">Intake</span>
          <select value={intakeId} onChange={(e) => setIntakeId(e.target.value)} data-testid="tenant-intake" className="mt-1 w-full rounded border px-3 py-2">
            <option value="">None</option>
            {intakes.map((i) => (
              <option key={i.id} value={i.id}>
                {i.prospect_name} ({i.file_count} files)
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="font-medium">Primary currency</span>
          <input required maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} className="mt-1 w-full rounded border px-3 py-2" />
        </label>
        <label className="text-sm">
          <span className="font-medium">Timezone</span>
          <input required value={timezone} onChange={(e) => setTimezone(e.target.value)} className="mt-1 w-full rounded border px-3 py-2" />
        </label>
        <div className="sm:col-span-2">
          <button type="submit" disabled={busy} data-testid="tenant-create" className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {busy ? "Creating…" : "Create tenant"}
          </button>
        </div>
      </form>

      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}
    </>
  );
}
