import { readFileSync } from "node:fs";
import path from "node:path";
import type { NextConfig } from "next";

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
 * **Only `NEXT_PUBLIC_`-prefixed keys are read.** That prefix is Next's own
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
    if (!key.startsWith("NEXT_PUBLIC_")) continue;
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

const nextConfig: NextConfig = {
  // A real environment variable already set in the shell wins, so CI and a
  // deployment can override without editing a file.
  env: { ...publicEnvFromRepoRoot(), ...pickPublic(process.env) },

  // Dev only. Next blocks requests for its own dev resources (HMR, the
  // client bundle) when the browser's origin is not the one the server
  // thinks it is -- so opening http://127.0.0.1:3000 while the server calls
  // itself localhost silently breaks hydration: the page renders, and
  // nothing is interactive. The failure gives no visible clue, so the hosts
  // that mean "this machine" are listed rather than left to chance.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
};

function pickPublic(source: NodeJS.ProcessEnv): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, value] of Object.entries(source)) {
    if (key.startsWith("NEXT_PUBLIC_") && typeof value === "string") {
      values[key] = value;
    }
  }
  return values;
}

export default nextConfig;
