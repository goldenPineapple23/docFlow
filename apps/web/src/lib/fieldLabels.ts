/**
 * The fields a REQ-001 or REQ-002 answer names (D-241), as the form calls
 * them. The API sends only each field's name, never its value or the
 * library's wording; the page puts the label in front of the catalog title,
 * for example "Owner email: This e-mail address can't receive mail".
 */

const LABELS: Record<string, string> = {
  name: "Company name",
  owner_email: "Owner email",
  contact_email: "Contact email",
  prospect_name: "Prospect name",
  primary_currency: "Currency",
};

/** One field's label: the form's own word, or its name spelled out. */
export function fieldLabel(field: string): string {
  const known = LABELS[field];
  if (known) return known;
  const spelled = field.replace(/[._]+/g, " ").trim();
  return spelled ? spelled.charAt(0).toUpperCase() + spelled.slice(1) : field;
}

/** The labels for an answer's `detail.fields`, or none when it names none. */
export function failedFieldLabels(detail: Record<string, unknown> | undefined): string[] {
  const fields = detail?.fields;
  if (!Array.isArray(fields)) return [];
  return fields.filter((f): f is string => typeof f === "string" && f.length > 0).map(fieldLabel);
}
