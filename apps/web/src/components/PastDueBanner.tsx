import Link from "next/link";
import type { CatalogError } from "@/lib/review";

/**
 * BIL-006 / BIL-009 (card billing, D-181): a card-billed account whose last
 * payment didn't go through. Every word comes from the error catalog through
 * the API; this only lays it out, and links to the Billing page unless it is
 * already the page being shown.
 */
export function PastDueBanner({
  banner,
  linkToBilling = false,
}: {
  banner: CatalogError;
  linkToBilling?: boolean;
}) {
  return (
    <div
      data-testid="past-due-banner"
      role="alert"
      className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950"
    >
      <p className="font-medium">{banner.title}</p>
      <p className="mt-0.5">{banner.message}</p>
      <p className="mt-0.5 text-amber-900">
        {banner.action}
        {linkToBilling ? (
          <>
            {" "}
            <Link href="/billing" className="font-medium underline">
              Go to Billing
            </Link>
          </>
        ) : null}
      </p>
    </div>
  );
}
