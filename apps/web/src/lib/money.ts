/**
 * Displaying money (CLAUDE.md Section 7.1).
 *
 * `1356.00` is hard to read and easy to misread as `135.60` at a glance,
 * which on a purchase order is the difference between one order and another.
 * So amounts get thousands separators — **for display only**.
 *
 * Three rules this module follows, and the reasons matter:
 *
 *   1. **It never converts to a Number.** JavaScript has no decimal type, so
 *      `parseFloat("1356.00")` produces a binary float and loses the scale
 *      the value was stored at. Section 7.1 keeps floats away from money
 *      everywhere else in this system; a formatter is not the place to make
 *      an exception. The grouping is done by walking the digit string.
 *   2. **It never touches an editable field.** What a reviewer types is sent
 *      straight to a NUMERIC column, and a comma would either be rejected or
 *      silently reinterpreted. Inputs show the stored value verbatim; only
 *      read-only displays are grouped.
 *   3. **Anything it does not recognise passes through unchanged.** A value
 *      that is not a plain decimal is shown exactly as stored rather than
 *      guessed at — the same discipline extraction follows.
 */

const PLAIN_DECIMAL = /^(-?)(\d+)(\.\d+)?$/;

/**
 * "1356.00" -> "1,356.00". "570.00" -> "570.00". "abc" -> "abc".
 *
 * Grouping is applied to the integer part only; the fractional part is left
 * exactly as it was stored, including trailing zeros, because the scale is
 * part of the value.
 */
export function formatAmount(value: string | null | undefined): string {
  if (value === null || value === undefined) return "";
  const trimmed = value.trim();
  const match = PLAIN_DECIMAL.exec(trimmed);
  if (!match) return value;

  const [, sign, whole, fraction = ""] = match;

  // Group from the right, three digits at a time, on the string itself.
  let grouped = "";
  for (let i = 0; i < whole.length; i++) {
    if (i > 0 && (whole.length - i) % 3 === 0) grouped += ",";
    grouped += whole[i];
  }

  return `${sign}${grouped}${fraction}`;
}

/** An amount with its currency, for a read-only display. */
export function formatMoney(
  value: string | null | undefined,
  currency?: string | null,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = formatAmount(value);
  return currency ? `${amount} ${currency}` : amount;
}
