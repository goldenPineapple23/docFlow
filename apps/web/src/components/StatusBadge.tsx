/**
 * A document's status, as a word a person uses rather than a database value.
 *
 * The first walkthrough tester read raw statuses off the screen and had to
 * work out what they meant. `needs_review` is not a phrase anyone says, and
 * "exported" was read as possibly meaning *imported*.
 */

const STATUS: Record<string, { label: string; className: string }> = {
  pending: { label: "Waiting to be read", className: "bg-slate-100 text-slate-700 ring-slate-200" },
  processing: { label: "Being read", className: "bg-sky-50 text-sky-800 ring-sky-200" },
  needs_review: { label: "Needs review", className: "bg-amber-50 text-amber-900 ring-amber-300" },
  approved: { label: "Approved", className: "bg-emerald-50 text-emerald-800 ring-emerald-300" },
  exported: { label: "Exported to file", className: "bg-emerald-50 text-emerald-800 ring-emerald-300" },
  rejected: { label: "Rejected", className: "bg-rose-50 text-rose-800 ring-rose-300" },
  failed: { label: "Couldn't be read", className: "bg-rose-50 text-rose-800 ring-rose-300" },
  quarantined: { label: "Held for review", className: "bg-violet-50 text-violet-800 ring-violet-300" },
};

export function statusLabel(status: string): string {
  return STATUS[status]?.label ?? status;
}

export function StatusBadge({ status }: { status: string }) {
  const entry = STATUS[status] ?? {
    label: status,
    className: "bg-slate-100 text-slate-700 ring-slate-200",
  };
  return (
    <span
      data-testid="status-badge"
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${entry.className}`}
    >
      {entry.label}
    </span>
  );
}
