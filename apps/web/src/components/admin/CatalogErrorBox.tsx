import { failedFieldLabels } from "@/lib/fieldLabels";
import type { CatalogError } from "@/lib/review";

/**
 * A catalog entry, rendered as given (Section 7.16.5). When the answer names
 * the fields it refused (REQ-001, REQ-002; D-241), their labels go in front
 * of the title, as plain text.
 */
export function CatalogErrorBox({ error }: { error: CatalogError }) {
  const labels = failedFieldLabels(error.detail);
  return (
    <div role="alert" data-testid="catalog-error" className="rounded border border-red-300 bg-red-50 p-3 text-sm">
      <p className="font-medium">{labels.length ? `${labels.join(", ")}: ${error.title}` : error.title}</p>
      <p className="mt-1">{error.message}</p>
      <p className="mt-1 text-gray-700">{error.action}</p>
    </div>
  );
}
