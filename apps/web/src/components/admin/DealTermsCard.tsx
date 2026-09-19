"use client";

import { useState } from "react";
import {
  listSetupFeePresets,
  listTiers,
  updateDealTerms,
  type DealTerms,
  type SetupFeePreset,
  type TenantOverview,
  type Tier,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { DealTermsFields, dealSummary, emptyDeal } from "@/components/admin/DealTermsFields";

/**
 * The tenant page's Deal terms (D-117): what was agreed before onboarding,
 * and what go-live will bill. Editable until go-live -- each save is logged
 * with before and after -- and locked once the tenant is live.
 */
export function DealTermsCard({ tenant, onChanged }: { tenant: TenantOverview; onChanged: () => Promise<void> }) {
  const live = tenant.onboarding_status === "live";
  const recorded = tenant.setup_fee_amount != null && tenant.setup_fee_billing != null;

  const [editing, setEditing] = useState(false);
  const [tiers, setTiers] = useState<Tier[]>([]);
  const [presets, setPresets] = useState<SetupFeePreset[]>([]);
  const [deal, setDeal] = useState<DealTerms>(emptyDeal);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);

  async function startEditing() {
    setError(null);
    try {
      const [t, p] = await Promise.all([listTiers(), listSetupFeePresets()]);
      setTiers(t.tiers);
      setPresets(p.presets);
      setDeal({
        tier: (tenant.tier_code as Tier["code"] | null) ?? "growth",
        setup_fee_preset: tenant.setup_fee_preset ?? "standard",
        setup_fee_amount:
          tenant.setup_fee_amount ?? p.presets.find((x) => x.code === (tenant.setup_fee_preset ?? "standard"))?.default_amount ?? null,
        setup_fee_billing: tenant.setup_fee_billing ?? "stripe",
        setup_fee_note: tenant.setup_fee_note,
        founding_price: tenant.founding_price,
      });
      setEditing(true);
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await updateDealTerms(tenant.id, { ...deal, setup_fee_note: deal.setup_fee_note?.trim() || null });
      setEditing(false);
      await onChanged();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section data-testid="deal-terms-card" className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Deal terms</h2>
        {!live && !editing ? (
          <button
            type="button"
            data-testid="deal-edit"
            onClick={() => void startEditing()}
            className="text-sm text-blue-700 underline"
          >
            {recorded ? "Change" : "Set the deal"}
          </button>
        ) : null}
      </div>

      {editing ? (
        <form
          className="mt-3"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <DealTermsFields value={deal} onChange={setDeal} tiers={tiers} presets={presets} />
          <div className="mt-4 flex gap-3">
            <button
              type="submit"
              disabled={busy}
              data-testid="deal-save"
              className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save deal terms"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
              className="rounded border border-gray-300 px-4 py-2 text-sm"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : recorded ? (
        <>
          <p data-testid="deal-summary" className="mt-1 text-sm">
            {dealSummary({
              tierName: tenant.tier_name,
              monthlyPrice: tenant.tier_monthly_price,
              promoMonthlyPrice: tenant.tier_promo_monthly_price,
              promoDays: tenant.tier_promo_days,
              founding: tenant.founding_price,
              feeAmount: tenant.setup_fee_amount,
              feeBilling: tenant.setup_fee_billing,
              presetName: tenant.setup_fee_preset_name,
              note: tenant.setup_fee_note,
            })}
          </p>
          {tenant.setup_fee_note && !/^0+(\.0+)?$/.test(tenant.setup_fee_amount ?? "") ? (
            <p className="mt-0.5 text-sm text-gray-600">Note: {tenant.setup_fee_note}</p>
          ) : null}
          <p className="mt-1 text-xs text-gray-500">
            {live ? "Billed at go-live; locked." : "Nothing is billed until go-live."}
          </p>
        </>
      ) : (
        <p data-testid="deal-missing" className="mt-1 text-sm text-amber-800">
          No deal recorded yet. Set it before go-live — go-live bills exactly what is recorded here.
        </p>
      )}

      {error ? (
        <div className="mt-3">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}
    </section>
  );
}
