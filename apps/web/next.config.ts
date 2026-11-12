import { readFileSync } from "node:fs";
import path from "node:path";
import type { NextConfig } from "next";
import { ADMIN_HEADERS, SECURITY_HEADERS } from "./src/lib/csp";
import { PUBLIC_ENV_NAMES, deployedBuildProblems } from "./src/lib/origins";

/**
 * Read the repo-root `.env`, and expose only its `NEXT_PUBLIC_*` values.
 *
 * DocFlow keeps **one** `.env`, at the repo root: `docflow_core.config`
 * resolves it by path rather than by cwd, precisely so the API, the worker
 * and any script find the same file whichever directory they are launched
 * from. Next does not work that way — it only reads `.env` files inside its
 * own project directory — so `apps/web` saw none of it, every
 * `process.env.NEXT_PUBLIC_*` was `undefined` in the browser bundle, and
 * `src/lib/supabase.ts` fell back to its placeholder URL. Sign-in then
 * failed for every account, because the browser was talking to a Supabase
 * project that does not exist. See DECISIONS.md D-089.
 *
 * The alternative was a second `.env` inside `apps/web`, which would mean
 * two files to keep in step and one more place for a stale value to hide.
 *
 * **Only the three names in `src/lib/origins.ts` are read** (D-237): the API's
 * URL, Supabase's URL and its anon key. Any other `NEXT_PUBLIC_` key is left
 * out, so a value can't reach the browser by being given the prefix, and
 * `npm run check:bundle` checks the built bundle for it.
 *
 * Within that, `NEXT_PUBLIC_` is Next's own
 * marker for "this is compiled into the browser bundle", so everything
 * selected here is public by definition — the Supabase anon key is designed
 * to ship to browsers. Nothing else in the root `.env` is touched, so a
 * service-role key or a database URL cannot reach the client through this,
 * even by accident.
 */
function publicEnvFromRepoRoot(): Record<string, string> {
  const envPath = path.resolve(__dirname, "../../.env");
  let raw: string;
  try {
    raw = readFileSync(envPath, "utf8");
  } catch {
    // No root .env (CI, a fresh clone before SETUP.md Step 1). The app still
    // builds; `supabase.ts` falls back to its placeholder, and any real
    // request against it fails at runtime, which is correct — there is no
    // backend to reach until it is configured.
    return {};
  }

  const values: Record<string, string> = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    if (!isPublic(key)) continue;
    let value = trimmed.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

const publicEnv = { ...publicEnvFromRepoRoot(), ...pickPublic(process.env) };

// A Vercel build without its real API and Supabase URLs would ship the
// localhost fallbacks, and a CSP naming them. Stop it here (D-237).
const buildProblems = deployedBuildProblems({ ...publicEnv, VERCEL_ENV: process.env.VERCEL_ENV });
if (buildProblems.length > 0) {
  throw new Error(`This Vercel build can't go out: ${buildProblems.join("; ")}. See RUNBOOK 13.`);
}

const nextConfig: NextConfig = {
  // Where the build goes. `next dev` and `next build` both write to `.next`
  // by default, so a production build run while a dev server is up corrupts
  // it under the dev server's feet -- the page then dies with "Jest worker
  // encountered N child process exceptions", which names nothing that
  // happened. The live end-to-end suite (D-166) builds and serves its own
  // copy, so it sets this and leaves a running `npm run dev` alone.
  distDir: process.env.NEXT_DIST_DIR || ".next",

  // A real environment variable already set in the shell wins, so CI and a
  // deployment can override without editing a file.
  env: publicEnv,

  // The security headers on every response, built files included (D-237).
  // The CSP isn't here: it carries a per-request nonce, so src/proxy.ts sets
  // it on each page.
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      { source: "/admin", headers: ADMIN_HEADERS },
      { source: "/admin/:path*", headers: ADMIN_HEADERS },
    ];
  },

  // Dev only. Next blocks requests for its own dev resources (HMR, the
  // client bundle) when the browser's origin is not the one the server
  // thinks it is -- so opening http://127.0.0.1:3000 while the server calls
  // itself localhost silently breaks hydration: the page renders, and
  // nothing is interactive. The failure gives no visible clue, so the hosts
  // that mean "this machine" are listed rather than left to chance.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
};

function isPublic(key: string): boolean {
  return (PUBLIC_ENV_NAMES as readonly string[]).includes(key);
}

function pickPublic(source: NodeJS.ProcessEnv): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, value] of Object.entries(source)) {
    if (isPublic(key) && typeof value === "string") {
      values[key] = value;
    }
  }
  return values;
}

export default nextConfig;
