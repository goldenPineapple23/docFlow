import { supabase } from "./supabase";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Every call to the FastAPI backend goes through here so the Supabase
 * session token is attached consistently. The backend derives tenant_id
 * and platform-admin status from this token server-side -- nothing about
 * tenant scope or admin capability is ever sent from the client
 * (CLAUDE.md Section 7.5 / Section 10).
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const headers = new Headers(init.headers);
  if (session?.access_token) {
    headers.set("Authorization", `Bearer ${session.access_token}`);
  }
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}
