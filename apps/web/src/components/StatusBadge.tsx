/**
 * A document's status, as a word a person uses rather than a database value.
 *
 * The first walkthrough tester read raw statuses off the screen and had to
 * work out what they meant. `needs_review` is not a phrase anyone says, and
 * "exported" was read as possibly meaning *imported*.
 *
 * Styled as a soft filled pill rather than an outlined chip: an outline ring
 * reads as a border around a form control, which is what these looked like.
 * A filled pill with no ring, generous corner radius and a nowrap label is
 * the shape that says "label", not "input".
 */

export const PILL = "inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium";

const STATUS: Record<string, { label: string; className: string }> = {
  staged: { label: "Uploaded, not run yet", className: "bg-slate-100 text-slate-700" },
  pending: { label: "Waiting to be read", className: "bg-slate-100 text-slate-700" },
  processing: { label: "Being read", className: "bg-sky-100 text-sky-800" },
  needs_review: { label: "Needs review", className: "bg-amber-100 text-amber-800" },
  approved: { label: "Approved", className: "bg-emerald-100 text-emerald-800" },
  exported: { label: "Exported to file", className: "bg-emerald-100 text-emerald-800" },
  rejected: { label: "Rejected", className: "bg-rose-100 text-rose-800" },
  failed: { label: "Couldn't be read", className: "bg-rose-100 text-rose-800" },
  quarantined: { label: "Held for review", className: "bg-violet-100 text-violet-800" },
};

export function statusLabel(status: string): string {
  return STATUS[status]?.label ?? status;
}

export function StatusBadge({ status }: { status: string }) {
  const entry = STATUS[status] ?? { label: status, className: "bg-slate-100 text-slate-700" };
  return (
    <span data-testid="status-badge" className={`${PILL} ${entry.className}`}>
      {entry.label}
    </span>
  );
}
