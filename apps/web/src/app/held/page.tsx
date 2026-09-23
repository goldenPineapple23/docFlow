"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { getHeld, getIgnoredMail, releaseHeld, type Held, type IgnoredMail } from "@/lib/held";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";

/**
 * "Held for review" (CLAUDE.md Section 7.16.4; D-126): documents DocFlow
 * received and stored but did not process yet, why, and -- for the holds that
 * are the customer's own call about their own buyers -- a way to release them.
 * Nothing here has been discarded, and the page says so.
 *
 * Who may release what is decided by the API. A group this person can't
 * release says so plainly instead of showing a dead button.
 */
export default function HeldPage() {
  const [held, setHeld] = useState<Held | null>(null);
  const [ignored, setIgnored] = useState<IgnoredMail[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<CatalogError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Bumping this refetches; the fetch itself lives in the effect, with setState
  // only in its promise callbacks (react-hooks/set-state-in-effect).
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getHeld(), getIgnoredMail()])
      .then(([h, i]) => {
        if (cancelled) return;
        setHeld(h);
        setIgnored(i.mail);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function release(ids: string[]) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await releaseHeld(ids);
      const n = result.released.length;
      setNotice(
        `Released ${n} ${n === 1 ? "document" : "documents"}. They are being processed now, oldest first.`,
      );
      setSelected(new Set());
      setRefresh((n) => n + 1);
    } catch (e) {
      setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
    } finally {
      setBusy(false);
    }
  }

  const releasable = held?.documents.filter((d) => d.can_release) ?? [];

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-6xl p-6">
        <Link href="/review" className="text-sm text-blue-700 underline">
          ← Purchase orders
        </Link>
        <h1 className="mt-1 text-xl font-semibold">Held for review</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-600">
          These documents arrived and are stored safely, but haven&apos;t been read yet. Nothing has
          been discarded, and every held document is listed below. They don&apos;t count toward your
          monthly documents until they&apos;re released.
        </p>

        {notice ? <p className="mt-4 text-sm text-green-800">{notice}</p> : null}
        {error ? (
          <div className="mt-4">
            <CatalogErrorBox error={error} />
          </div>
        ) : null}
        {held === null && !error ? <p className="mt-4 text-sm text-gray-500">Loading…</p> : null}

        {held && held.total === 0 ? (
          <p className="mt-4 rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-600">
            Nothing is being held right now.
          </p>
        ) : null}

        {held && held.groups.length > 0 ? (
          <section className="mt-5 space-y-3">
            {held.groups.map((g) => (
              <div
                key={g.reason}
                data-testid={`held-group-${g.reason}`}
                className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm"
              >
                <p className="font-medium">
                  {g.count} {g.count === 1 ? "document" : "documents"} — {g.title}
                </p>
                <p className="mt-1">{g.message}</p>
                <p className="mt-1 text-gray-700">{g.action}</p>
              </div>
            ))}
          </section>
        ) : null}

        {held && held.documents.length > 0 ? (
          <section className="mt-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Documents</h2>
              <button
                type="button"
                data-testid="release-selected"
                disabled={busy || selected.size === 0}
                onClick={() => void release([...selected])}
                className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
              >
                {busy ? "Releasing…" : `Release selected (${selected.size})`}
              </button>
            </div>
            <table className="mt-2 w-full text-left text-sm">
              <thead className="text-xs text-gray-500">
                <tr>
                  <th className="w-8 py-1" />
                  <th className="py-1">File</th>
                  <th className="py-1">From</th>
                  <th className="py-1">Received</th>
                </tr>
              </thead>
              <tbody>
                {held.documents.map((d) => (
                  <tr key={d.id} className="border-t border-gray-100">
                    <td className="py-1.5">
                      <input
                        type="checkbox"
                        aria-label={`Select ${d.filename}`}
                        disabled={!d.can_release || busy}
                        checked={selected.has(d.id)}
                        onChange={() => toggle(d.id)}
                      />
                    </td>
                    <td className="py-1.5">{d.filename}</td>
                    <td className="py-1.5">{d.sender_email ?? "—"}</td>
                    <td className="py-1.5">{new Date(d.received_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {releasable.length > 1 ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => setSelected(new Set(releasable.map((d) => d.id)))}
                className="mt-2 text-sm text-blue-700 underline"
              >
                Select everything I can release
              </button>
            ) : null}
          </section>
        ) : null}

        <details className="mt-8">
          <summary className="cursor-pointer text-sm font-semibold">
            Ignored mail{ignored ? ` (${ignored.length})` : ""}
          </summary>
          <p className="mt-1 text-sm text-gray-600">
            Emails that didn&apos;t produce a document, and why. Nothing is silently lost.
          </p>
          {ignored && ignored.length === 0 ? (
            <p className="mt-2 text-sm text-gray-500">No ignored mail.</p>
          ) : null}
          <ul className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm">
            {(ignored ?? []).map((m) => (
              <li key={m.id} className="px-4 py-2">
                <p className="font-medium">
                  {m.title}{" "}
                  <span className="font-normal text-gray-500">
                    — {m.sender_email ?? "unknown sender"}
                  </span>
                </p>
                {m.subject ? <p className="text-gray-600">Subject: {m.subject}</p> : null}
                <p className="text-gray-600">{m.message}</p>
                <p className="text-gray-700">{m.action}</p>
              </li>
            ))}
          </ul>
        </details>
      </main>
    </>
  );
}
