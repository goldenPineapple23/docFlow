/**
 * The web app's Content-Security-Policy (CLAUDE.md 7.12; D-235 item 2;
 * D-237). Built per request by src/proxy.ts with a fresh nonce, which Next
 * puts on its own scripts and inline styles as it renders.
 *
 * Every source and why (docs/designs/public-staging.md, 3.2):
 * - script-src: our own files, and only scripts carrying this request's
 *   nonce; 'strict-dynamic' lets the chunks those load run too.
 * - style-src: our compiled CSS file and Next's nonce'd inline styles.
 * - img-src: page images come from the API (it reads Storage itself; the
 *   browser never talks to Storage); data: is the authenticator QR code.
 * - connect-src: the API, Supabase Auth, and Next's own fetches.
 * - frame-src: the original's sandboxed frame, served by the API.
 *
 * No 'unsafe-inline' and no 'unsafe-eval' in production. Development adds
 * 'unsafe-eval' (React's dev build uses eval for its error overlay),
 * 'unsafe-inline' styles (the dev server injects styles without a nonce)
 * and ws: for hot reload.
 */

export type CspInput = {
  nonce: string;
  apiOrigin: string;
  supabaseOrigin: string;
  development: boolean;
};

export function buildCsp({ nonce, apiOrigin, supabaseOrigin, development }: CspInput): string {
  const directives: [string, string[]][] = [
    ["default-src", ["'self'"]],
    [
      "script-src",
      ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'", ...(development ? ["'unsafe-eval'"] : [])],
    ],
    // With a nonce present a browser ignores 'unsafe-inline', so development
    // drops the nonce here rather than adding to it.
    ["style-src", development ? ["'self'", "'unsafe-inline'"] : ["'self'", `'nonce-${nonce}'`]],
    ["img-src", ["'self'", "data:", apiOrigin]],
    ["font-src", ["'self'"]],
    ["connect-src", ["'self'", apiOrigin, supabaseOrigin, ...(development ? ["ws:"] : [])]],
    ["frame-src", [apiOrigin]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
    ["frame-ancestors", ["'none'"]],
  ];
  return directives.map(([name, sources]) => `${name} ${[...new Set(sources)].join(" ")}`).join("; ");
}

/** 128 random bits, base64: a nonce nobody can guess. */
export function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/**
 * The headers every response carries, CSP aside (it needs the request's
 * nonce, so src/proxy.ts sets it). next.config.ts serves these on every path,
 * static files included.
 */
export const SECURITY_HEADERS: { key: string; value: string }[] = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), hid=()",
  },
];

/** The Console's pages are never indexed (founder, 2026-11-12; D-237). */
export const ADMIN_HEADERS: { key: string; value: string }[] = [
  { key: "X-Robots-Tag", value: "noindex, nofollow" },
];
