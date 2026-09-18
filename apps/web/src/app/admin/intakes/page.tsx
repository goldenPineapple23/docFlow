"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createIntake, listIntakes, type IntakeSummary } from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Onboarding Step 1 -- intake staging (CLAUDE.md Section 7.15.2).
 *
 * A prospect emails their catalog, customer list and sample POs to the
 * founder; the founder records the prospect here and uploads the files into
 * staging. Nothing here belongs to any tenant until Step 2 creates one.
 */
export default function IntakesPage() {
  const router = useRouter();
  const [intakes, setIntakes] = useState<IntakeSummary[] | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listIntakes()
      .then(({ intakes: rows }) => {
        if (!cancelled) setIntakes(rows);
      })
      .catch((e) => {
        if (!cancelled && e instanceof ReviewApiError) setError(e.catalog);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <h1 className="text-xl font-semibold">Intakes</h1>
      <p className="mt-1 text-sm text-gray-600">
        Step 1 of onboarding. Record a prospect, then upload the files they emailed you. Files
        stay in staging until you create the tenant.
      </p>

      <form
        className="mt-5 grid gap-3 rounded-xl border border-gray-200 bg-white p-5 sm:grid-cols-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const { intake_id } = await createIntake({
              prospect_name: name,
              contact_email: email || null,
              source: "email",
              notes: notes || null,
            });
            router.push(`/admin/intakes/${intake_id}`);
          } catch (err) {
            if (err instanceof ReviewApiError) setError(err.catalog);
            setBusy(false);
          }
        }}
      >
        <label className="text-sm">
          <span className="font-medium">Prospect company</span>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            data-testid="intake-name"
            className="mt-1 w-full rounded border px-3 py-2"
          />
        </label>
        <label className="text-sm">
          <span className="font-medium">Contact email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          <span className="font-medium">Notes</span>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2"
          />
        </label>
        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={busy}
            data-testid="intake-create"
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Creating…" : "Start intake"}
          </button>
        </div>
      </form>

      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}

      <table className="mt-6 w-full overflow-hidden rounded-xl border border-gray-200 bg-white text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th className="px-4 py-2">Prospect</th>
            <th className="px-4 py-2">Received</th>
            <th className="px-4 py-2">Files</th>
            <th className="px-4 py-2">Tenant</th>
          </tr>
        </thead>
        <tbody>
          {(intakes ?? []).map((row) => (
            <tr key={row.id} className="border-t border-gray-100">
              <td className="px-4 py-2">
                <Link href={`/admin/intakes/${row.id}`} className="font-medium text-blue-700 hover:underline">
                  {row.prospect_name}
                </Link>
              </td>
              <td className="px-4 py-2 text-gray-600">{new Date(row.received_at).toLocaleDateString()}</td>
              <td className="px-4 py-2">{row.file_count}</td>
              <td className="px-4 py-2">
                {row.linked_tenant_id ? (
                  <Link href={`/admin/tenants/${row.linked_tenant_id}`} className="text-blue-700 hover:underline">
                    {row.linked_tenant_name}
                  </Link>
                ) : (
                  <span className="text-gray-500">Not created yet</span>
                )}
              </td>
            </tr>
          ))}
          {intakes !== null && intakes.length === 0 ? (
            <tr>
              <td colSpan={4} className="px-4 py-3 text-gray-500">
                No intakes yet.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </>
  );
}
