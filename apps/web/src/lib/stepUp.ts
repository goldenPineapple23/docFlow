/**
 * The Console's step-up (DECISIONS.md D-151, D-177). A destructive action --
 * hard delete, clear quarantine, cancel, intake-address rotation, buyer merge,
 * go live, tier change -- is refused by the API with AUTH-007 when the
 * authenticator code on the session is more than five minutes old.
 *
 * Rather than every button handling that, `apiFetch` asks the registered
 * handler (the Console layout's code dialog) for a fresh code, then sends the
 * same request once more. Outside the Console no handler is registered, and a
 * refusal comes back to the caller unchanged.
 */

import type { CatalogError } from "./review";

export const STEP_UP_CODE = "AUTH-007";

/** Shown the API's own AUTH-007 entry; resolves true once a fresh code is in. */
type StepUpHandler = (refusal: CatalogError) => Promise<boolean>;

let handler: StepUpHandler | null = null;

/** Register the dialog that collects a fresh code; returns an unregister function. */
export function registerStepUpHandler(fn: StepUpHandler): () => void {
  handler = fn;
  return () => {
    if (handler === fn) handler = null;
  };
}

/** The API's AUTH-007 entry when this response asks for a fresh code, else null. */
export async function stepUpRefusal(response: Response): Promise<CatalogError | null> {
  if (response.status !== 403) return null;
  try {
    const body = await response.clone().json();
    return body?.detail?.code === STEP_UP_CODE ? (body.detail as CatalogError) : null;
  } catch {
    return null;
  }
}

/**
 * If `response` asks for a step-up and a handler is registered, collect a code
 * and return the retried response; otherwise return `response` untouched.
 */
export async function withStepUp(
  response: Response,
  retry: () => Promise<Response>,
): Promise<Response> {
  if (handler === null) return response;
  const refusal = await stepUpRefusal(response);
  if (refusal === null) return response;
  const confirmed = await handler(refusal);
  return confirmed ? retry() : response;
}
