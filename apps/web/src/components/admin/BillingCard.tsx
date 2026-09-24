"use client";

import type { TenantOverview } from "@/lib/admin";

/**
 * The tenant page's Billing card (Section 7.15.3: "a summary plus a link to
 * that customer in Stripe"; slice 5.9). Only what DocFlow already knows from
 * the webhook-synced columns -- no live Stripe call on page load, and nothing
 * about invoices or payments, which Stripe shows better (Section 10: do not
 * build billing analytics inside DocFlow).
 */

const STATUS: Record<string, { label: string; tone: string }> = {
  trialing: { label: "Free week", tone: "bg-blue-50 text-blue-800 border-blue-200" },
  active: { label: "Paying", tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  past_due: { label: "Payment overdue", tone: "bg-amber-50 text-amber-900 border-amber-200" },
  unpaid: { label: "Unpaid", tone: "bg-red-50 text-red-800 border-red-200" },
  canceled: { label: "Cancelled at Stripe", tone: "bg-gray-100 text-gray-700 border-gray-200" },
};

function day(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : "—";
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

export function BillingCard({ tenant, children }: { tenant: TenantOverview; children?: React.ReactNode }) {
  const b = tenant.billing ?? {
    subscription_id: null,
    status: tenant.stripe_subscription_status,
    current_period_end: null,
    first_past_due_at: null,
    trial_ends_at: null,
    founding_price_ends_at: null,
    stripe_dashboard_url: null,
  };
  const foundingUntil =
    b.founding_price_ends_at && new Date(b.founding_price_ends_at) > new Date() ? b.founding_price_ends_at : null;
  const status = b.status ? (STATUS[b.status] ?? { label: b.status, tone: "bg-gray-100 text-gray-700 border-gray-200" }) : null;
  const setupFee =
    tenant.setup_fee_amount == null
      ? "Not agreed yet"
      : tenant.setup_fee_billing === "invoiced_manually"
        ? `$${tenant.setup_fee_amount} · invoiced by hand${tenant.setup_fee_note ? ` (${tenant.setup_fee_note})` : ""}`
        : `$${tenant.setup_fee_amount} · on the first Stripe invoice`;

  return (
    <section data-testid="billing-card" className="mt-5 rounded-xl border border-gray-200 bg-white p-5 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Billing</h2>
        {b.stripe_dashboard_url ? (
          <a
            href={b.stripe_dashboard_url}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="stripe-link"
            className="text-sm font-medium text-blue-700 hover:underline"
          >
            Open in Stripe ↗
          </a>
        ) : null}
      </div>

      {!b.subscription_id ? (
        <p className="mt-2 text-gray-600">
          No subscription yet. Billing starts at go-live, with a free week before the first invoice.
        </p>
      ) : (
        <dl className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
          <Row label="Plan">
            {!tenant.tier_name ? (
              "None"
            ) : foundingUntil && tenant.tier_promo_monthly_price ? (
              // What a founding customer actually pays (docflow-pricing.docx:
              // founding price for the first 90 days, then the list price).
              <span data-testid="founding-until">
                {tenant.tier_name} (v{tenant.tier_version}) — ${tenant.tier_promo_monthly_price}/month founding price
                until {day(foundingUntil)}, then ${tenant.tier_monthly_price}/month
              </span>
            ) : (
              `${tenant.tier_name} (v${tenant.tier_version}) — $${tenant.tier_monthly_price}/month`
            )}
          </Row>
          <Row label="Subscription">
            {status ? (
              <span data-testid="billing-status" className={`rounded-full border px-2 py-0.5 text-xs font-medium ${status.tone}`}>
                {status.label}
              </span>
            ) : (
              "—"
            )}
          </Row>
          {b.trial_ends_at ? (
            <Row label="Free week ends">{day(b.trial_ends_at)} — the first invoice goes out then</Row>
          ) : (
            <Row label="Current period ends">{day(b.current_period_end)}</Row>
          )}
          <Row label="Setup fee">{setupFee}</Row>
          {b.first_past_due_at ? (
            <Row label="Overdue since">
              <span className="text-amber-900">{day(b.first_past_due_at)}</span>
            </Row>
          ) : null}
        </dl>
      )}
      {children}
    </section>
  );
}
