# Public staging's code: API docs off, CORS, the web app's headers, the Vercel project, Supabase Auth's redirect URLs

Status: **design, awaiting the founder's answers.** Nothing is built.

- **Plan:** task 5 in D-235 item 18, sized there at 1.5 working days.
- **Base:** `main` at `7d6bb45`, which is #98's merge. **Branch:** `phase55/stage5-public-staging`.
- **Record:** D-237, the next free number. D-236 is the last entry in `DECISIONS.md`, and no file names D-237.
- **Scope:** code and records only. There are no deploys, no staging writes, no Fly action, and no change in the Vercel or Supabase dashboards. Those happen at task 7, under their own Grants and founder steps, in the order set out in section 10.

Estimates are in working days and include tests and records, as D-235 item 18 counts them.

## 0. What the code does today (read 2026-11-12)

- **The API.**
  - `FastAPI(title="DocFlow API", ...)` in `apps/api/app/main.py:129` keeps FastAPI's defaults, so `/docs`, `/redoc`, `/docs/oauth2-redirect` and `/openapi.json` all answer.
  - CORS already reads `CORS_ALLOWED_ORIGINS` (`main.py:179-196`), with `allow_credentials=False`.
  - The original document's frame takes its `frame-ancestors` from the same setting (`review.py:98-114`).
  - The page-image, original-content and export-download routes set their own headers.
  - No other API response sets a security header.
- **The web app.** It sets no security headers: `apps/web/next.config.ts` has no `headers()`, and there is no `proxy.ts`.
  - The browser calls the API at `NEXT_PUBLIC_API_BASE_URL`. The fallback is `http://localhost:8000`, in `src/lib/api.ts:4` and in `viewerParts.tsx:17`.
  - It calls Supabase Auth at `NEXT_PUBLIC_SUPABASE_URL` (`src/lib/supabase.ts:12`).
  - It loads nothing from any third party. Fonts are `next/font` (Geist), self-hosted at build. There is no Stripe.js: billing is `window.location.assign` to a Stripe page. There is no analytics.
- **Page images don't come from Supabase Storage.** The brief says the CSP must allow "page images from Supabase Storage", but the browser never contacts Storage.
  - `PageViewer.tsx` sets `<img src={API_BASE_URL + page.url}>`. That is `GET /review/documents/{id}/pages/{i}?token=…` on the API.
  - The API reads the bytes from Storage server-side, over the S3 endpoint, and serves them (`review.py:1105-1145`).
  - The original works the same way: `<iframe sandbox="" src={API_BASE_URL + url}>`.
  - So `*.storage.supabase.co` is **not** in the CSP, and adding it would only widen the policy. If the founder knows of a Storage URL that reaches the browser, I'll look again.
- **The API on Fly.** `apps/api/fly.toml` describes a private app: its comment block says "Private only", its deploy line has `--no-public-ips`, and it sets `force_https = false`.

## 1. API docs endpoints off

**What changes**
- A new setting, `API_DOCS_ENABLED: bool = False`, in `docflow_core.config`, documented in `.env.example`.
- `main.py` passes `docs_url`, `redoc_url` and `openapi_url` as `None` unless the setting is true. With `openapi_url=None`, FastAPI also drops `/docs/oauth2-redirect`.
- `app.openapi()`, the method, still builds the schema. `test_cors.py` and `test_roles.py` read routes through it, and they keep working. The build checks this by running both.
- Local development turns the docs on with `API_DOCS_ENABLED=true` in `.env`. Staging and production never set it.
- **Question 1:** should the API also ignore the setting when `FLY_APP_NAME` is set, logging one line at startup, so a stray secret on Fly can't turn the docs back on?

**Tests**
- `test_api_docs.py` runs the API in a subprocess with `DOCFLOW_ENV=staging` set explicitly and `API_DOCS_ENABLED` unset.
  - `/docs`, `/redoc`, `/openapi.json` and `/docs/oauth2-redirect` each answer 404.
  - A subprocess is needed because `app` is built when the module is imported, so the settings must be in place first.
