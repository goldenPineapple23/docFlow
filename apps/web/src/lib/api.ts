import { withStepUp } from "./stepUp";
import { supabase } from "./supabase";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Every call to the FastAPI backend goes through here so the Supabase
 * session token is attached consistently. The backend derives tenant_id
 * and platform-admin status from this token server-side -- nothing about
 * tenant scope or admin capability is ever sent from the client
 * (CLAUDE.md Section 7.5 / Section 10).
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = async (): Promise<Response> => {
    // Read the session on every send: after a step-up the retry must carry
    // the new token, which has the fresh authenticator code on it.
    const {
      data: { session },
    } = await supabase.auth.getSession();

    const headers = new Headers(init.headers);
    if (session?.access_token) {
      headers.set("Authorization", `Bearer ${session.access_token}`);
    }
    // FormData sets its own multipart type, with the boundary; overriding it
    // with JSON would make every file upload unreadable to the API.
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  };

  // A destructive Console action refused for want of a fresh authenticator
  // code is retried once after the code is entered (D-177, lib/stepUp.ts).
  return withStepUp(await send(), send);
}
