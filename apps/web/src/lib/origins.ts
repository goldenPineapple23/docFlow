/**
 * Where the browser app calls out to: the API and Supabase Auth. One place,
 * read by the code that calls them and by the Content-Security-Policy that
 * allows them (src/lib/csp.ts), so the two can't drift (D-237).
 *
 * Each value is compiled into the bundle at build (next.config.ts), so a
 * deployment's CSP names exactly the hosts that build calls. The fallbacks
 * are the ones this app has always used: the local API, and a placeholder
 * Supabase host that answers nothing.
 *
 * These three are the only `NEXT_PUBLIC_` values the browser ever gets;
 * next.config.ts exposes no other, and `npm run check:bundle` fails a build
 * that carries anything else.
 */

export const PUBLIC_ENV_NAMES = [
  "NEXT_PUBLIC_API_BASE_URL",
  "NEXT_PUBLIC_SUPABASE_URL",
  "NEXT_PUBLIC_SUPABASE_ANON_KEY",
] as const;

// Written out in full: Next only inlines a literal `process.env.NEXT_PUBLIC_X`.
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || "https://placeholder.supabase.co";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "placeholder-anon-key";

/** `https://host[:port]` of a URL: no path, no trailing slash. */
export function originOf(url: string): string {
  return new URL(url).origin;
}

/**
 * Why a Vercel build must not go out, or nothing (D-237). A deployed web app
 * built without its API or Supabase URL would ship the localhost fallbacks
 * and a CSP naming them, so `next build` stops instead.
 */
export function deployedBuildProblems(env: Record<string, string | undefined>): string[] {
  if (!env.VERCEL_ENV) return [];
  const problems: string[] = [];
  for (const name of ["NEXT_PUBLIC_API_BASE_URL", "NEXT_PUBLIC_SUPABASE_URL"] as const) {
    const value = env[name];
    if (!value) {
      problems.push(`${name} is not set`);
      continue;
    }
    let url: URL;
    try {
      url = new URL(value);
    } catch {
      problems.push(`${name} is not a URL`);
      continue;
    }
    if (url.protocol !== "https:") problems.push(`${name} is not https`);
    if (url.pathname !== "/" || url.search || url.hash) problems.push(`${name} has a path`);
  }
  if (!env.NEXT_PUBLIC_SUPABASE_ANON_KEY) problems.push("NEXT_PUBLIC_SUPABASE_ANON_KEY is not set");
  return problems;
}
