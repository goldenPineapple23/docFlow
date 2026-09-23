"use client";

import { useEffect, useState } from "react";
import { listOutbox, type OutboxEmail } from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { OutboxList } from "@/components/admin/OutboxList";

/**
 * Every email DocFlow has written, newest first (D-103). Until an email
 * provider is configured they are all `held`: this page is how an invite
 * link or an alert reaches the founder in the meantime.
 */
export default function OutboxPage() {
  const [emails, setEmails] = useState<OutboxEmail[] | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    listOutbox()
      .then(({ emails: rows }) => {
        if (!cancelled) setEmails(rows);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <h1 className="text-xl font-semibold">Outbox</h1>
      <p className="mt-1 text-sm text-gray-600">
        Every email DocFlow has written. &ldquo;Held&rdquo; means it was not sent because no email
        provider is set up yet — open it to read it, and send it yourself if it matters.
      </p>
      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}
      {emails === null && !error ? <p className="mt-4 text-sm text-gray-500">Loading…</p> : null}
      {emails ? <OutboxList emails={emails} /> : null}
    </>
  );
}
