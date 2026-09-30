"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppHeader } from "@/components/AppHeader";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { PastDueBanner } from "@/components/PastDueBanner";
import {
  getBilling,
  openCardPage,
  openCardUpdatePage,
  type Billing,
} from "@/lib/billing";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";

/**
 * The owner's Billing page (card billing, founder 2026-09-29; D-181).
 *
 * Admins only; a reviewer who reaches it is shown the catalog entry saying why
 * (AUTH-003). Card details go straight to Stripe: "Add a card" and "Update
 * card" each ask the API for a fresh Stripe page and go there. Stripe brings
 * the owner back here afterwards.
 */

function money(amount: string): string {
  const [whole, cents = "00"] = amount.split(".");
  return `$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${cents.padEnd(2, "0").slice(0, 2)}`;
}

// useSearchParams needs a Suspense boundary to prerender (Next's rule), as on
// the review page.
export default function BillingPage() {
  return (
    <Suspense fallback={null}>
      <BillingScreen />
    </Suspense>
  );
}

function BillingScreen() {
  const params = useSearchParams();
  const [billing, setBilling] = useState<Billing | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);
  const [going, setGoing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getBilling()
      .then((data) => {
        if (!cancelled) setBilling(data);
      })
      .catch((e) => {
        if (!cancelled)
          setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function go(open: () => Promise<{ url: string }>) {
    setGoing(true);
    setError(null);
    try {
      const { url } = await open();
      window.location.assign(url);
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      setGoing(false);
    }
  }

  const justSaved = params.get("card") === "saved";

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-3xl p-6">
        <h1 className="text-xl font-semibold">Billing</h1>
        <p className="mt-1 text-sm text-gray-600">
          Your card details go straight to Stripe, our payment provider. DocFlow
          never sees them.
        </p>

        {error ? (
          <div className="mt-4">
            <CatalogErrorBox error={error} />
          </div>
        ) : null}
        {billing === null && !error ? (
          <p className="mt-4 text-sm text-gray-500">Loading…</p>
        ) : null}

        {billing ? (
          <>
            {billing.banner ? <PastDueBanner banner={billing.banner} /> : null}

            {justSaved && !billing.card_on_file ? (
              <p
                data-testid="card-saving"
                className="mt-4 text-sm text-gray-600"
              >
                Stripe has your card. It can take a minute to show here —
                refresh this page shortly.
              </p>
            ) : null}

            <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
              <h2 className="text-sm font-semibold">Card on file</h2>
              {billing.card_on_file ? (
                <>
                  <p
                    data-testid="card-on-file"
                    className="mt-1 text-sm text-gray-700"
                  >
                    A card is on file with Stripe.
                  </p>
                  <button
                    type="button"
                    data-testid="update-card"
                    disabled={going}
                    onClick={() => go(openCardUpdatePage)}
                    className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                  >
                    Update card
                  </button>
                </>
              ) : (
                <>
                  <p
                    data-testid="no-card"
                    className="mt-1 text-sm text-gray-700"
                  >
                    No card is on file yet.
                    {billing.setup_fee_due_now
                      ? ` Adding one charges your setup fee (${money(billing.setup_fee_due_now)}) now.`
                      : " Adding one charges nothing now."}
                  </p>
                  <button
                    type="button"
                    data-testid="add-card"
                    disabled={going}
                    onClick={() => go(openCardPage)}
                    className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                  >
                    Add a card
                  </button>
                </>
              )}
              {billing.setup_fee_paid ? (
                <p
                  data-testid="setup-fee-paid"
                  className="mt-3 text-sm text-gray-600"
                >
                  Your setup fee is paid.
                </p>
              ) : null}
            </section>
          </>
        ) : null}
      </main>
    </>
  );
}
