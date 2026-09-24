"use client";

import type { Dashboard, Kpis } from "@/lib/admin";

/**
 * The KPI cards (CLAUDE.md Section 7.15.3): the Master Spec's success
 * metrics, computed from the nightly rollup — "the numbers no other tool can
 * give the founder". Each shows the current value and how it moved against
 * the previous window of the same length.
 *
 * Every value here came from `tenant_daily_metrics`; this component does no
 * arithmetic on money beyond formatting.
 */

function share(value: string | null): string {
  return value === null ? "—" : `${Math.round(Number(value) * 100)}%`;
}

function usd(value: string | null, places = 2): string {
  return value === null ? "—" : `$${Number(value).toFixed(places)}`;
}

function hours(value: string | null): string {
  if (value === null) return "—";
  const h = Number(value);
  return h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(1)} h`;
}

/** Up is good unless `lowerIsBetter`. Returns null when either side is unknown. */
function Trend({
  now,
  before,
  lowerIsBetter,
}: {
  now: string | null;
  before: string | null;
  lowerIsBetter?: boolean;
}) {
  if (now === null || before === null) return null;
  const delta = Number(now) - Number(before);
  if (Math.abs(delta) < 1e-9) return <span className="text-xs text-gray-500"> · level</span>;
  const better = lowerIsBetter ? delta < 0 : delta > 0;
  return (
    <span className={`text-xs ${better ? "text-green-700" : "text-amber-700"}`}>
      {" "}
      · {delta > 0 ? "▲" : "▼"} vs previous
    </span>
  );
}

function Card({
  label,
  value,
  note,
  children,
}: {
  label: string;
  value: string;
  note?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <p className="text-[11px] uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">
        {value}
        {children}
      </p>
      {note ? <p className="mt-1 text-xs text-gray-500">{note}</p> : null}
    </div>
  );
}

export function KpiCards({ data }: { data: Dashboard }) {
  const k: Kpis = data.kpis;
  const p: Kpis = data.previous;
  const { money } = data;
  const mrr = Number(money.mrr);
  const margin = mrr > 0 ? (mrr - Number(money.ai_cost_this_month)) / mrr : null;
  const retention = (retained: number, live: number) =>
    live > 0 ? `${Math.round((retained / live) * 100)}%` : "—";

  return (
    <section className="mt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">How the business is doing</h2>
        <p className="text-xs text-gray-500">Last {data.days} days, against the {data.days} before</p>
      </div>
      <div data-testid="kpi-cards" className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Card
          label="Reviewed in 2 minutes"
          value={share(k.review_within_target_share)}
          note={
            k.review_sessions_excluded
              ? `${k.review_sessions} reviews · ${k.review_sessions_excluded} left open too long, excluded`
              : `${k.review_sessions} reviews`
          }
        >
          <Trend now={k.review_within_target_share} before={p.review_within_target_share} />
        </Card>
        <Card
          label="Received → ready to export"
          value={hours(k.median_hours_to_approval)}
          note="Median, receipt to approval"
        >
          <Trend now={k.median_hours_to_approval} before={p.median_hours_to_approval} lowerIsBetter />
        </Card>
        <Card
          label="Approved with no edits"
          value={share(k.zero_edit_share)}
          note={`${k.zero_edit_approvals} of ${k.documents_approved} approved`}
        >
          <Trend now={k.zero_edit_share} before={p.zero_edit_share} />
        </Card>
        <Card
          label="Matched from a learned rule"
          value={share(k.mapping_reuse_share)}
          note={`${k.learned_rule_lines} of ${k.matched_lines} matched lines`}
        >
          <Trend now={k.mapping_reuse_share} before={p.mapping_reuse_share} />
        </Card>
        <Card
          label="Corrections per 100 lines"
          value={k.corrections_per_100_lines ?? "—"}
          note="Should fall month over month"
        >
          <Trend now={k.corrections_per_100_lines} before={p.corrections_per_100_lines} lowerIsBetter />
        </Card>
        <Card
          label="Cost per document"
          value={usd(k.mean_cost_per_document, 3)}
          note={`p95 ${usd(k.p95_cost_per_document, 3)} · ${k.documents_received} read`}
        >
          <Trend now={k.mean_cost_per_document} before={p.mean_cost_per_document} lowerIsBetter />
        </Card>
        <Card
          label="MRR"
          value={usd(money.mrr, 0)}
          note={
            Number(money.mrr_in_trial ?? 0) > 0
              ? `What paying customers actually pay, founding prices included · + ${usd(money.mrr_in_trial ?? null, 0)} in free trial. DocFlow's view — Stripe is the source of truth for cash`
              : "What paying customers actually pay, founding prices included. DocFlow's view — Stripe is the source of truth for cash"
          }
        />
        <Card
          label="Margin after AI"
          value={margin === null ? "—" : `${Math.round(margin * 100)}%`}
          note={`${usd(money.ai_cost_this_month)} of AI this month`}
        />
        <Card
          label="Retention"
          value={`${retention(money.retained_60, money.live_60)} / ${retention(money.retained_90, money.live_90)}`}
          note={`60 / 90 day · ${money.live_tenants} live tenants`}
        />
      </div>
    </section>
  );
}
