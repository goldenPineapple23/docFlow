import type { CatalogError } from "@/lib/review";

/** A catalog entry, rendered as given (Section 7.16.5). */
export function CatalogErrorBox({ error }: { error: CatalogError }) {
  return (
    <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm">
      <p className="font-medium">{error.title}</p>
      <p className="mt-1">{error.message}</p>
      <p className="mt-1 text-gray-700">{error.action}</p>
    </div>
  );
}
