"use client";

import { useEffect, useState } from "react";
import { getGoLivePlan, goLive, type GoLivePlan, type TenantOverview } from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { dealSummary, money } from "@/components/admin/DealTermsFields";

/**
 * Onboarding Step 9 (CLAUDE.md Section 7.15.2): one action, enabled only once
 * the test batch is complete.
 *
 * No price is chosen here (D-117). The deal was agreed before onboarding and
 * recorded at Create tenant, so the customer never watches a price being
 * decided; this panel shows one line of what will be billed and does it.
 * Every amount comes from the tenant's deal and tier (Section 10).
 *
 * Billing (D-113): the monthly plan is a Stripe subscription invoiced by
 * email, and the setup fee either goes on that first invoice or is invoiced
 * by hand. That first invoice doesn't go out immediately -- the subscription
 * starts on a trial, so nothing is billed until trial_period_days after
 * go-live (D-125); the tenant itself is live and usable right away.
 *
 * Two clicks, because it bills a customer: "Go live" shows exactly what is
 * about to happen, and "Confirm" does it.
 */
export function GoLivePanel({ tenant, onChanged }: { tenant: TenantOverview; onChanged: () => Promise<void> }) {
  const ready = tenant.onboarding_status === "test_batch_complete";
  const live = tenant.onboarding_status === "live";
  // Changing the deal refetches the plan, so the summary is never stale.
  const dealKey = `${tenant.tier_code}|${tenant.setup_fee_amount}|${tenant.setup_fee_billing}|${tenant.founding_price}`;

  const [plan, setPlan] = useState<GoLivePlan | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    getGoLivePlan(tenant.id)
      .then((p) => {
        if (cancelled) return;
        setPlan(p);
        setError(null);
        setConfirming(false);
      })
      .catch((e) => {
        if (!cancelled) {
          setPlan(null);
          setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [ready, tenant.id, dealKey]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await goLive(tenant.id);
      setConfirming(false);
      await onChanged();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  const summary = plan
    ? dealSummary({
        tierName: plan.tier_name,
        monthlyPrice: plan.monthly_price,
        promoMonthlyPrice: plan.promo_monthly_price,
        promoDays: plan.promo_months ? plan.promo_months * 30 : null,
        founding: plan.founding_price,
        feeAmount: plan.setup_fee_amount,
        feeBilling: plan.setup_fee_billing,
        presetName: plan.setup_fee_preset_name,
        note: plan.setup_fee_note,
      })
    : null;
  const feeBilled = plan !== null && plan.setup_fee_billing === "stripe" && !/^0+(\.0+)?$/.test(plan.setup_fee_amount);

  return (
    <section data-testid="go-live" className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
      <h2 className="text-sm font-semibold">Go live</h2>

      {live ? (
        <p data-testid="go-live-done" className="mt-2 text-sm text-green-800">
          Live since {tenant.went_live_at ? new Date(tenant.went_live_at).toLocaleString() : "—"}.
        </p>
      ) : !ready ? (
        <p className="mt-1 text-sm text-gray-600">Available once the test batch is complete.</p>
      ) : (
        <>
          {summary ? (
            <p data-testid="go-live-deal" className="mt-2 text-sm">
              {summary}
            </p>
          ) : !error ? (
            <p className="mt-2 text-sm text-gray-500">Loading…</p>
          ) : null}

          {confirming && plan ? (
            <div data-testid="go-live-summary" className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
              <p className="font-medium">Going live will:</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                <li>
                  Start the {plan.tier_name} plan at{" "}
                  {plan.founding_price && plan.promo_monthly_price
                    ? `${money(plan.promo_monthly_price)}/month for ${plan.promo_months} months, then ${money(plan.monthly_price)}`
                    : money(plan.monthly_price)}
                  /month. Nothing is billed until {plan.trial_period_days} days after go-live, when
                  Stripe sends one invoice for the first month
                  {feeBilled ? ` plus the ${money(plan.setup_fee_amount)} setup fee` : ""}, due{" "}
                  {plan.invoice_days_until_due} days later.
                </li>
                {!feeBilled ? (
                  <li>
                    {/^0+(\.0+)?$/.test(plan.setup_fee_amount)
                      ? "Charge no setup fee (waived)."
                      : `Record the ${money(plan.setup_fee_amount)} setup fee as invoiced by you.`}
                  </li>
                ) : null}
                <li>Turn on the intake address, so buyers&apos; orders start being read.</li>
                <li>{plan.invite_sent ? "Send the go-live email." : "Send the owner's invite and the go-live email."}</li>
                <li>Schedule the first-week check-in.</li>
              </ul>
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap gap-3">
            {!confirming ? (
              <button
                type="button"
                data-testid="go-live-start"
                disabled={!plan || busy}
                onClick={() => setConfirming(true)}
                className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-300"
              >
                Go live…
              </button>
            ) : (
              <>
                <button
                  type="button"
                  data-testid="go-live-confirm"
                  disabled={busy}
                  onClick={() => void submit()}
                  className="rounded bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {busy ? "Going live…" : "Confirm and go live"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setConfirming(false)}
                  className="rounded border border-gray-300 px-4 py-2 text-sm"
                >
                  Not yet
                </button>
              </>
            )}
          </div>
        </>
      )}

      {error ? (
        <div className="mt-3">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}
    </section>
  );
}
