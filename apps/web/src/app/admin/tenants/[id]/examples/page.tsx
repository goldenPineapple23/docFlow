"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  getExamplePrompting,
  getTenantOverview,
  setExamplePrompting,
  type ExamplePromptingOverview,
} from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Approved-example prompting (Section 7.13; D-141). The founder's switch for
 * one tenant, and what it would do: which customers have enough approved
 * orders to be shown to the model as examples, and what it has cost this
 * month.
 *
 * Off by default. Switching it on needs the founder to confirm a live golden
 * run with examples has passed (EXM-001 otherwise); switching it off never
 * needs anything and takes effect from the next order.
 */
export default function ExamplesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [data, setData] = useState<ExamplePromptingOverview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getTenantOverview(id), getExamplePrompting(id)])
      .then(([t, overview]) => {
        if (cancelled) return;
        setTenantName(t.tenant.name);
        setData(overview);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function change(enabled: boolean) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await setExamplePrompting(id, { enabled, golden_run_confirmed: confirmed });
      setData(result);
      setConfirmed(false);
      setNotice(
        enabled
          ? "Example prompting is on. It applies from the next order that arrives."
          : "Example prompting is off. The next order is read without examples.",
      );
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  const qualifying = data?.buyers.filter((b) => b.qualifies).length ?? 0;

  return (
    <>
      <Link href={`/admin/tenants/${id}`} className="text-sm text-blue-700 underline">
        ← Tenant
      </Link>
      <h1 className="mt-1 text-xl font-semibold">
        Example prompting{tenantName ? ` — ${tenantName}` : ""}
      </h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        When this is on, an order from a customer with at least {data?.min_approved ?? 10} approved
        orders is read alongside up to {data?.max_examples ?? 3} of that customer&apos;s most recent
        approved orders, so the model sees how their purchase orders are usually laid out. The
        examples are shown as text only and are never a source of values: every value still has to
        be printed on the order being read. No model is trained, and nothing crosses to another
        customer or tenant.
      </p>

      {error ? (
        <div className="mt-4">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}
      {notice ? (
        <p data-testid="examples-notice" className="mt-4 text-sm text-green-800">
          {notice}
        </p>
      ) : null}

      {data === null ? (
        !error ? <p className="mt-5 text-sm text-gray-500">Loading…</p> : null
      ) : (
        <>
          <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold">Switch</h2>
            <p className="mt-1 text-sm" data-testid="examples-state">
              Currently <strong>{data.enabled ? "on" : "off"}</strong>.{" "}
              {qualifying
                ? `${qualifying} customer${qualifying === 1 ? "" : "s"} would get examples.`
                : "No customer has enough approved orders yet, so turning it on changes nothing today."}
            </p>
            {data.enabled ? (
              <button
                type="button"
                disabled={busy}
                data-testid="examples-off"
                onClick={() => change(false)}
                className="mt-3 rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-50"
              >
                Turn off
              </button>
            ) : (
              <>
                <label className="mt-3 flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    data-testid="examples-confirm"
                    onChange={(e) => setConfirmed(e.target.checked)}
                    className="mt-1"
                  />
                  <span>
                    I ran the live golden check with examples
                    (<code>pytest -m live_api tests/test_example_prompting_golden.py</code> in{" "}
                    <code>apps/api</code>) and it passed.
                  </span>
                </label>
                <button
                  type="button"
                  disabled={busy}
                  data-testid="examples-on"
                  onClick={() => change(true)}
                  className="mt-3 rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-50"
                >
                  Turn on
                </button>
              </>
            )}
          </section>

          <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold">Customers with approved orders</h2>
            {data.buyers.length === 0 ? (
              <p className="mt-2 text-sm text-gray-600">No approved orders yet.</p>
            ) : (
              <table className="mt-2 w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="py-1">Customer</th>
                    <th className="py-1 pl-3 text-center">Approved</th>
                    <th className="py-1 pl-3 text-center" title="Orders DocFlow read as text; scans and photos can't be examples">
                      Usable as examples
                    </th>
                    <th className="py-1 pl-3 text-center">Gets examples</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {data.buyers.map((b) => (
                    <tr key={b.buyer_id} data-testid={`examples-buyer-${b.buyer_id}`}>
                      <td className="py-2">{b.name}</td>
                      <td className="py-2 pl-3 text-center tabular-nums">{b.approved}</td>
                      <td className="py-2 pl-3 text-center tabular-nums">{b.with_text}</td>
                      <td className="py-2 pl-3 text-center">
                        {b.qualifies
                          ? "Yes"
                          : b.approved < data.min_approved
                            ? `Not yet (${data.min_approved - b.approved} more)`
                            : "No text-based orders"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold">This month</h2>
            <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
              <dt className="text-gray-600">Orders read with examples</dt>
              <dd data-testid="examples-runs" className="tabular-nums">{data.this_month.runs_with_examples}</dd>
              <dt className="text-gray-600">Example tokens</dt>
              <dd className="tabular-nums">{data.this_month.example_input_tokens.toLocaleString()}</dd>
              <dt className="text-gray-600">Example cost (est.)</dt>
              <dd className="tabular-nums">${data.this_month.example_cost_usd}</dd>
              <dt className="text-gray-600">Customer look-ups (cheap model)</dt>
              <dd className="tabular-nums">
                {data.this_month.routing_runs} · ${data.this_month.routing_cost_usd}
              </dd>
            </dl>
          </section>
        </>
      )}
    </>
  );
}
