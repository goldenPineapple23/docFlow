import type { CatalogError } from "./review";

/**
 * Catalog entries for failures that happen in the browser, where no API
 * response carries the entry (Section 7.16.5; DECISIONS.md D-177).
 *
 * Each one is a copy of the entry in packages/core/docflow_core/errors.py,
 * word for word. packages/core/tests/test_errors.py fails if the two ever
 * differ, so a wording change is made in the catalog and copied here.
 */

/** A wrong or expired authenticator code: Supabase refused it in the browser. */
export const AUTH_008: CatalogError = {
  code: "AUTH-008",
  title: "That code didn't match",
  message:
    "The code wasn't accepted. Authenticator codes change every 30 seconds, so it may have expired while you typed it. Nothing was changed.",
  action: "Enter the code your authenticator app is showing now.",
};
