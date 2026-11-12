#!/usr/bin/env node
/**
 * The browser bundle carries no secret and no tenant data (founder,
 * 2026-11-12; D-237). Run after `npm run build`: `npm run check:bundle`.
 *
 * Everything under `<distDir>/static` is what any visitor can download,
 * signed in or not. It fails when it finds:
 *  - a JWT whose role isn't `anon` (a Supabase service-role key);
 *  - a known secret shape: Stripe secret, restricted or webhook keys, an
 *    Anthropic key, a Supabase secret key, a database or Redis URL with a
 *    password, a private key block, a Healthchecks.io ping URL;
 *  - the value of any setting that isn't one of the three public ones,
 *    from the repo-root `.env` (when there is one) and from this process's
 *    environment, wherever the name looks like a secret;
 *  - "Acme Test", the prefix every test tenant on staging is named with
 *    (CLAUDE.md Section 0 rule 4): tenant data at build would mean a page
 *    fetched data while it was being built, which no page here should.
 *
 * It prints the three public names and whether each one's value is in the
 * bundle -- never the values.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(WEB, "..", "..");
const DIST = path.join(WEB, process.env.NEXT_DIST_DIR || ".next", "static");

const PUBLIC_NAMES = ["NEXT_PUBLIC_API_BASE_URL", "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY"];

const SECRET_SHAPES = [
  ["a Stripe secret key", /\bsk_(live|test)_[A-Za-z0-9]{8,}/],
  ["a Stripe restricted key", /\brk_(live|test)_[A-Za-z0-9]{8,}/],
  ["a Stripe webhook secret", /\bwhsec_[A-Za-z0-9]{8,}/],
  ["an Anthropic key", /\bsk-ant-[A-Za-z0-9_-]{8,}/],
  ["a Supabase secret key", /\bsb_secret_[A-Za-z0-9_-]{8,}/],
  ["a database or Redis URL with a password", /\b(postgres(ql)?|rediss?):\/\/[^\s"'`:@/]+:[^\s"'`@/]+@/],
  ["a private key", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ["a Healthchecks.io ping URL", /hc-ping\.com\/[0-9a-f-]{8,}/],
];

const SECRET_NAME = /(SECRET|PASSWORD|TOKEN|PRIVATE|_KEY|DATABASE_URL|REDIS_URL|DSN|HEARTBEAT_URL|WEBHOOK)/;

function files(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) out.push(...files(full));
    else if (/\.(js|css|html|json|txt|map)$/.test(name)) out.push(full);
  }
  return out;
}

function dotenv(file) {
  const values = {};
  let raw;
  try {
    raw = readFileSync(file, "utf8");
  } catch {
    return values;
  }
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const at = trimmed.indexOf("=");
    let value = trimmed.slice(at + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[trimmed.slice(0, at).trim()] = value;
  }
  return values;
}

function jwtRole(token) {
  try {
    const payload = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8"));
    return typeof payload === "object" && payload ? payload.role : undefined;
  } catch {
    return undefined;
  }
}

let built;
try {
  built = files(DIST);
} catch {
  console.error(`check:bundle: no build at ${path.relative(WEB, DIST)} -- run npm run build first`);
  process.exit(2);
}
const bundle = built.map((f) => [path.relative(WEB, f), readFileSync(f, "utf8")]);

const rootEnv = dotenv(path.join(REPO, ".env"));
const publicValues = {};
for (const name of PUBLIC_NAMES) publicValues[name] = process.env[name] || rootEnv[name] || "";

// Every non-public value long enough to be a secret, from .env and from the
// environment where the name says secret.
const privateValues = new Map();
for (const [name, value] of Object.entries(rootEnv)) {
  if (!PUBLIC_NAMES.includes(name) && value.length >= 12) privateValues.set(value, `.env ${name}`);
}
for (const [name, value] of Object.entries(process.env)) {
  if (!PUBLIC_NAMES.includes(name) && SECRET_NAME.test(name) && value && value.length >= 12) {
    privateValues.set(value, `environment ${name}`);
  }
}
// A private setting that happens to equal a public one (the anon key under
// both names, say) is public by definition.
for (const value of Object.values(publicValues)) privateValues.delete(value);

const findings = [];
for (const [file, text] of bundle) {
  for (const [what, shape] of SECRET_SHAPES) {
    if (shape.test(text)) findings.push(`${file}: ${what}`);
  }
  for (const token of text.match(/eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g) ?? []) {
    const role = jwtRole(token);
    if (role !== "anon") findings.push(`${file}: a JWT with role ${JSON.stringify(role)}, not anon`);
  }
  for (const [value, where] of privateValues) {
    if (text.includes(value)) findings.push(`${file}: the value of ${where}`);
  }
  if (text.includes("Acme Test")) findings.push(`${file}: "Acme Test" (a test tenant's name)`);
}

console.log(`check:bundle: ${bundle.length} files under ${path.relative(WEB, DIST)}`);
for (const name of PUBLIC_NAMES) {
  const value = publicValues[name];
  const present = value ? bundle.some(([, text]) => text.includes(value)) : false;
  console.log(`  ${name}: ${value ? (present ? "in the bundle" : "set, not in the bundle") : "unset (fallback used)"}`);
}
console.log(`  private values checked: ${privateValues.size}`);

if (findings.length > 0) {
  console.error("check:bundle: FAILED -- the browser bundle carries what it must not:");
  for (const finding of [...new Set(findings)]) console.error(`  ${finding}`);
  process.exit(1);
}
console.log("check:bundle: passed -- no secret, no private setting and no tenant data in the bundle");
