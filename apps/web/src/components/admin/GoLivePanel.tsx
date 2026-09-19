"use client";

import { useEffect, useState } from "react";
import { getGoLivePlan, goLive, type GoLivePlan, type TenantOverview } from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Onboarding Step 9 (CLAUDE.md Section 7.15.2): one action, enabled only once
 * the test batch is complete. Every price shown comes from the tenant's tier
 * (Section 10: no price typed into code); only the setup fee is entered here,
 * because it is chosen per customer (D-102).
 *
 * Billing (D-113, founder decision): the monthly plan is a Stripe
 * subscription invoiced by email -- nobody has entered a card yet -- and the
 * setup fee either goes on that first invoice or is invoiced by hand.
 *
 * Two clicks, because it bills a customer: "Go live" shows exactly what is
 * about to happen, and "Confirm" does it.
 */
export function GoLivePanel({ tenant, onChanged }: { tenant: TenantOverview; onChanged: () => Promise<void> }) {
  const ready = tenant.onboarding_status === "test_batch_complete";
  const live = tenant.onboarding_status === "live";

  const [plan, setPlan] = useState<GoLivePlan | null>(null);
  const [fee, setFee] = useState("");
  const [billing, setBilling] = useState<"stripe" | "invoiced_manually">("stripe");
  const [note, setNote] = useState("");
  const [founding, setFounding] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    getGoLivePlan(tenant.id)
      .then((p) => {
        if (!cancelled) setPlan(p);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, tenant.id]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await goLive(tenant.id, {
        setup_fee_amount: fee,
        setup_fee_billing: billing,
        setup_fee_note: note.trim() || null,
        founding_price: founding,
      });
      setConfirming(false);
      await onChanged();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section data-testid="go-live" className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
      <h2 className="text-sm font-semibold">9. Go live</h2>

      {live ? (
        <p data-testid="go-live-done" className="mt-2 text-sm text-green-800">
          Live since {tenant.went_live_at ? new Date(tenant.went_live_at).toLocaleString() : "—"}. Setup fee $
          {tenant.setup_fee_amount ?? "—"},{" "}
          {tenant.setup_fee_billing === "invoiced_manually" ? "invoiced by hand" : "on the first Stripe invoice"}
          {tenant.founding_price ? ", founding-customer price" : ""}.
        </p>
      ) : !ready ? (
        <p className="mt-1 text-sm text-gray-600">Available once the test batch is complete.</p>
      ) : (
        <>
          {plan ? (
            <div className="mt-3 grid gap-4 text-sm sm:grid-cols-2">
              <label className="block">
                <span className="font-medium">Setup fee (USD)</span>
                <input
                  value={fee}
                  onChange={(e) => {
                    setFee(e.target.value);
                    setConfirming(false);
                  }}
                  inputMode="decimal"
                  placeholder="e.g. 750.00 — 0 to waive"
                  data-testid="go-live-fee"
                  className="mt-1 w-full rounded border px-2 py-1.5"
                />
              </label>
              <fieldset className="block">
                <legend className="font-medium">How the setup fee is billed</legend>
                <label className="mt-1 flex items-center gap-2">
                  <input
                    type="radio"
                    checked={billing === "stripe"}
                    onChange={() => {
                      setBilling("stripe");
                      setConfirming(false);
                    }}
                  />
                  On the first Stripe invoice
                </label>
                <label className="mt-1 flex items-center gap-2">
                  <input
                    type="radio"
                    checked={billing === "invoiced_manually"}
                    data-testid="go-live-manual"
                    onChange={() => {
                      setBilling("invoiced_manually");
                      setConfirming(false);
                    }}
                  />
                  I&apos;ll invoice it myself
                </label>
              </fieldset>
              {billing === "invoiced_manually" ? (
                <label className="block sm:col-span-2">
                  <span className="font-medium">Note (optional)</span>
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    maxLength={500}
                    placeholder="e.g. invoice #1001 sent 18 Sept"
                    className="mt-1 w-full rounded border px-2 py-1.5"
                  />
                </label>
              ) : null}
              {plan.promo_monthly_price ? (
                <label className="flex items-start gap-2 sm:col-span-2">
                  <input
                    type="checkbox"
                    checked={founding}
                    data-testid="go-live-founding"
                    onChange={(e) => {
                      setFounding(e.target.checked);
                      setConfirming(false);
                    }}
                    className="mt-1"
                  />
                  <span>
                    Founding-customer price: ${plan.promo_monthly_price}/month for the first{" "}
                    {plan.promo_months} months, then ${plan.monthly_price}/month.
                  </span>
                </label>
              ) : null}
            </div>
          ) : (
            <p className="mt-2 text-sm text-gray-500">Loading…</p>
          )}

          {confirming && plan ? (
            <div data-testid="go-live-summary" className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
              <p className="font-medium">Going live will:</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                <li>
                  Start the {plan.tier_name} plan at $
                  {founding && plan.promo_monthly_price
                    ? `${plan.promo_monthly_price}/month for ${plan.promo_months} months, then $${plan.monthly_price}`
                    : plan.monthly_price}
                  /month, invoiced by Stripe with {plan.invoice_days_until_due} days to pay.
                </li>
                <li>
                  {billing === "stripe"
                    ? `Add a $${fee || "0"} setup fee to that first invoice.`
                    : `Record a $${fee || "0"} setup fee that you invoice yourself.`}
                </li>
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
