"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { changeRule, getRules, getTenantOverview, type LearnedRule } from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { PILL } from "@/components/StatusBadge";

/**
 * Learned rules (Section 7.13; D-119): everything DocFlow has been taught for
 * this tenant, by whom, and how often it has fired. Rules are only ever made
 * by a person -- a reviewer confirming a match, or a merge -- so this screen
 * never creates one. It switches them off, back on, or deletes them.
 */

const TYPE_LABEL: Record<LearnedRule["rule_type"], string> = {
  sku_mapping: "Product wording → SKU",
  buyer_alias: "Customer name → customer",
  uom_alias: "Unit wording → unit",
  field_hint: "Field hint",
};

function when(rule: LearnedRule): string {
  const v = rule.match_value;
  if (rule.rule_type === "sku_mapping" && typeof v.raw_description === "string") return v.raw_description;
  if (rule.rule_type === "buyer_alias" && typeof v.alias_name === "string") return v.alias_name;
  return rule.match_key;
}

function means(rule: LearnedRule): string {
  const v = rule.match_value;
  switch (rule.rule_type) {
    case "sku_mapping":
      return rule.item_sku
        ? `${rule.item_sku}${rule.item_description ? ` · ${rule.item_description}` : ""}`
        : String(v.sku ?? "—");
    case "buyer_alias":
      return rule.buyer_name ?? "—";
    case "uom_alias":
      return String(v.unit_of_measure ?? "—");
    default:
      return JSON.stringify(v);
  }
}

export default function RulesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [rules, setRules] = useState<LearnedRule[] | null>(null);
  const [type, setType] = useState<"all" | LearnedRule["rule_type"]>("all");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  const load = useCallback(async () => {
    setRules((await getRules(id)).rules);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getTenantOverview(id), getRules(id)])
      .then(([t, r]) => {
        if (cancelled) return;
        setTenantName(t.tenant.name);
        setRules(r.rules);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const shown = useMemo(
    () => (rules ?? []).filter((r) => type === "all" || r.rule_type === type),
    [rules, type],
  );

  async function change(rule: LearnedRule, action: "disable" | "enable" | "delete") {
    setBusy(rule.id);
    setError(null);
    try {
      await changeRule(id, rule.id, action);
      setDeleting(null);
      await load();
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <Link href={`/admin/tenants/${id}`} className="text-sm text-blue-700 underline">
        ← Tenant
      </Link>
      <h1 className="mt-1 text-xl font-semibold">Learned rules{tenantName ? ` — ${tenantName}` : ""}</h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        What DocFlow has been taught for this customer. Every rule was confirmed by a person, in a
        review or a merge. Switching one off stops it being used on the next order; orders already
        read keep their values, and nothing here overrides a human edit.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-gray-600">Show</span>
          <select
            value={type}
            onChange={(e) => setType(e.target.value as typeof type)}
            data-testid="rules-filter"
            className="rounded border px-2 py-1"
          >
            <option value="all">All rules</option>
            {Object.entries(TYPE_LABEL).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {rules ? <span className="text-gray-500">{shown.length} shown</span> : null}
      </div>

      {error ? (
        <div className="mt-4">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}

      {rules === null ? (
        !error ? <p className="mt-5 text-sm text-gray-500">Loading…</p> : null
      ) : shown.length === 0 ? (
        <p data-testid="rules-empty" className="mt-5 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600">
          No rules yet. They appear as reviewers confirm matches and as customers are merged.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-gray-200 bg-white">
          <table data-testid="rules-table" className="w-full min-w-[56rem] text-left text-sm">
            <thead className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-3 py-2">Rule</th>
                <th className="px-3 py-2">For</th>
                <th className="px-3 py-2">When the order says</th>
                <th className="px-3 py-2">It means</th>
                <th className="px-3 py-2 text-right">Used</th>
                <th className="px-3 py-2">Confirmed by</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {shown.map((r) => (
                <tr key={r.id} data-testid="rule-row" className={r.status === "disabled" ? "text-gray-500" : ""}>
                  <td className="px-3 py-2">{TYPE_LABEL[r.rule_type]}</td>
                  <td className="px-3 py-2">{r.rule_type === "buyer_alias" ? "—" : (r.buyer_name ?? "All customers")}</td>
                  <td className="px-3 py-2">“{when(r)}”</td>
                  <td className="px-3 py-2">
                    {means(r)}
                    {r.item_retired ? (
                      <span className={`${PILL} ml-2 bg-amber-100 text-amber-900`}>SKU retired</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.times_applied}</td>
                  <td className="px-3 py-2">
                    {r.by_docflow_support ? "DocFlow support" : (r.confirmed_by_email ?? "—")}
                    <span className="block text-xs text-gray-500">
                      {new Date(r.created_at).toLocaleDateString()}
                      {r.source_document_id ? (
                        <>
                          {" · "}
                          <Link
                            href={`/admin/tenants/${id}/review/${r.source_document_id}`}
                            className="text-blue-700 hover:underline"
                          >
                            {r.source_po_number ? `PO ${r.source_po_number}` : "source order"}
                          </Link>
                        </>
                      ) : null}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`${PILL} ${r.status === "active" ? "bg-sky-100 text-sky-900" : "bg-gray-100 text-gray-700"}`}
                    >
                      {r.status === "active" ? "On" : "Off"}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    {deleting === r.id ? (
                      <>
                        <button
                          type="button"
                          disabled={busy === r.id}
                          data-testid="rule-delete-confirm"
                          onClick={() => void change(r, "delete")}
                          className="text-red-700 underline"
                        >
                          Delete it
                        </button>{" "}
                        <button type="button" onClick={() => setDeleting(null)} className="ml-2 text-gray-600 underline">
                          Keep
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busy === r.id}
                          data-testid="rule-toggle"
                          onClick={() => void change(r, r.status === "active" ? "disable" : "enable")}
                          className="text-blue-700 underline"
                        >
                          {r.status === "active" ? "Switch off" : "Switch on"}
                        </button>
                        <button
                          type="button"
                          disabled={busy === r.id}
                          data-testid="rule-delete"
                          onClick={() => setDeleting(r.id)}
                          className="ml-3 text-gray-600 underline"
                        >
                          Delete…
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