- The same subprocess with `API_DOCS_ENABLED=true` gets 200 on all three pages. Without it, the off test could pass for the wrong reason.
- If question 1 is yes: with `FLY_APP_NAME` set, all four stay 404 and the log line appears.

**Estimate:** 0.15.

## 2. CORS for the web app's origin

**What stays as it is**
- **The allowlist** is read from `CORS_ALLOWED_ORIGINS`, comma-separated, trimmed, with no trailing slash. Nothing is hardcoded.
  - The setting's default lists four loopback origins for local development and the live e2e.
  - At task 7 it becomes `https://<vercel-host>` on the API's Fly app. The value isn't known until the Vercel project exists.
- **No wildcard.** Neither `allow_origin_regex` nor `"*"` is used.
- **Credentials.** `allow_credentials=False`. DocFlow authenticates with `Authorization: Bearer <Supabase access token>`, which the app sets on every call itself (`apiFetch`). The browser never sends it on its own, so a hostile page can't ride a session, and no cookie is involved.
- **Preflight.** Starlette's `CORSMiddleware` answers it.
  - An `OPTIONS` from a listed origin gets 200, with `Access-Control-Allow-Origin` set to that origin, the methods (GET, POST, PUT, PATCH, OPTIONS), the headers (`Authorization`, `Content-Type`) and `Access-Control-Max-Age: 600`.
  - A preflight from any other origin gets 400 with no allow headers, so the browser stops before the real request.
  - `test_cors.py` already fails the build if a route uses a method that isn't listed.
- **The viewer frame.** The original's `frame-ancestors` comes from the same setting, so the Vercel origin is allowed to frame originals with no second setting.

**What changes**
- **Question 2, recommended:** a startup refusal on Fly (`FLY_APP_NAME` set) when `CORS_ALLOWED_ORIGINS` is wrong in any of these ways:
  - it is unset, which today falls back quietly to localhost (D-223 item 7);
  - it is loopback only;
  - it contains `*`;
  - it contains a value with a path, or one that isn't `https://`.

  The API would refuse to start the same way the Q12 parse-token refusal does.
- **Question 3:** Vercel preview deployments get their own `*.vercel.app` hosts, which no exact allowlist can cover. I recommend that previews are not used against staging's API. The project deploys only from `main` (section 5), so previews never call it.

**Tests**
- The existing tests are kept: an allowed origin's preflight is answered, an unknown origin is refused, and every method is listed.
- New: a preflight from `https://<vercel-host>`, set through the setting, is answered; the same preflight from `https://evil.example` gets no allow header.
- New: `Access-Control-Allow-Credentials` is never sent.
- If question 2 is yes, the refusal is tested in a subprocess with `FLY_APP_NAME` set, for each bad value.

**Estimate:** 0.1, or 0.15 with the refusal.

## 3. The web app's CSP and security headers

### 3.1 The headers

The CSP is set on every HTML response by `apps/web/src/proxy.ts`. In Next 16, Proxy is the file that used to be called middleware (the guide in `node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md`). The other headers go on every response, including `/_next/static`, through `next.config.ts`'s `headers()`.

| Header | Value | Why |
|---|---|---|
| `Content-Security-Policy` | see 3.2 | Section 7.12 |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` | two years. No `preload`, because that is a commitment for the eventual custom domain, not for a `vercel.app` host. Browsers ignore HSTS on plain `http`, so the local e2e on `http://127.0.0.1:3100` is unaffected (checked in the e2e) |
| `X-Content-Type-Options` | `nosniff` | |
| `Referrer-Policy` | `no-referrer` | URLs carry document ids. Nothing in the app needs a referrer, and the page `<img>` and the frame already set `referrerPolicy="no-referrer"` themselves |
| `X-Frame-Options` | `DENY` | for old browsers. CSP's `frame-ancestors 'none'` is the rule modern ones follow |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), hid=()` | the app uses none of these. A file `<input>` on a phone still offers "Take photo": that is the OS picker, not `getUserMedia`. Checked on a real iPhone at task 9 |

