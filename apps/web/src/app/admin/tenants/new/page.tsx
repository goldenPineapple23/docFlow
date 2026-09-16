"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";

// CLAUDE.md Section 7.15.2 Step 2 (Phase 0 minimal version): one form,
// creating the tenant, its first owner user (pending invite), and its
// intake address, all in a single backend transaction. This calls the
// same admin_data_access.create_tenant function Phase 5's fuller setup
// tool will extend -- there is no second, duplicate tenant-creation path.
export default function NewTenantPage() {
  const [name, setName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [primaryCurrency, setPrimaryCurrency] = useState("USD");
  const [timezone, setTimezone] = useState("UTC");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ tenant_id: string; owner_user_id: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);

    const res = await apiFetch("/admin/tenants/new", {
      method: "POST",
      body: JSON.stringify({
        name,
        owner_email: ownerEmail,
        primary_currency: primaryCurrency,
        timezone,
      }),
    });

    setSubmitting(false);
    if (!res.ok) {
      setError("Couldn't create the tenant. Check the values and try again.");
      return;
    }
    setResult(await res.json());
    setName("");
    setOwnerEmail("");
  }

  return (
    <main className="mx-auto max-w-lg p-8">
      <h1 className="text-xl font-semibold">Create a new tenant</h1>
      <p className="mt-1 text-sm text-gray-500">
        Onboarding Step 2. Creates the tenant, its first owner user (pending invite), and its
        intake address in one transaction.
      </p>

      <form onSubmit={handleSubmit} className="mt-6 space-y-4">
        <div className="space-y-1">
          <label htmlFor="name" className="block text-sm font-medium">
            Company name
          </label>
          <input
            id="name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded border px-3 py-2"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="owner_email" className="block text-sm font-medium">
            Owner email
          </label>
          <input
            id="owner_email"
            type="email"
            required
            value={ownerEmail}
            onChange={(e) => setOwnerEmail(e.target.value)}
            className="w-full rounded border px-3 py-2"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label htmlFor="currency" className="block text-sm font-medium">
              Primary currency
            </label>
            <input
              id="currency"
              value={primaryCurrency}
              onChange={(e) => setPrimaryCurrency(e.target.value)}
              className="w-full rounded border px-3 py-2"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="timezone" className="block text-sm font-medium">
              Timezone
            </label>
            <input
              id="timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="w-full rounded border px-3 py-2"
            />
          </div>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}
        {result && (
          <p className="text-sm text-green-700">
            Created tenant {result.tenant_id} with owner user {result.owner_user_id}.
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-black px-3 py-2 text-white disabled:opacity-50"
        >
          {submitting ? "Creating…" : "Create tenant"}
        </button>
      </form>
    </main>
  );
}
