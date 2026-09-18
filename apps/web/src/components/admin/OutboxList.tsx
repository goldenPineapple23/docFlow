"use client";

import type { OutboxEmail } from "@/lib/admin";

/**
 * Emails from the outbox (D-103). A `held` email has not been sent -- no
 * provider is configured -- and its body, including any link, is shown so
 * the founder can deliver it by hand. Rendered as plain text; nothing here
 * is ever treated as markup.
 */
const STATUS_STYLE: Record<OutboxEmail["status"], string> = {
  held: "bg-amber-100 text-amber-800",
  queued: "bg-sky-100 text-sky-800",
  sent: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export function OutboxList({ emails }: { emails: OutboxEmail[] }) {
  if (emails.length === 0) {
    return <p className="mt-2 text-sm text-gray-500">No emails yet.</p>;
  }
  return (
    <ul data-testid="outbox" className="mt-2 space-y-2">
      {emails.map((email) => (
        <li key={email.id} className="rounded-xl border border-gray-200 bg-white">
          <details>
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-4 py-2 text-sm">
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[email.status]}`}>
                {email.status === "held" ? "held — not sent" : email.status}
              </span>
              <span className="font-medium">{email.subject}</span>
              <span className="text-gray-500">
                to {email.to_address} · {new Date(email.created_at).toLocaleString()}
                {email.tenant_name ? ` · ${email.tenant_name}` : ""}
              </span>
            </summary>
            <pre className="whitespace-pre-wrap break-all border-t border-gray-100 px-4 py-3 text-xs">
              {email.body_text}
            </pre>
          </details>
        </li>
      ))}
    </ul>
  );
}
