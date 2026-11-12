import { NextResponse, type NextRequest } from "next/server";
import { buildCsp, newNonce } from "./lib/csp";
import { API_BASE_URL, SUPABASE_URL, originOf } from "./lib/origins";

/**
 * Next 16's Proxy (what used to be middleware): every page gets a fresh
 * nonce and the CSP built around it (D-237). Next reads the nonce back from
 * the request's CSP header while it renders and puts it on its own scripts
 * and inline styles, so no 'unsafe-inline' script is ever allowed. That
 * needs every page rendered per request; the root layout makes sure of it.
 */
export function proxy(request: NextRequest) {
  const nonce = newNonce();
  const csp = buildCsp({
    nonce,
    apiOrigin: originOf(API_BASE_URL),
    supabaseOrigin: originOf(SUPABASE_URL),
    development: process.env.NODE_ENV === "development",
  });

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      // Pages, not built files: a stylesheet or script file needs no CSP of
      // its own. next.config.ts's other headers still cover those.
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