### 3.2 The CSP, every source and why

Production:

```
default-src 'self';
script-src 'self' 'nonce-<per request>' 'strict-dynamic';
style-src 'self' 'nonce-<per request>';
img-src 'self' data: <API origin>;
font-src 'self';
connect-src 'self' <API origin> <Supabase origin>;
frame-src <API origin>;
object-src 'none';
base-uri 'self';
form-action 'self';
frame-ancestors 'none'
```

| Directive | Sources | Why each one |
|---|---|---|
| `default-src` | `'self'` | everything not named below: the web app's own origin only |
| `script-src` | `'self'`, a nonce, `'strict-dynamic'` | Next's framework scripts and the inline RSC payload carry the nonce, which Next applies by itself during server rendering. `'strict-dynamic'` lets the chunks those scripts load run too |
| `style-src` | `'self'`, a nonce | Tailwind's compiled CSS file; Next's own inline `<style>` tags carry the nonce. See 3.3 on style attributes |
| `img-src` | `'self'`, `data:`, the API origin | the API origin serves the page images. `data:` is for the authenticator QR code, Supabase's SVG shown as `<img src="data:…">` in `AuthenticatorSetup.tsx`. `'self'` is for the favicon |
| `font-src` | `'self'` | Geist is served from `/_next/static/media` |
| `connect-src` | `'self'`, the API origin, the Supabase origin | the API is every `apiFetch`. Supabase is Auth's REST calls: sign-in, session refresh, MFA. `'self'` is Next's own client-side fetches on navigation. No `wss:`, because the app opens no Realtime channel |
| `frame-src` | the API origin | the original's `<iframe sandbox="">` |
| `object-src` | `'none'` | no plugins |
| `base-uri` | `'self'` | no `<base>` hijack |
| `form-action` | `'self'` | every form submits through JavaScript. Billing leaves for Stripe with `location.assign`, which `form-action` doesn't govern |
| `frame-ancestors` | `'none'` | nothing frames the web app |

- **Not used:**
  - `*.storage.supabase.co` (section 0);
  - `blob:`, since no object URL is made;
  - `upgrade-insecure-requests`. Every source above is named with its own scheme, so it adds nothing in production, and it would break the e2e, whose API is `http://localhost:8000`.
- **Where the origins come from.** One new module, `src/lib/origins.ts`, holds `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_SUPABASE_URL`, each reduced to its origin with `new URL(x).origin`, with the same fallbacks `api.ts` and `supabase.ts` use today.
  - `api.ts`, `viewerParts.tsx`, `supabase.ts` and the CSP builder all read from it, so the policy can't drift from what the app actually calls.
  - The values are compiled in at build, so the CSP names exactly the hosts this build calls.
- **A build that would ship localhost.** When `VERCEL_ENV` is set, `next build` fails if either origin is unset or isn't `https://`.

### 3.3 `'unsafe-inline'` and `'unsafe-eval'`

- **`'unsafe-eval'`: not in production.** It is added only when `NODE_ENV=development`, because React's dev build uses `eval` for its error overlays (the Next guide above). The e2e and Vercel both run production builds.
- **`'unsafe-inline'` for scripts: not needed.** I recommend nonces (question 4).
  - The cost: every page renders per request, because a nonce can't be baked into a static page. The root layout reads `headers()` so that no page is left static by mistake.
  - Every screen in DocFlow is a signed-in client page that fetches its data after load, so losing static HTML and CDN caching costs little. Vercel Pro covers the function time at demo volume.
  - The alternative is a static header in `next.config.ts` with `script-src 'self' 'unsafe-inline'`. That is weaker against exactly the injected-script case Section 7.12 is about.
