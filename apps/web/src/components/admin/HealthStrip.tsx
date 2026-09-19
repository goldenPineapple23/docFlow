"use client";

import { useState } from "react";
import { recomputeRollup, type Dashboard } from "@/lib/admin";

/**
 * The health strip (CLAUDE.md Section 7.15.3): "cheap signals from your own
 * system, not a rebuilt APM". Each number is one indexed query about right
 * now. Next to it, links OUT to the tools that do this properly -- DocFlow
 * does not rebuild uptime monitoring, error aggregation or billing
 * analytics (Section 10).
 */

const LINKS: Array<{ label: string; href: string; what: string }> = [
  { label: "Sentry", href: "https://sentry.io/", what: "errors" },
  { label: "Supabase", href: "https://supabase.com/dashboard", what: "database" },
  { label: "Stripe", href: "https://dashboard.stripe.com/", what: "billing" },
];

function Stat({ label, value, tone }: { label: string; value: string; tone?: "warn" | "bad" }) {
  const colour = tone === "bad" ? "text-red-700" : tone === "warn" ? "text-amber-700" : "text-slate-900";
  return (
    <div className="min-w-[8rem]">
      <dt className="text-[11px] uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className={`mt-0.5 text-lg font-semibold tabular-nums ${colour}`}>{value}</dd>
    </div>
  );
}

function minutes(value: string | null): string {
  if (value === null) return "—";
  const m = Math.round(Number(value));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d`;
}

function usd(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `$${Number(value).toFixed(2)}`;
}

export function HealthStrip({ data, onRecomputed }: { data: Dashboard; onRecomputed: () => void }) {
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);
  const { health, queues } = data;
  const failureRate =
    health.model_calls_hour > 0
      ? Math.round((health.model_failures_hour / health.model_calls_hour) * 100)
      : 0;

  return (
    <section data-testid="health-strip" className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Right now</h2>
        <p className="text-xs text-gray-500">
          {LINKS.map((l, i) => (
            <span key={l.label}>
              {i > 0 ? " · " : ""}
              <a href={l.href} target="_blank" rel="noreferrer" className="text-blue-700 hover:underline">
                {l.label}
              </a>{" "}
              for {l.what}
            </span>
          ))}
        </p>
      </div>

      <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-4">
        <Stat
          label="Waiting"
          value={`${health.pending + health.processing}`}
          tone={health.pending + health.processing > 50 ? "warn" : undefined}
        />
        <Stat
          label="Oldest waiting"
          value={minutes(health.oldest_waiting_minutes)}
          tone={Number(health.oldest_waiting_minutes ?? 0) > 60 ? "warn" : undefined}
        />
        <Stat label="Queue (now / bulk)" value={`${queues.interactive ?? "—"} / ${queues.bulk ?? "—"}`} />
        <Stat label="Worker" value={data.worker ? "answering" : "silent"} tone={data.worker ? undefined : "bad"} />
        <Stat label="Read today" value={`${health.documents_today}`} />
        <Stat label="Awaiting review" value={`${health.needs_review}`} />
        <Stat
          label="Model errors (1h)"
          value={health.model_calls_hour ? `${failureRate}%` : "—"}
          tone={failureRate >= 20 ? "bad" : failureRate > 0 ? "warn" : undefined}
        />
        <Stat label="AI spend today" value={usd(health.spend_today)} />
        <Stat label="Yesterday" value={usd(health.spend_yesterday)} />
      </dl>

      <p className="mt-4 flex flex-wrap items-center gap-2 text-xs text-gray-500">
        <span data-testid="rollup-state" className={data.rollup_is_stale ? "text-amber-800" : undefined}>
          {data.rollup?.finished_at
            ? `Figures below last recomputed ${new Date(data.rollup.finished_at).toLocaleString()}`
            : "The nightly figures have never been computed"}
          {data.rollup_is_stale
            ? ` — that is more than ${data.rollup_stale_hours}h ago, so the numbers below may be behind.`
            : ""}
        </span>
        <button
          type="button"
          disabled={busy}
          data-testid="recompute"
          onClick={async () => {
            setBusy(true);
            try {
              await recomputeRollup(2);
              setQueued(true);
              onRecomputed();
            } finally {
              setBusy(false);
            }
          }}
          className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50 disabled:opacity-50"
        >
          {busy ? "Queueing…" : "Recompute"}
        </button>
        {queued ? <span className="text-gray-600">Queued — reload in a moment.</span> : null}
      </p>
    </section>
  );
}
