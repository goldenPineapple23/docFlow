"use client";

import { useState } from "react";
import { changeTier, listTiers, type TenantOverview, type Tier } from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * "Change plan" on the Billing card (Section 7.16.1; slice 5.9, D-138). The
 * founder only -- customers are told to contact DocFlow to upgrade.
 *
 * It says what will happen before anything does: the new price and allowance,
 * that Stripe prorates the difference onto the next invoice, and -- for a
 * founding customer -- that the founding price carries over for the rest of
 * their 90 days. The API enforces every rule; this only explains them.
 */
export function PlanChange({ tenant, onChanged }: { tenant: TenantOverview; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [tiers, setTiers] = useState<Tier[]>([]);
  const [choice, setChoice] = useState<Tier["code"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function start() {
    setError(null);
    setDone(null);
    try {
      setTiers((await listTiers()).tiers);
      setChoice(null);
      setOpen(true);
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    }
  }

  const target = tiers.find((t) => t.code === choice) ?? null;
  // The same tier is still a change when the customer is on an older version.
  const unchanged = target !== null && target.code === tenant.tier_code && target.version === tenant.tier_version;
  // A founding customer still inside their 90 days keeps a founding price on
  // the new plan (D-138), so that is the price to show them.
  const foundingNow =
    !!tenant.billing?.founding_price_ends_at && new Date(tenant.billing.founding_price_ends_at) > new Date();
  const price = (t: Tier) =>
    `${t.name} (v${t.version}) — ` +
    (foundingNow && t.promo_monthly_price
      ? `$${t.promo_monthly_price}/month founding price, then $${t.monthly_price}`
      : `$${t.monthly_price}/month`) +
    `, ${t.document_allowance.toLocaleString()} documents`;

  async function confirm() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const { change } = await changeTier(tenant.id, target.code);
      setOpen(false);
      setDone(
        `Moved to ${target.name}. Stripe prorates the difference onto the next invoice.` +
          (change.founding_months_carried > 0
            ? ` Founding price kept for ${change.founding_months_carried} more month${change.founding_months_carried === 1 ? "" : "s"}.`
            : ""),
      );
      await onChanged();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="plan-change" className="mt-4 border-t border-gray-100 pt-4">
      {done ? (
        <p role="status" data-testid="plan-change-done" className="mb-3 rounded border border-blue-200 bg-blue-50 p-3">
          {done}
        </p>
      ) : null}
      {error ? (
        <div className="mb-3">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}

      {!open ? (
        <button
          type="button"
          onClick={() => void start()}
          data-testid="plan-change-open"
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 font-medium text-gray-700 hover:bg-gray-50"
        >
          Change plan…
        </button>
      ) : (
        <div className="space-y-3">
          <fieldset className="space-y-1">
            <legend className="text-xs text-gray-500">Move this customer to</legend>
            {tiers.map((t) => (
              <label key={t.id} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="plan"
                  value={t.code}
                  checked={choice === t.code}
                  onChange={() => setChoice(t.code)}
                />
                <span>
                  {price(t)}
                  {t.code === tenant.tier_code && t.version === tenant.tier_version ? (
                    <span className="text-gray-500"> · current plan</span>
                  ) : null}
                </span>
              </label>
            ))}
          </fieldset>

          {target && !unchanged ? (
            <div data-testid="plan-change-preview" className="rounded border border-amber-200 bg-amber-50 p-3">
              <p>
                From <strong>{tenant.tier_name ?? "no plan"}</strong> (
                {foundingNow && tenant.tier_promo_monthly_price
                  ? `$${tenant.tier_promo_monthly_price}/month founding`
                  : `$${tenant.tier_monthly_price}/month`}
                , {tenant.tier_document_allowance?.toLocaleString()} documents) to <strong>{target.name}</strong> (
                {foundingNow && target.promo_monthly_price
                  ? `$${target.promo_monthly_price}/month founding, then $${target.monthly_price}`
                  : `$${target.monthly_price}/month`}
                , {target.document_allowance.toLocaleString()} documents).
              </p>
              <ul className="mt-1 list-disc pl-5 text-gray-700">
                <li>The monthly allowance changes immediately.</li>
                <li>Stripe prorates the price difference onto the next invoice.</li>
                {tenant.founding_price ? (
                  <li>
                    Founding customer: they keep {target.name}&apos;s founding price for whatever is left of their 90
                    days.
                  </li>
                ) : null}
                <li>A downgrade is allowed even if they&apos;re already over the new allowance this month.</li>
              </ul>
            </div>
          ) : null}

          <div className="flex gap-2">
            <button
              type="button"
              disabled={!target || unchanged || busy}
              onClick={() => void confirm()}
              data-testid="plan-change-confirm"
              className="rounded-md bg-slate-900 px-3 py-1.5 font-medium text-white disabled:opacity-40"
            >
              Change plan
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-md border border-gray-300 bg-white px-3 py-1.5 font-medium text-gray-700"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