- **`'unsafe-inline'` for styles: not expected.**
  - A nonce can't cover a `style="…"` attribute in server-rendered HTML. There are two `style={{…}}` props in `src/`, both in `PageViewer.tsx`, on the page `<img>` and its scroll box. Both render only after the page's data has loaded, in the browser, so React sets them through the CSSOM, which CSP doesn't restrict.
  - Next's own `<style>` tags carry the nonce.
  - The proof is the e2e in 3.4, not this reading.
  - If it finds a violation, the narrowest fallback is `style-src-attr 'unsafe-inline'` (attributes only, never `<style>` tags), and I'd come back to the founder before using it.

### 3.4 Tests

- **Vitest, `csp.test.ts`:**
  - the exact production policy for given origins;
  - no `'unsafe-inline'`, `'unsafe-eval'` or `*` in production;
  - `'unsafe-eval'` present only in development;
  - a fresh nonce on each call;
  - a value with a path is reduced to its origin.
- **Playwright, stubbed (`apps/web/e2e`, the `web` CI job):**
  - **A CSP-violation guard on every spec.** A shared fixture adds an init script that records each `securitypolicyviolation` event, and fails the test if any were recorded.
    - `page.route` stubs still pass through the CSP: a request the policy blocks never reaches the route.
    - So the existing specs, which load page images, the frame, the stored text and the Console, all become checks of `img-src`, `frame-src` and `connect-src`, as well as `script-src` and `style-src`.
    - One assertion is added to `console-mfa.spec.ts`: the enrolment QR (`mfa-qr`, a `data:` image) is visible, with the guard on.
    - The guard is proved once by a test page that breaks the policy on purpose and is expected to fail.
  - **`headers.spec.ts`:**
    - `/`, `/login`, `/review/<id>`, `/admin` and one `/_next/static` asset carry each header in 3.1;
    - the CSP differs between two loads, so the nonce is fresh;
    - every `<script>` on the page carries the nonce.
  - **WebKit.** Today the `webkit-375` project runs only `review-375.spec.ts`, and that spec loads neither a page image nor the frame. So a new `webkit` project (Desktop Safari, 1280) runs `headers.spec.ts` and `review.spec.ts`'s viewer tests: the page image, its zoom, the frame fallback and the stored text, all with the guard on.
    - `review-375.spec.ts` keeps running in WebKit at 375, now with the guard.
    - `review-375-touch.spec.ts` stays Chromium only, because it needs CDP.
- **Playwright, live (`apps/web/e2e-live`, the `web-live` job, Chromium):** `viewer-cross-site.spec.ts` already loads a real page image and the real frame from the real API across sites (`127.0.0.1` against `localhost`). It now runs with the headers on and with the guard, which proves the CSP against the real API, not a stub.
  - **Question 5:** add WebKit to the live job too? That is about 40 s more for the browser install (D-235 item 15's measure) and roughly 0.1 day. My recommendation is no: the stubbed WebKit run and task 9's real-Safari pass cover it.

**Estimate:** 0.75.

### 3.5 Does the API need these headers too?

Partly. The API serves JSON and files, never a page. The viewer, page and download routes already set their own headers, and they stay as they are.

**What changes**
- A small middleware in `main.py` adds to every response that hasn't set the header itself:
  - `X-Content-Type-Options: nosniff`;
  - `Referrer-Policy: no-referrer`;
  - `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, so a JSON body opened in a tab runs nothing and can't be framed;
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains`. Browsers ignore it over plain `http`, so local runs are unaffected.
- **Not added:**
  - `X-Frame-Options`, because `DENY` would fight the original's frame, which must be framable by the web origin. CSP's `frame-ancestors` already covers framing per route;
  - `Permissions-Policy`, which means nothing for JSON.

**Tests**
- `/healthz`, a 401, an admin 404, a CORS preflight and a page image each carry the four headers.
- The original's and the page's own CSP are unchanged, byte for byte: the existing tests are kept, and one new test says so.

**Estimate:** 0.15.

## 4. The API's Fly config

**What changes in this PR (`apps/api/fly.toml`, comments and one setting)**
- `force_https = true`. A public API answers `http` with a redirect to `https`.
- The comment block is rewritten for a public staging API:
  - the deploy line drops `--no-public-ips`;
  - "Private only" is replaced by what the public address is for (D-235 item 2, replacing D-194 item 4).
- `auto_stop_machines = "stop"` with `min_machines_running = 0` is kept. A cold start took 0.95 s to the machine and about 5 s to `Application startup complete` (D-227 item 3). Postmark and Stripe both retry. Section 10 has the demo-day step.
- **Applied at task 7, not here:** the deploy itself, and `fly ips allocate-v4 --shared` plus `fly ips allocate-v6`, each under a Grant.

**Tests:** none run against Fly in this task, and no test reads the API's `fly.toml` today; only the worker's is read, by `test_ci_guards.py`. A new core test in `test_ci_guards.py` reads it and checks `force_https = true` and that the deploy line has no `--no-public-ips`. The real proof is task 7's `http://` request getting a redirect.

**Estimate:** 0.05.

## 5. The Vercel project

**What changes in this PR: `apps/web/vercel.json`.**

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "nextjs",
  "installCommand": "npm ci",
  "buildCommand": "npm run build",
  "regions": ["iad1"]
}
```

- `iad1` puts the page functions next to the API's `iad` region.
- Headers are not set in `vercel.json`. They live in the app (section 3), so the e2e tests exactly what Vercel serves.

**Question 6: how deploys start.** D-235 item 3 wants no deploy in the 48 hours before a demo, and D-213 puts every deploy under a Grant. A Git-connected project would deploy on every push to `main`. The options:
- **(a), recommended:** Git connected, with automatic deploys off (`"git": {"deploymentEnabled": false}` in `vercel.json`). The founder deploys a named commit from the dashboard or with a deploy hook, under a Grant.
- **(b):** no Git connection. The founder deploys with the Vercel CLI from a clean checkout.

Before writing the steps I'll check against Vercel's current docs that (a) still allows a manual deploy of a chosen commit.

**What changes in RUNBOOK: a new section 13, "The web app on Vercel (staging)."** These are founder steps at task 7, written but not done.
1. **Create the project** on the Pro team: import `goldenPineapple23/docFlow`.
   - **Root Directory:** `apps/web`.
   - **Framework preset:** Next.js.
   - **Install command:** `npm ci`. **Build command:** `npm run build`. **Output:** the default, `.next`.
   - **Node.js:** 22.x, as CI uses.
   - **Production branch:** `main`.
   - "Include files outside the root directory" can stay on. `next.config.ts` looks for a repo-root `.env`, finds none on Vercel, and takes the variables below instead.
2. **Environment variables, Production scope only.** All three are public by design and compiled into the browser bundle, so changing one needs a redeploy. No secret goes on Vercel.

   | Variable | Value | Where it comes from |
   |---|---|---|
   | `NEXT_PUBLIC_API_BASE_URL` | `https://docflow-api-staging.fly.dev` | the API's Fly app name. The `fly.dev` host answers once task 7's public IPs exist |
   | `NEXT_PUBLIC_SUPABASE_URL` | `https://<project-ref>.supabase.co` | Supabase dashboard → docflow-staging → Project Settings → Data API (the same value as `.env`'s) |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | the anon (public) key | same page → API Keys. Public by design. Never the service-role key |

3. **Deployment Protection.** Previews are protected (Vercel Authentication). The production deployment must be public, because prospects open it.
4. **The first deploy**, under a Grant. Then read `<vercel-host>`, the production domain shown on the project page. It feeds section 10's later steps.
5. **Checks after the deploy** (read-only):
   - `curl -sI https://<vercel-host>/login` shows each header in 3.1;
   - the browser console shows no CSP violation on sign-in, the review screen with a page image, and the Console.

**Tests:** CI's `web` job already runs `npm ci` and `npm run build` in `apps/web`, which is the same install and build Vercel runs. A vitest test reads `vercel.json` and checks the root-relative commands, so a later edit can't silently point Vercel at a different build.

**Estimate:** 0.25, including RUNBOOK 13.

## 6. Supabase Auth redirect URLs

The links that reach `/auth/accept` are:
- invites and resends, from `invites.py` through `generate_invite_link`, with `redirect_to=f"{APP_BASE_URL}/auth/accept"` (D-105);
- a dashboard-sent password recovery, which lands on the Site URL. `src/app/page.tsx` sends it on to `/auth/accept`.

**What changes in RUNBOOK: a step in section 13, done at task 7 under a Grant.**

Supabase dashboard → docflow-staging → Authentication → URL Configuration:
- **Redirect URLs:** add exactly `https://<vercel-host>/auth/accept`, with no wildcard. The existing `http://localhost:3000/auth/accept` is kept for local development.
- **Site URL** (question 7): change it to `https://<vercel-host>`, so a dashboard-sent recovery reaches the public app, or leave it as it is. Local invites are unaffected either way, because they carry their own `redirect_to`, which is on the list.
- **Read only, no change:** "Allow new users to sign up" is still off (SETUP.md step 7). Once the web app is public, a project that allowed sign-ups would let anyone with the public anon key create an Auth user. That user would reach no data (401, 403 or 404 everywhere), but it shouldn't exist at all.

**Tests:** none in code. The redirect target is the existing `invites.py` value, covered by its tests. The proof is task 7's invite: a test user's link lands on `https://<vercel-host>/auth/accept`.

**Estimate:** 0.05.

## 7. A polled check of `/healthz` on staging

- **The code costs nothing.**
  - `/healthz` already answers without sign-in, always 200.
  - The keyword `"stale":false` is pinned by `test_healthz_dispatcher.py`.
  - RUNBOOK 9.4 already has the monitor's settings: a 3-minute check, a 120 s confirmation, a 30 s timeout, and the 17-minute pass mark.
- **What is open is the tool.**
  - Healthchecks.io, which watches the worker's heartbeat, only receives pings; it doesn't poll.
  - Better Stack's free plan was read on 2026-10-02 as "Free for personal projects", and a demo for prospects is commercial use.
  - So it needs Better Stack's paid plan or another tool chosen by the founder (question 8).
- **Estimate:** 0.25, if question 8 names a tool. That covers the RUNBOOK 9.4 update for staging's public API (setup steps and the test on the day) and re-reading the tool's terms on the day.
  - Choosing a tool by comparing several would take longer than 0.25.
  - So I include it **only if the founder names the tool**. Otherwise it is left out and recorded in D-237, as D-235 item 2 says.
- **Cost note:** a check every 3 minutes may keep the API machine awake, about $3.19 a month (RUNBOOK 9.4).

## 8. Admin exposure once the API is public: a report, no change

### 8.1 The API

- **Every `/admin/*` route** runs `require_platform_admin` through the router's own dependency (`admin.py:102`: `dependencies=[Depends(_console_mfa_notice)]`, which depends on `require_platform_admin`).
  - It answers **404** to everyone who isn't an active platform admin: no header, a bad or expired token, or a real tenant user (`deps.py:334-350`).
  - Seven destructive routes also need a fresh authenticator code (`require_recent_mfa`; `test_console_mfa.py` pins exactly which seven).
- **MFA on staging.** `CONSOLE_MFA_ENFORCED` is set on `docflow-api-staging` (D-220 item 11, read from the deployed API's startup log on 2026-10-27). The Console then needs an `aal2` session. Task 7 re-reads `fly secrets list` (read-only) to confirm it is still there.
- **The acting-as mounts** (`/admin/tenants/{id}/act/review/...` and the exports) carry `acting_as_gate`. `current_actor` fails closed: on that mount it 404s unless the gate put the founder's support Actor on the request (`actor.py:87-95`).
- **Every tenant route** resolves the Supabase JWT, checked against the project's JWKS. With no valid session it answers 401 (AUTH-005), and the role checks follow (`test_roles.py`).
- **Reachable without a login, the complete list:**
  - `GET /healthz`: ages and counts only, no ids;
  - `POST /intake/email/{token}`: Postmark's HTTP Basic credentials, compared in constant time, and refused when unset (H8);
  - `POST /webhooks/stripe`: Stripe's signature, idempotent on event id;
  - `GET /review/documents/{id}/original/content`, `GET /review/documents/{id}/pages/{i}` and `GET /review/exports/{id}/download`: each needs a short-lived signed token bound to its purpose, document and tenant;
  - `/docs`, `/redoc` and `/openapi.json`, until section 1 turns them off.

### 8.2 Findings and proposed fixes (nothing changed)

1. **No test proves the list in 8.1 is complete.** A route added later without a gate would be public.
   - **Proposed:** a route-walk test. It walks every route, including the included routers and the acting-as mount, and calls each with no `Authorization` header.
   - Each must answer 401 or 404, except an explicit allowlist: the six routes above and the docs routes when they're on.
   - Estimate 0.25. **Question 9:** add it to this task?
2. **The web app's `/admin/*` is gated in the browser only** (`src/app/admin/layout.tsx`).
   - Anyone gets the Console's HTML shell and its JavaScript chunks, then sees "not found" once `/auth/me` answers. No data is exposed: every Console call is the API's 404. But a stranger can tell the Console exists, which sits badly with Section 7.15.1's "unreachable, not just hidden".
   - A server-side gate isn't simple, because the session is a bearer token in the browser's storage, not a cookie the server sees.
   - Options:
     - **(a)** accept it for the demo and record it as a Phase 6 item. Recommended.
     - **(b)** move the Supabase session into cookies (`@supabase/ssr`) and 404 `/admin/*` in `proxy.ts` without a platform-admin session. About 1.5 to 2 days, and it touches sign-in everywhere.
     - **(c)** serve the Console's pages with `X-Robots-Tag: noindex` and nothing else. Cosmetic only.
3. **No HTTP request rate limit on upload or intake** (Section 7.9). D-196's context lists it as "Phase 6's list".
   - Once the API is public, a flood of requests reaches the API machine. A flood of documents is still caught by the abuse ceiling and the quarantine (7.16.2). Sign-in is rate-limited by Supabase per IP (D-220's figures).
   - **Proposed:** a per-tenant and per-IP limit on `POST /documents/upload` and `POST /intake/email/{token}`, held in Redis, answering 429 with a catalog code. About 1 day. **Question 10:** before the demo, or kept in Phase 6?
4. **The client IP behind Fly's edge** (D-226 item 10, second bullet). `request.client.host` is the proxy's address, so the intake's source-IP note can't see Postmark's real address.
   - **Proposed:** read `Fly-Client-IP` when `FLY_APP_NAME` is set. About 0.1 day.
   - It is also a prerequisite for finding 3's per-IP limit. It goes with finding 3.
5. **Supabase sign-ups** must be off: a read at task 7 (section 6).

## 9. The staging suites before merge

**Not needed, as I read the records.**
- **The rule** is D-223 item 5: a PR runs the staging suites only when the staging environment can change its tests' results in a way CI can't show.
- **The precedents:** that reasoning was applied to a code PR in D-227 item 7 (the API launcher) and to A5 in D-234 item 12.
- **Why here:** this task changes the API's app construction, middleware and settings, the web app, `vercel.json`, `fly.toml`, and records. No database, Storage, worker or migration path changes. CI's `api` job (the full API suite against the local Supabase stack), `web`, and `web-live` (the real API across sites) exercise all of it.
- **If the build changes that,** the answer flips and I ask for a Grant first. Examples: finding 3's rate limit through Redis on the deployed path, or anything touching a database or Storage path.
- **But:** D-232's A3 entry reads D-223 item 5 more narrowly ("exempts only lock-only and tests-only PRs"). So the founder's word decides (question 11).

## 10. Task 7: every Grant and founder step, in order

1. **Founder:** create the Vercel project with no deploy yet (RUNBOOK 13 step 1), and read `<vercel-host>` from the project page.
2. **Grant:** the API deploy from `main`, after RUNBOOK 11's three checks. Set first, typed, history off, staged as RUNBOOK 8:
   - `CORS_ALLOWED_ORIGINS=https://<vercel-host>`;
   - `APP_BASE_URL=https://<vercel-host>`. Invite links, billing return URLs, alert links and the review digest all use it;
   - the signing secret and webhook credentials, per D-235 item 18 task 7;
   - not `API_DOCS_ENABLED`.
3. **Grant:** `fly ips allocate-v4 --shared` and `fly ips allocate-v6` on `docflow-api-staging`. Then `GET https://docflow-api-staging.fly.dev/healthz` (read-only), with `/docs`, `/redoc` and `/openapi.json` all 404.
4. **Founder:** set the three Vercel environment variables (RUNBOOK 13 step 2).
5. **Grant:** the first Vercel production deploy of `main`, then the header and console checks (RUNBOOK 13 step 5).
6. **Grant:** the Supabase Auth URL configuration: the redirect URL, the Site URL if question 7 is yes, and the sign-ups-off read.
7. **Founder or Grant:** the `/healthz` monitor, if question 8 names a tool. Its on-the-day test stops the worker, which is a Fly action, so it needs its own Grant.
8. **Read-only:** `fly secrets list --app docflow-api-staging` shows `CONSOLE_MFA_ENFORCED`.

The rest of task 7 follows its D-235 order: parse, worker, the backfill, page images on the real hosts. The steps above assume the API deploy is the one in that list, not an extra.

**The order's constraint.** `NEXT_PUBLIC_API_BASE_URL` is compiled into the web build, and the API's `CORS_ALLOWED_ORIGINS` and `APP_BASE_URL` need the Vercel host. The project is therefore created first to learn its host (step 1), and the web deploy comes after the API is public (step 5). The API's host is predictable from its app name, so neither side waits on a guess.

**Demo day** (D-235 item 3): starting the machines 30 minutes early covers the API's cold start.

## 11. The total

| Item | Days |
|---|---|
| 1. Docs off | 0.15 |
| 2. CORS (with the refusal, if question 2 is yes) | 0.1 to 0.15 |
| 3. Web CSP and headers, WebKit viewer coverage | 0.75 |
| 3.5. API headers | 0.15 |
| 4. `fly.toml` | 0.05 |
| 5. Vercel config and RUNBOOK 13 | 0.25 |
| 6. Supabase redirect step | 0.05 |
| Records: D-237, BUILD-STATUS, `.env.example` | 0.1 |
| **The task as asked** | **1.6 to 1.65** |
| 7. `/healthz` poll, if question 8 names a tool | +0.25 |
| 8.2 item 1, the route walk, if question 9 is yes | +0.25 |

The task as asked is 0.1 to 0.15 over the plan's 1.5. Most of that is WebKit viewer coverage, which doesn't exist today. With both optional items it is 2.15, 0.65 over. Findings 2(b), 3 and 4 are outside these figures.

## 12. Questions for the founder

1. Docs: also ignore `API_DOCS_ENABLED` on Fly, with a startup line?
2. CORS: refuse to start on Fly with origins that are unset, loopback only, `*`, not `https`, or with a path? Recommended.
3. Vercel previews: never pointed at staging's API? Recommended.
4. CSP: nonces through `proxy.ts`, with every page rendered per request, rather than `'unsafe-inline'` scripts? Recommended.
5. Live e2e: add WebKit to `viewer-cross-site` (+0.1)? Not recommended.
6. Vercel deploys: (a) Git connected with automatic deploys off (recommended), or (b) CLI only?
7. Supabase Site URL: change it to the Vercel host at task 7?
8. `/healthz` poll: which tool, if any? Without one named, it is left out and recorded.
9. Add the route-walk test (+0.25)?
10. The HTTP rate limit (finding 3, with finding 4): before the demo (about 1.1), or kept in Phase 6?
11. Staging suites: none before merge, on D-223 item 5 as applied in D-227 item 7 and D-234 item 12?
12. The web Console's client-only gate: (a) accept and record for Phase 6 (recommended), (b) cookies and a server gate, or (c) noindex only?
