# DocFlow build status: earlier status paragraphs

Each entry is the status paragraph `docs/BUILD-STATUS.md` carried until the date shown, word
for word, newest first (founder, 2026-10-05; D-191). The current status is in BUILD-STATUS
itself. This file is not read at the start of a session; it is here so nothing is lost.

## Replaced on 2026-11-12 (the CI hardening merged; M10's design written; D-239)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-12, with public staging merged and the Playwright install hardening built and ruled on:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 13** (D-199 item 10; flake 12 is #95's run #526 and flake 13 is #97's run #533, each counted once, D-238 item 5; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A6's deploys and backfill are on staging** (founder,
2026-11-04; D-228 item 8, changed by D-233), and its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. A3 stores them and A4 shows
them. A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is merged** (PR #93, `main` is
`799c08a`; main's push run #521 green; D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. Migration
0040 is on staging (2026-11-04; `backup_0040` stays until the founder drops it), and the three
staging suites passed on `5180f59`: core `1283 passed, 1 skipped in 143.62s (0:02:23)`, worker
`307 passed, 8 skipped in 2631.65s (0:43:51)`, API `797 passed, 1 skipped, 3 deselected, 1 warning
in 2860.85s (0:47:40)`, the six counts +0 (RUNBOOK 1.12). **A4 is merged**
(PR #94, `main` is `014f6c8`; main's push run #524 green; D-233): the viewer shows a document's page images in the page, with a
pager, zoom and pan, and a text file's stored text in the page as escaped text; the sandboxed frame
is used only while a drawn format has no pages yet. Page images come on one token of purpose
`page`, and the page route serves only PNG and JPEG; the web-live job runs the dev parse service,
and 21 of the 22 positive fixtures have a live test (`po.doc` needs LibreOffice; D-233 item 8). Its
staging suites passed on `ef96859`, the tree `014f6c8` merged: core `1289 passed, 1 skipped in
132.68s (0:02:12)`, worker `307 passed, 8 skipped in 2563.54s (0:42:43)`, API `813 passed, 1 skipped,
3 deselected, 1 warning in 2899.02s (0:48:19)`, the six counts +0. **A5 is merged**
(PR #95, `main` is `ec3509a`; main's push run #527 green; D-234): the review screen at 375 px. Below 1280 the document has a box
of its own height, the actions sit in a bar fixed to the bottom of the screen, and the app header's
links scroll sideways in a row of their own; below 640 each line is a card made from the same table,
which keeps its table roles. Every control a reviewer taps is at least 44 px, Save and Approve 48.
Pinch and the zoom buttons keep their centre, and at fit to width a swipe scrolls as on any page. A
page turn more than 240 s after the links arrived asks for fresh ones first. The page no longer
scrolls sideways at 375: the 196 px came from the line table's hidden labels, not the header (D-234
item 2). WebKit runs the 375 px layout spec in CI (amending D-228 item 10). No staging suites, and
nothing deploys in A5. **The demo plan is D-235** (founder,
2026-11-10 and 2026-11-11): the demo runs on staging, made publicly reachable: the web app on
Vercel Pro with its CSP and security headers, and the API with a public address and `/docs`,
`/redoc` and `/openapi.json` off. D-209's outreach rules apply to it, with 48 hours of quiet before a
booked demo. Both synthetic PO sets, Ironline (welding) and Keystone (fasteners), run on staging
after an accuracy bar for each; the founder then picks the vertical and one demo tenant is built.
**A6 is merged** (PR #98, `main` `7d6bb45`; D-236): `python -m app.backfill_pages` queues the normal page render for
every reviewable document with no pages, a batch at a time and only once the pages queue is empty
(run at task 7 under a Grant, never in the 48 hours before a booked demo; RUNBOOK 9.2). The
Console's strip shows "Pages waiting". The worker refuses to start with a `DISPATCH_IN_FLIGHT_TARGET`
other than the one `PARSE_SLOTS` was sized for, and prints the value. `image_preview` stays until
after the demo. The Debian snapshot PR went first (PR #97, `cb064ef`); CI was green on A6's final
head `1fd17d0`, and its staging suites passed there under a Grant: core 1291 passed / 1 skipped,
worker 344 passed / 8 skipped, API 815 passed / 1 skipped / 3 deselected, the six counts +0
(D-236 item 8). **Public staging's code is merged** (PR #99, `main` `637f96f`; D-237;
design `docs/designs/public-staging.md`). On Fly the API keeps `/docs`, `/redoc` and
`/openapi.json` off and refuses to start without an exact `https` CORS origin. Every API response
carries nosniff, no-referrer, a deny-all CSP and HSTS. The web app sends a per-request nonce CSP
(no `'unsafe-inline'`, no `'unsafe-eval'`) and its security headers, `noindex` on `/admin`, and
every page renders per request. `npm run check:bundle` proves the browser bundle carries only the
three public values. A route walk classifies all 111 API routes. WebKit now runs the viewer, and
every e2e test fails on a CSP violation. `apps/web/vercel.json` turns off automatic deploys.
RUNBOOK 13 is task 7's order, starting with the Anthropic spend limit. Staging's sign-ups were
read off. No `/healthz` poll before the demo; the web Console's browser-only gate, the HTTP rate
limit and `Fly-Client-IP` move to Phase 6. No staging suites before merge (D-237 item 12).
**The Playwright install hardening is built** (`ci/playwright-install-hardening`; D-238; CI
only). Both Playwright installs run through `scripts/ci/playwright_install.sh`: at most two
attempts of 5 minutes, 30 s apart, inside an 11-minute step, `--with-deps` kept. Every job has a
time limit, about twice its longest of the last 10 green runs on `main`, or more where its own
step limits need it (web 14, web-live 15, core 7, api 21, worker 13, parse 21). Every
upload-artifact step fails in 5 minutes and stays required. Evidence: #95's web install ran
1 h 41 min on a DNS failure, and #97's uploads hung 11 to 13 minutes. The founder's rulings (D-238): the four job limits stay as set; slow installs failing is accepted, and the 5-minute limit is revisited if a 5-minute-plus install fails a run; #95 is flake 12 and #97 is flake 13, each run counted once, so the count in D-199 item 10 is now 13.
**Next, in order (D-235 item 18):** the CI hardening's merge. M10, with the routing default
at today's `claude-haiku-4-5`. Then the
deploys under Grants and the backfill. The Console's sweep, the Chrome and Safari pass, the
real-iPhone checks and the partner walkthrough. Postmark inbound end to end. Both PO sets on
staging. E1, then E2a. B1's demo half, labelling with the routing model. The pricing-unit fix and
the export preset, only if needed. One demo tenant. The final redeploy with the staging suites,
D-209's smoke test (owed since the worker's deploy of 2026-11-01) and `demo-1`. The demo routine
and two cold rehearsals. About 32 working days with contingency: demo-ready about 29 December
2026. **After the demo:** Blocker 3's background commit with the duplicate SKU index (0041). The
docs PR, which ends Stage 5 (it also takes `apps/web/AGENTS.md`, whose first heading `next dev`
rewrote from `#` to `##` when it started on 2026-10-27: either commit that heading or stop `next
dev` from rewriting the file; founder), CLAUDE.md naming the three staging apps, and RUNBOOK 1.4's
hand-started dev parse service checked against the suites that use it (D-229 item 6). Counting rows
before extraction. Firefox in CI. The production environment. D-195's changes 1 and 2 and its
comparison. Then the independent review, then pilots. **The 2026-10-28 environments can be
deleted** (`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on
the new ones; the founder decides when (D-229 item 8). **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0040 (2026-11-04). `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-12 (the Playwright install hardening ruled on; D-238)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-12, with public staging merged and the Playwright install hardening built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A6's deploys and backfill are on staging** (founder,
2026-11-04; D-228 item 8, changed by D-233), and its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. A3 stores them and A4 shows
them. A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is merged** (PR #93, `main` is
`799c08a`; main's push run #521 green; D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. Migration
0040 is on staging (2026-11-04; `backup_0040` stays until the founder drops it), and the three
staging suites passed on `5180f59`: core `1283 passed, 1 skipped in 143.62s (0:02:23)`, worker
`307 passed, 8 skipped in 2631.65s (0:43:51)`, API `797 passed, 1 skipped, 3 deselected, 1 warning
in 2860.85s (0:47:40)`, the six counts +0 (RUNBOOK 1.12). **A4 is merged**
(PR #94, `main` is `014f6c8`; main's push run #524 green; D-233): the viewer shows a document's page images in the page, with a
pager, zoom and pan, and a text file's stored text in the page as escaped text; the sandboxed frame
is used only while a drawn format has no pages yet. Page images come on one token of purpose
`page`, and the page route serves only PNG and JPEG; the web-live job runs the dev parse service,
and 21 of the 22 positive fixtures have a live test (`po.doc` needs LibreOffice; D-233 item 8). Its
staging suites passed on `ef96859`, the tree `014f6c8` merged: core `1289 passed, 1 skipped in
132.68s (0:02:12)`, worker `307 passed, 8 skipped in 2563.54s (0:42:43)`, API `813 passed, 1 skipped,
3 deselected, 1 warning in 2899.02s (0:48:19)`, the six counts +0. **A5 is merged**
(PR #95, `main` is `ec3509a`; main's push run #527 green; D-234): the review screen at 375 px. Below 1280 the document has a box
of its own height, the actions sit in a bar fixed to the bottom of the screen, and the app header's
links scroll sideways in a row of their own; below 640 each line is a card made from the same table,
which keeps its table roles. Every control a reviewer taps is at least 44 px, Save and Approve 48.
Pinch and the zoom buttons keep their centre, and at fit to width a swipe scrolls as on any page. A
page turn more than 240 s after the links arrived asks for fresh ones first. The page no longer
scrolls sideways at 375: the 196 px came from the line table's hidden labels, not the header (D-234
item 2). WebKit runs the 375 px layout spec in CI (amending D-228 item 10). No staging suites, and
nothing deploys in A5. **The demo plan is D-235** (founder,
2026-11-10 and 2026-11-11): the demo runs on staging, made publicly reachable: the web app on
Vercel Pro with its CSP and security headers, and the API with a public address and `/docs`,
`/redoc` and `/openapi.json` off. D-209's outreach rules apply to it, with 48 hours of quiet before a
booked demo. Both synthetic PO sets, Ironline (welding) and Keystone (fasteners), run on staging
after an accuracy bar for each; the founder then picks the vertical and one demo tenant is built.
**A6 is merged** (PR #98, `main` `7d6bb45`; D-236): `python -m app.backfill_pages` queues the normal page render for
every reviewable document with no pages, a batch at a time and only once the pages queue is empty
(run at task 7 under a Grant, never in the 48 hours before a booked demo; RUNBOOK 9.2). The
Console's strip shows "Pages waiting". The worker refuses to start with a `DISPATCH_IN_FLIGHT_TARGET`
other than the one `PARSE_SLOTS` was sized for, and prints the value. `image_preview` stays until
after the demo. The Debian snapshot PR went first (PR #97, `cb064ef`); CI was green on A6's final
head `1fd17d0`, and its staging suites passed there under a Grant: core 1291 passed / 1 skipped,
worker 344 passed / 8 skipped, API 815 passed / 1 skipped / 3 deselected, the six counts +0
(D-236 item 8). **Public staging's code is merged** (PR #99, `main` `637f96f`; D-237;
design `docs/designs/public-staging.md`). On Fly the API keeps `/docs`, `/redoc` and
`/openapi.json` off and refuses to start without an exact `https` CORS origin. Every API response
carries nosniff, no-referrer, a deny-all CSP and HSTS. The web app sends a per-request nonce CSP
(no `'unsafe-inline'`, no `'unsafe-eval'`) and its security headers, `noindex` on `/admin`, and
every page renders per request. `npm run check:bundle` proves the browser bundle carries only the
three public values. A route walk classifies all 111 API routes. WebKit now runs the viewer, and
every e2e test fails on a CSP violation. `apps/web/vercel.json` turns off automatic deploys.
RUNBOOK 13 is task 7's order, starting with the Anthropic spend limit. Staging's sign-ups were
read off. No `/healthz` poll before the demo; the web Console's browser-only gate, the HTTP rate
limit and `Fly-Client-IP` move to Phase 6. No staging suites before merge (D-237 item 12).
**The Playwright install hardening is built** (`ci/playwright-install-hardening`; D-238; CI
only). Both Playwright installs run through `scripts/ci/playwright_install.sh`: at most two
attempts of 5 minutes, 30 s apart, inside an 11-minute step, `--with-deps` kept. Every job has a
time limit, about twice its longest of the last 10 green runs on `main`, or more where its own
step limits need it (web 14, web-live 15, core 7, api 21, worker 13, parse 21). Every
upload-artifact step fails in 5 minutes and stays required. Evidence: #95's web install ran
1 h 41 min on a DNS failure, and #97's uploads hung 11 to 13 minutes. Whether those count as
flakes (D-199 item 10) is the founder's to decide.
**Next, in order (D-235 item 18):** the CI hardening's merge. M10, with the routing default
at today's `claude-haiku-4-5`. Then the
deploys under Grants and the backfill. The Console's sweep, the Chrome and Safari pass, the
real-iPhone checks and the partner walkthrough. Postmark inbound end to end. Both PO sets on
staging. E1, then E2a. B1's demo half, labelling with the routing model. The pricing-unit fix and
the export preset, only if needed. One demo tenant. The final redeploy with the staging suites,
D-209's smoke test (owed since the worker's deploy of 2026-11-01) and `demo-1`. The demo routine
and two cold rehearsals. About 32 working days with contingency: demo-ready about 29 December
2026. **After the demo:** Blocker 3's background commit with the duplicate SKU index (0041). The
docs PR, which ends Stage 5 (it also takes `apps/web/AGENTS.md`, whose first heading `next dev`
rewrote from `#` to `##` when it started on 2026-10-27: either commit that heading or stop `next
dev` from rewriting the file; founder), CLAUDE.md naming the three staging apps, and RUNBOOK 1.4's
hand-started dev parse service checked against the suites that use it (D-229 item 6). Counting rows
before extraction. Firefox in CI. The production environment. D-195's changes 1 and 2 and its
comparison. Then the independent review, then pilots. **The 2026-10-28 environments can be
deleted** (`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on
the new ones; the founder decides when (D-229 item 8). **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0040 (2026-11-04). `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-12 (public staging merged; the Playwright install hardening built on the branch; D-238)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-12, with public staging's code built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A6's deploys and backfill are on staging** (founder,
2026-11-04; D-228 item 8, changed by D-233), and its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. A3 stores them and A4 shows
them. A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is merged** (PR #93, `main` is
`799c08a`; main's push run #521 green; D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. Migration
0040 is on staging (2026-11-04; `backup_0040` stays until the founder drops it), and the three
staging suites passed on `5180f59`: core `1283 passed, 1 skipped in 143.62s (0:02:23)`, worker
`307 passed, 8 skipped in 2631.65s (0:43:51)`, API `797 passed, 1 skipped, 3 deselected, 1 warning
in 2860.85s (0:47:40)`, the six counts +0 (RUNBOOK 1.12). **A4 is merged**
(PR #94, `main` is `014f6c8`; main's push run #524 green; D-233): the viewer shows a document's page images in the page, with a
pager, zoom and pan, and a text file's stored text in the page as escaped text; the sandboxed frame
is used only while a drawn format has no pages yet. Page images come on one token of purpose
`page`, and the page route serves only PNG and JPEG; the web-live job runs the dev parse service,
and 21 of the 22 positive fixtures have a live test (`po.doc` needs LibreOffice; D-233 item 8). Its
staging suites passed on `ef96859`, the tree `014f6c8` merged: core `1289 passed, 1 skipped in
132.68s (0:02:12)`, worker `307 passed, 8 skipped in 2563.54s (0:42:43)`, API `813 passed, 1 skipped,
3 deselected, 1 warning in 2899.02s (0:48:19)`, the six counts +0. **A5 is merged**
(PR #95, `main` is `ec3509a`; main's push run #527 green; D-234): the review screen at 375 px. Below 1280 the document has a box
of its own height, the actions sit in a bar fixed to the bottom of the screen, and the app header's
links scroll sideways in a row of their own; below 640 each line is a card made from the same table,
which keeps its table roles. Every control a reviewer taps is at least 44 px, Save and Approve 48.
Pinch and the zoom buttons keep their centre, and at fit to width a swipe scrolls as on any page. A
page turn more than 240 s after the links arrived asks for fresh ones first. The page no longer
scrolls sideways at 375: the 196 px came from the line table's hidden labels, not the header (D-234
item 2). WebKit runs the 375 px layout spec in CI (amending D-228 item 10). No staging suites, and
nothing deploys in A5. **The demo plan is D-235** (founder,
2026-11-10 and 2026-11-11): the demo runs on staging, made publicly reachable: the web app on
Vercel Pro with its CSP and security headers, and the API with a public address and `/docs`,
`/redoc` and `/openapi.json` off. D-209's outreach rules apply to it, with 48 hours of quiet before a
booked demo. Both synthetic PO sets, Ironline (welding) and Keystone (fasteners), run on staging
after an accuracy bar for each; the founder then picks the vertical and one demo tenant is built.
**A6 is merged** (PR #98, `main` `7d6bb45`; D-236): `python -m app.backfill_pages` queues the normal page render for
every reviewable document with no pages, a batch at a time and only once the pages queue is empty
(run at task 7 under a Grant, never in the 48 hours before a booked demo; RUNBOOK 9.2). The
Console's strip shows "Pages waiting". The worker refuses to start with a `DISPATCH_IN_FLIGHT_TARGET`
other than the one `PARSE_SLOTS` was sized for, and prints the value. `image_preview` stays until
after the demo. The Debian snapshot PR went first (PR #97, `cb064ef`); CI was green on A6's final
head `1fd17d0`, and its staging suites passed there under a Grant: core 1291 passed / 1 skipped,
worker 344 passed / 8 skipped, API 815 passed / 1 skipped / 3 deselected, the six counts +0
(D-236 item 8). **Public staging's code is built** (`phase55/stage5-public-staging`; D-237;
design `docs/designs/public-staging.md`). On Fly the API keeps `/docs`, `/redoc` and
`/openapi.json` off and refuses to start without an exact `https` CORS origin. Every API response
carries nosniff, no-referrer, a deny-all CSP and HSTS. The web app sends a per-request nonce CSP
(no `'unsafe-inline'`, no `'unsafe-eval'`) and its security headers, `noindex` on `/admin`, and
every page renders per request. `npm run check:bundle` proves the browser bundle carries only the
three public values. A route walk classifies all 111 API routes. WebKit now runs the viewer, and
every e2e test fails on a CSP violation. `apps/web/vercel.json` turns off automatic deploys.
RUNBOOK 13 is task 7's order, starting with the Anthropic spend limit. Staging's sign-ups were
read off. No `/healthz` poll before the demo; the web Console's browser-only gate, the HTTP rate
limit and `Fly-Client-IP` move to Phase 6. No staging suites before merge (D-237 item 12).
**Next, in order (D-235 item 18):** public staging's merge. M10, with the routing default
at today's `claude-haiku-4-5`. Then the
deploys under Grants and the backfill. The Console's sweep, the Chrome and Safari pass, the
real-iPhone checks and the partner walkthrough. Postmark inbound end to end. Both PO sets on
staging. E1, then E2a. B1's demo half, labelling with the routing model. The pricing-unit fix and
the export preset, only if needed. One demo tenant. The final redeploy with the staging suites,
D-209's smoke test (owed since the worker's deploy of 2026-11-01) and `demo-1`. The demo routine
and two cold rehearsals. About 32 working days with contingency: demo-ready about 29 December
2026. **After the demo:** Blocker 3's background commit with the duplicate SKU index (0041). The
docs PR, which ends Stage 5 (it also takes `apps/web/AGENTS.md`, whose first heading `next dev`
rewrote from `#` to `##` when it started on 2026-10-27: either commit that heading or stop `next
dev` from rewriting the file; founder), CLAUDE.md naming the three staging apps, and RUNBOOK 1.4's
hand-started dev parse service checked against the suites that use it (D-229 item 6). Counting rows
before extraction. Firefox in CI. The production environment. D-195's changes 1 and 2 and its
comparison. Then the independent review, then pilots. **The 2026-10-28 environments can be
deleted** (`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on
the new ones; the founder decides when (D-229 item 8). **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0040 (2026-11-04). `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-12 (public staging's code built on the branch; D-237)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-12, with A6's code built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A6's deploys and backfill are on staging** (founder,
2026-11-04; D-228 item 8, changed by D-233), and its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. A3 stores them and A4 shows
them. A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is merged** (PR #93, `main` is
`799c08a`; main's push run #521 green; D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. Migration
0040 is on staging (2026-11-04; `backup_0040` stays until the founder drops it), and the three
staging suites passed on `5180f59`: core `1283 passed, 1 skipped in 143.62s (0:02:23)`, worker
`307 passed, 8 skipped in 2631.65s (0:43:51)`, API `797 passed, 1 skipped, 3 deselected, 1 warning
in 2860.85s (0:47:40)`, the six counts +0 (RUNBOOK 1.12). **A4 is merged**
(PR #94, `main` is `014f6c8`; main's push run #524 green; D-233): the viewer shows a document's page images in the page, with a
pager, zoom and pan, and a text file's stored text in the page as escaped text; the sandboxed frame
is used only while a drawn format has no pages yet. Page images come on one token of purpose
`page`, and the page route serves only PNG and JPEG; the web-live job runs the dev parse service,
and 21 of the 22 positive fixtures have a live test (`po.doc` needs LibreOffice; D-233 item 8). Its
staging suites passed on `ef96859`, the tree `014f6c8` merged: core `1289 passed, 1 skipped in
132.68s (0:02:12)`, worker `307 passed, 8 skipped in 2563.54s (0:42:43)`, API `813 passed, 1 skipped,
3 deselected, 1 warning in 2899.02s (0:48:19)`, the six counts +0. **A5 is merged**
(PR #95, `main` is `ec3509a`; main's push run #527 green; D-234): the review screen at 375 px. Below 1280 the document has a box
of its own height, the actions sit in a bar fixed to the bottom of the screen, and the app header's
links scroll sideways in a row of their own; below 640 each line is a card made from the same table,
which keeps its table roles. Every control a reviewer taps is at least 44 px, Save and Approve 48.
Pinch and the zoom buttons keep their centre, and at fit to width a swipe scrolls as on any page. A
page turn more than 240 s after the links arrived asks for fresh ones first. The page no longer
scrolls sideways at 375: the 196 px came from the line table's hidden labels, not the header (D-234
item 2). WebKit runs the 375 px layout spec in CI (amending D-228 item 10). No staging suites, and
nothing deploys in A5. **The demo plan is D-235** (founder,
2026-11-10 and 2026-11-11): the demo runs on staging, made publicly reachable: the web app on
Vercel Pro with its CSP and security headers, and the API with a public address and `/docs`,
`/redoc` and `/openapi.json` off. D-209's outreach rules apply to it, with 48 hours of quiet before a
booked demo. Both synthetic PO sets, Ironline (welding) and Keystone (fasteners), run on staging
after an accuracy bar for each; the founder then picks the vertical and one demo tenant is built.
**A6's code is built**
(`phase55/stage5-a6`; D-236): `python -m app.backfill_pages` queues the normal page render for
every reviewable document with no pages, a batch at a time and only once the pages queue is empty
(run at task 7 under a Grant, never in the 48 hours before a booked demo; RUNBOOK 9.2). The
Console's strip shows "Pages waiting". The worker refuses to start with a `DISPATCH_IN_FLIGHT_TARGET`
other than the one `PARSE_SLOTS` was sized for, and prints the value. `image_preview` stays until
after the demo. Its core, worker and API suites run on staging under a Grant before the merge (D-223).
**The Debian snapshot PR is merged** (PR #97, as `cb064ef`) and is merged into `phase55/stage5-a6`; A6's staging suites follow CI green on the branch's final head, under a Grant (D-236 item 8).
**Next, in order (D-235 item 18):** A6's staging suites and merge. Public staging's code. M10, with the routing default
at today's `claude-haiku-4-5`. Then the
deploys under Grants and the backfill. The Console's sweep, the Chrome and Safari pass, the
real-iPhone checks and the partner walkthrough. Postmark inbound end to end. Both PO sets on
staging. E1, then E2a. B1's demo half, labelling with the routing model. The pricing-unit fix and
the export preset, only if needed. One demo tenant. The final redeploy with the staging suites,
D-209's smoke test (owed since the worker's deploy of 2026-11-01) and `demo-1`. The demo routine
and two cold rehearsals. About 32 working days with contingency: demo-ready about 29 December
2026. **After the demo:** Blocker 3's background commit with the duplicate SKU index (0041). The
docs PR, which ends Stage 5 (it also takes `apps/web/AGENTS.md`, whose first heading `next dev`
rewrote from `#` to `##` when it started on 2026-10-27: either commit that heading or stop `next
dev` from rewriting the file; founder), CLAUDE.md naming the three staging apps, and RUNBOOK 1.4's
hand-started dev parse service checked against the suites that use it (D-229 item 6). Counting rows
before extraction. Firefox in CI. The production environment. D-195's changes 1 and 2 and its
comparison. Then the independent review, then pilots. **The 2026-10-28 environments can be
deleted** (`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on
the new ones; the founder decides when (D-229 item 8). **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0040 (2026-11-04). `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-11 (A6's code built on the branch; D-236)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-11, with A5 merged and the demo plan recorded:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A6's deploys and backfill are on staging** (founder,
2026-11-04; D-228 item 8, changed by D-233), and its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. A3 stores them and A4 shows
them. A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is merged** (PR #93, `main` is
`799c08a`; main's push run #521 green; D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. Migration
0040 is on staging (2026-11-04; `backup_0040` stays until the founder drops it), and the three
staging suites passed on `5180f59`: core `1283 passed, 1 skipped in 143.62s (0:02:23)`, worker
`307 passed, 8 skipped in 2631.65s (0:43:51)`, API `797 passed, 1 skipped, 3 deselected, 1 warning
in 2860.85s (0:47:40)`, the six counts +0 (RUNBOOK 1.12). **A4 is merged**
(PR #94, `main` is `014f6c8`; main's push run #524 green; D-233): the viewer shows a document's page images in the page, with a
pager, zoom and pan, and a text file's stored text in the page as escaped text; the sandboxed frame
is used only while a drawn format has no pages yet. Page images come on one token of purpose
`page`, and the page route serves only PNG and JPEG; the web-live job runs the dev parse service,
and 21 of the 22 positive fixtures have a live test (`po.doc` needs LibreOffice; D-233 item 8). Its
staging suites passed on `ef96859`, the tree `014f6c8` merged: core `1289 passed, 1 skipped in
132.68s (0:02:12)`, worker `307 passed, 8 skipped in 2563.54s (0:42:43)`, API `813 passed, 1 skipped,
3 deselected, 1 warning in 2899.02s (0:48:19)`, the six counts +0. **A5 is merged**
(PR #95, `main` is `ec3509a`; main's push run #527 green; D-234): the review screen at 375 px. Below 1280 the document has a box
of its own height, the actions sit in a bar fixed to the bottom of the screen, and the app header's
links scroll sideways in a row of their own; below 640 each line is a card made from the same table,
which keeps its table roles. Every control a reviewer taps is at least 44 px, Save and Approve 48.
Pinch and the zoom buttons keep their centre, and at fit to width a swipe scrolls as on any page. A
page turn more than 240 s after the links arrived asks for fresh ones first. The page no longer
scrolls sideways at 375: the 196 px came from the line table's hidden labels, not the header (D-234
item 2). WebKit runs the 375 px layout spec in CI (amending D-228 item 10). No staging suites, and
nothing deploys in A5. **The demo plan is D-235** (founder,
2026-11-10 and 2026-11-11): the demo runs on staging, made publicly reachable: the web app on
Vercel Pro with its CSP and security headers, and the API with a public address and `/docs`,
`/redoc` and `/openapi.json` off. D-209's outreach rules apply to it, with 48 hours of quiet before a
booked demo. Both synthetic PO sets, Ironline (welding) and Keystone (fasteners), run on staging
after an accuracy bar for each; the founder then picks the vertical and one demo tenant is built.
**Next, in order (D-235 item 18):** A6's code. Public staging's code. M10, with the routing default
at today's `claude-haiku-4-5`. The Debian snapshot PR (`deps/debian-snapshot-20261109`), then the
deploys under Grants and the backfill. The Console's sweep, the Chrome and Safari pass, the
real-iPhone checks and the partner walkthrough. Postmark inbound end to end. Both PO sets on
staging. E1, then E2a. B1's demo half, labelling with the routing model. The pricing-unit fix and
the export preset, only if needed. One demo tenant. The final redeploy with the staging suites,
D-209's smoke test (owed since the worker's deploy of 2026-11-01) and `demo-1`. The demo routine
and two cold rehearsals. About 32 working days with contingency: demo-ready about 29 December
2026. **After the demo:** Blocker 3's background commit with the duplicate SKU index (0041). The
docs PR, which ends Stage 5 (it also takes `apps/web/AGENTS.md`, whose first heading `next dev`
rewrote from `#` to `##` when it started on 2026-10-27: either commit that heading or stop `next
dev` from rewriting the file; founder), CLAUDE.md naming the three staging apps, and RUNBOOK 1.4's
hand-started dev parse service checked against the suites that use it (D-229 item 6). Counting rows
before extraction. Firefox in CI. The production environment. D-195's changes 1 and 2 and its
comparison. Then the independent review, then pilots. **The 2026-10-28 environments can be
deleted** (`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on
the new ones; the founder decides when (D-229 item 8). **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0040 (2026-11-04). `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-11 (A5 merged at ec3509a; D-235 recorded)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-05, with A4 merged and A5 built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A6's deploys and backfill are on staging** (founder,
2026-11-04; D-228 item 8, changed by D-233), and its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. A3 stores them and A4 shows
them. A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is merged** (PR #93, `main` is
`799c08a`; main's push run #521 green; D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. Migration
0040 is on staging (2026-11-04; `backup_0040` stays until the founder drops it), and the three
staging suites passed on `5180f59`: core `1283 passed, 1 skipped in 143.62s (0:02:23)`, worker
`307 passed, 8 skipped in 2631.65s (0:43:51)`, API `797 passed, 1 skipped, 3 deselected, 1 warning
in 2860.85s (0:47:40)`, the six counts +0 (RUNBOOK 1.12). **A4 is merged**
(PR #94, `main` is `014f6c8`; main's push run #524 green; D-233): the viewer shows a document's page images in the page, with a
pager, zoom and pan, and a text file's stored text in the page as escaped text; the sandboxed frame
is used only while a drawn format has no pages yet. Page images come on one token of purpose
`page`, and the page route serves only PNG and JPEG; the web-live job runs the dev parse service,
and 21 of the 22 positive fixtures have a live test (`po.doc` needs LibreOffice; D-233 item 8). Its
staging suites passed on `ef96859`, the tree `014f6c8` merged: core `1289 passed, 1 skipped in
132.68s (0:02:12)`, worker `307 passed, 8 skipped in 2563.54s (0:42:43)`, API `813 passed, 1 skipped,
3 deselected, 1 warning in 2899.02s (0:48:19)`, the six counts +0. **A5 is built**
(`phase55/stage5-review-a5`; D-234): the review screen at 375 px. Below 1280 the document has a box
of its own height, the actions sit in a bar fixed to the bottom of the screen, and the app header's
links scroll sideways in a row of their own; below 640 each line is a card made from the same table,
which keeps its table roles. Every control a reviewer taps is at least 44 px, Save and Approve 48.
Pinch and the zoom buttons keep their centre, and at fit to width a swipe scrolls as on any page. A
page turn more than 240 s after the links arrived asks for fresh ones first. The page no longer
scrolls sideways at 375: the 196 px came from the line table's hidden labels, not the header (D-234
item 2). WebKit runs the 375 px layout spec in CI (amending D-228 item 10). No staging suites, and
nothing deploys in A5. **Next, in order (D-228 item 7):** A6. M10, with D-195 change 1 straight after it and M10's model-ID part folded
into change 1. B1's demo half, the inbound-mail item (D-230). Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder), CLAUDE.md
naming the three staging apps, and RUNBOOK 1.4's hand-started dev parse service checked against the
suites that use it (D-229 item 6). **The 2026-10-28 environments can be deleted**
(`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on the new
ones; the founder decides when (D-229 item 8). The production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0040 (2026-11-04). `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-05 (A4 merged at 014f6c8; A5 built on the branch)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-04, with A3 merged and A4 built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A6's deploys and backfill are on staging** (founder,
2026-11-04; D-228 item 8, changed by D-233), and its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. A3 stores them and A4 shows
them. A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is merged** (PR #93, `main` is
`799c08a`; main's push run #521 green; D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. Migration
0040 is on staging (2026-11-04; `backup_0040` stays until the founder drops it), and the three
staging suites passed on `5180f59`: core `1283 passed, 1 skipped in 143.62s (0:02:23)`, worker
`307 passed, 8 skipped in 2631.65s (0:43:51)`, API `797 passed, 1 skipped, 3 deselected, 1 warning
in 2860.85s (0:47:40)`, the six counts +0 (RUNBOOK 1.12). **A4 is built**
(`phase55/stage5-viewer-a4`; D-233): the viewer shows a document's page images in the page, with a
pager, zoom and pan, and a text file's stored text in the page as escaped text; the sandboxed frame
is used only while a drawn format has no pages yet. Page images come on one token of purpose
`page`, and the page route serves only PNG and JPEG; the web-live job runs the dev parse service,
and 21 of the 22 positive fixtures have a live test (`po.doc` needs LibreOffice; D-233 item 8). It
merges only after a green PR run and the staging suites passing; nothing deploys in A4. **Next, in order (D-228 item 7):** A5 and A6, with
A5 absorbing the 375 px fix. M10, with D-195 change 1 straight after it and M10's model-ID part folded
into change 1. B1's demo half, the inbound-mail item (D-230). Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder), CLAUDE.md
naming the three staging apps, and RUNBOOK 1.4's hand-started dev parse service checked against the
suites that use it (D-229 item 6). **The 2026-10-28 environments can be deleted**
(`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on the new
ones; the founder decides when (D-229 item 8). The production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0040 (2026-11-04). `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-04 (A3 merged; A4 built on the branch)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-04, with the port PR merged and A3 built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is merged** (PR #92, `main` is `c36ca96`; main's push run #516
green; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A4 is on staging** (founder, 2026-11-02; D-228 item 8), and
its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. Nothing stores or shows them
yet (A3, A4). A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **A3 is built** (`phase55/stage5-viewer-a3`;
D-232): the worker asks the parse service for every page and stores it under
`derived/{doc}/pages/`, on a queue and process of its own, and migration 0040 adds
`documents.page_count` and `pages`, written once when every page is stored. A failed or cut-off render
leaves both NULL, and the viewer falls back; a seccomp kill still alerts, and a stored path in another tenant's folder raises the existing critical alert. It
merges only after a green PR run, then 0040 on staging and the staging suites passing (RUNBOOK 1.12);
its database tests first run in CI. **Next, in order (D-228 item 7):** A4 to A6, with A5
absorbing the 375 px fix. M10, with D-195 change 1 straight after it and M10's model-ID part folded
into change 1. B1's demo half, the inbound-mail item (D-230). Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder), CLAUDE.md
naming the three staging apps, and RUNBOOK 1.4's hand-started dev parse service checked against the
suites that use it (D-229 item 6). **The 2026-10-28 environments can be deleted**
(`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on the new
ones; the founder decides when (D-229 item 8). The production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0039. `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-04 (the port PR merged; A3 built on the branch)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-03, with A2 merged:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 11** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7; flake 11 is
A2's branch push run #511 on `1a04530`, the same port at the same step, holder not identified, D-231
item 1). **The port-reservation CI PR is built** (`ci/supabase-port-reservation`; D-231): each job
that starts the Supabase stack first keeps 54320 to 54327 out of automatic port assignment, and the
steps around the stack's start print every socket on those ports with its process, and the runner
image; "Build the API's image" now runs only when buildx was set up, so a failed stack start no longer
turns it red too. **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A4 is on staging** (founder, 2026-11-02; D-228 item 8), and
its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is merged** (PR #91, `main` is `56fe7d2`; main's push run #513 green; PR run #512's parse HTTP
step `478 passed`, parse unit `635 passed, 17 warnings`, parse audit no known vulnerabilities, API
`798 passed, 3 deselected, 1 warning in 90.37s (0:01:30)`, every left-behind count +0; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. Nothing stores or shows them
yet (A3, A4). A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **Next, in order (D-228 item 7):** A3 to A6, with A5
absorbing the 375 px fix. M10, with D-195 change 1 straight after it and M10's model-ID part folded
into change 1. B1's demo half, the inbound-mail item (D-230). Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder), CLAUDE.md
naming the three staging apps, and RUNBOOK 1.4's hand-started dev parse service checked against the
suites that use it (D-229 item 6). **The 2026-10-28 environments can be deleted**
(`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on the new
ones; the founder decides when (D-229 item 8). The production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0039. `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-03, after A2's merge (the port-reservation CI PR built)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-03, with A1 merged and the worker suite's re-run passed:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 10** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A4 is on staging** (founder, 2026-11-02; D-228 item 8), and
its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **A2 is built** (`phase55/stage5-viewer-a2`; D-228): the parse
service draws page images on a new `POST /v1/pages`: every page of a PDF or TIFF, and a sender's
single image, a PDF at 200 dpi, each page the smaller of PNG and JPEG, a gray page stored as gray,
in answers of at most 32 MiB of base64 that say where the next starts. Nothing stores or shows them
yet (A3, A4). A 25 MB upload holds at most about 30 poor-scan pages (23.7 MB), which fit in one
answer; D-228 item 2's 44-page figure assumed more could arrive. The batching still serves long PDFs
whose pages are small on disk but large as images. **Next, in order (D-228 item 7):** A3 to A6, with A5
absorbing the 375 px fix. M10, with D-195 change 1 straight after it and M10's model-ID part folded
into change 1. B1's demo half, the inbound-mail item (D-230). Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder), CLAUDE.md
naming the three staging apps, and RUNBOOK 1.4's hand-started dev parse service checked against the
suites that use it (D-229 item 6). **The 2026-10-28 environments can be deleted**
(`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on the new
ones; the founder decides when (D-229 item 8). The production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0039. `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-03, later the same day (A2 built on the branch)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-03, with A1 merged and the worker suite's re-run passed:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 10** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A4 is on staging** (founder, 2026-11-02; D-228 item 8), and
its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **Next, in order (D-228 item 7):** A2, then A3 to A6, with A5
absorbing the 375 px fix. M10, with D-195 change 1 straight after it and M10's model-ID part folded
into change 1. B1's demo half, the inbound-mail item (D-230). Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder), CLAUDE.md
naming the three staging apps, and RUNBOOK 1.4's hand-started dev parse service checked against the
suites that use it (D-229 item 6). **The 2026-10-28 environments can be deleted**
(`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on the new
ones; the founder decides when (D-229 item 8). The production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0039. `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-03, later the same day (B1 sized and its rulings recorded in D-230)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-03, with A1 merged and the worker suite's re-run passed:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 10** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9; flake 10 is main's push run #510 on `91d4962`,
where Inbucket's host port 54324 was already in use at the api job's stack start, D-229 item 7). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A4 is on staging** (founder, 2026-11-02; D-228 item 8), and
its step is cancelled. **The founder's viewer decisions are D-228.** **A1 is merged** (PR #90,
`main` is `91d4962`; D-228 item 12; main's push run #510 green on a re-run of its api job, after
flake 10): a PDF goes to a download panel, downloads are attachments, and a missing signing secret
answers SYS-002. Its staging suites passed on `1309a0b`, the worker's on a re-run alone on
2026-11-03, `260 passed, 8 skipped in 2171.77s (0:36:11)`. On 2026-11-02 the worker's
`test_C1_property_random_decimals_survive_model_db_edit_approve_and_every_export` hit a Storage 503
in its clean-up, a staging-suite failure, not a CI flake (D-229 items 1 to 3). **Suites run with no
VPN connection on this PC** (founder, 2026-11-03; D-229 item 4): on 2026-11-02, with NordVPN
connected, the worker and API suites took about 1.8 times their 2026-10-27 times; the cause is not
proved and won't be investigated. **Next, in order (D-228 item 7):** A2, then A3 to A6, with A5
absorbing the 375 px fix. B1, the inbound-mail item, whose place is decided after its sizing and may move after M10 and
D-195 change 1. M10, with D-195 change 1 straight after it and M10's model-ID part folded into
change 1. Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder), CLAUDE.md
naming the three staging apps, and RUNBOOK 1.4's hand-started dev parse service checked against the
suites that use it (D-229 item 6). **The 2026-10-28 environments can be deleted**
(`DocFlow-evidence/2026-10-28-venv-rebuild/`), now that all three suites have passed on the new
ones; the founder decides when (D-229 item 8). The production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0039. `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-03 (A1 merged at 91d4962; the worker's re-run passed; suites with no VPN; flake 10)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-02, with Blocker 3 confirmed, staging shut down and the viewer's decisions recorded:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release v12,
`deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The API's bind fix is merged** (PR #89, `main` is
`56e00f3`; D-227; main's push run #507 green): PR run #506's API tests read `779 passed, 3
deselected, 1 warning in 87.77s (0:01:27)`, every leftover count +0. The API's image build and
listen step took 52 s and 6 s on #506, 56 s and 5 s on #505, and 48 s and 5 s on #507. **The API is
redeployed to staging** (2026-11-02): release v3, `deployment-01M6EBY8KNF3TAE702XSMSWWN4`; rollback
is v2's image, `deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`. **Blocker 3 is confirmed (2026-11-02):** the
commit was cut at about 60 s with no rollback. All 20,000 items are correct, at 292 items a second,
but the browser showed 409 IMP-006 for a commit that succeeded in 68.43 s. A second POST arrived
60.06 s after the first, from the browser or Fly's edge, not proved which; the founder pressed
Commit once. Its fix is before-first-pilot (D-228 item 7). Step 2b did not run: the commit wasn't a
clean 200 (founder). **An observation, cause not proved:** on a newly allocated shared IPv4, curl
got TLS connect errors (exit 35) for about 30 s, 15:17:43Z to 15:18:12Z, before the edge served it.
They failed before the edge reached the machine, outside the system under test, so they weren't a
stop (founder). The next time a gate retries, curl's error text is captured with `-sS`. **The
partner walkthrough is postponed until A4 is on staging** (founder, 2026-11-02; D-228 item 8), and
its step is cancelled. **The founder's viewer decisions are D-228.** **Next, in order (D-228 item
7):** A1, in progress on `phase55/stage5-viewer-a1`, then A2 to A6, with A5 absorbing the 375 px
fix. B1, the inbound-mail item, whose place is decided after its sizing and may move after M10 and
D-195 change 1. M10, with D-195 change 1 straight after it and M10's model-ID part folded into
change 1. Blocker 3's background commit with the duplicate SKU index (0041). Counting rows before
extraction. E1, then E2a. The staging API's redeploy, and the worker's again if those changed it,
with the staging suites and D-209's smoke test, owed since the worker's deploy of 2026-11-01 (there
is no demo tenant yet, and it needs a model call). The docs PR, which ends Stage 5 (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder). The
production environment. Demo content: both demo tenants, seeded history, a reset procedure, the
synthetic POs, the accuracy bar set before anything is measured, and the D-195 comparison. Two
rehearsals from every machine stopped, fastener first, then welding, the smoke test, tag demo-1.
Then the independent review, then pilots. **To investigate later, not now (founder, 2026-10-27):**
CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but
under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes
per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. **Staging:** the
database has 0039. `docflow-api-staging` runs v3, is stopped, and has no address: 66.241.124.71 was
released at 15:40:14Z on 2026-11-02. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow
13.0.0), is stopped, and stays private. `docflow-worker-staging` is on v12 with no machines: worker
and beat were destroyed in the shutdown of 2026-11-02. Tenant `a7492bf7` still holds the 20,000 test
items and import `96bc7e0c`. Four `tenant_ready_to_delete` alerts are open, and the leftover line's
start is tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users with no
tenant 15. The Healthchecks.io check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-02 (the API redeployed, Blocker 3 confirmed, staging shut down; the viewer's decisions in D-228)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-11-01, with the dev-tools PR merged, the staging worker deployed and the API's
bind fix in its PR:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **The dev-tools PR is merged** (PR #88, `main` is `4cadcaa`; D-225; main's push run
#504 green, its image check 15 s on api and 12 s on worker; PR run #503's API tests read `774 passed,
3 deselected, 1 warning in 89.97s (0:01:29)`, the same starlette warning, and its image check took
23 s and 17 s): the dev tools are out of the API's and the worker's images, openpyxl and lxml are out
of the API's, and the local environments are rebuilt on Python 3.13.17. **The founder's decisions of
2026-10-28 and 2026-10-29 are D-226**, which changes D-199's order. **Rule (a) has dropped:** the
Memurai restart task runs at sign-in since 2026-10-29, so a suite needs only PONG and an uptime under
9 days (RUNBOOK 1.4). **The worker is deployed to staging** (2026-11-01; D-227 item 2): `4cadcaa`, release
v12, `deployment-01M6C0E3KRZKC7DSMAKJA002PZ`. **The Blocker 3 test stopped at its first request
(2026-11-01; D-227 item 3):** Fly's edge can't reach the API, because `uvicorn --host ::` listens on
IPv6 alone, so `/healthz` through a public address read 503. No catalog went through it, so the cut
is still unmeasured. **Next, in order (D-226 item 5, changed by D-227):** the API's bind fix (open;
D-227), then the staging API's redeploy, under a Grant, without the suites, then the Blocker 3 test
again. Then M10, with D-195 change 1 straight after it and M10's model-ID part folded into change 1.
If Blocker 3 is confirmed, its fix, with the duplicate SKU index in the same migration. The 375 px
fix, then counting rows before extraction. E1, then E2a. The staging API's redeploy, and the worker's
again if those changed it, with the staging suites and D-209's smoke test, owed since the worker's
deploy of 2026-11-01 (there is no demo tenant yet, and it needs a model call). The docs PR, which ends
Stage 5 (it also takes `apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##`
when it started on 2026-10-27: either commit that heading or stop `next dev` from rewriting the file;
founder). The production environment. Demo content: both demo tenants, seeded history, a reset
procedure, the synthetic POs, the accuracy bar set before anything is measured, and the D-195
comparison. Two rehearsals from every machine stopped, fastener first, then welding, the smoke test,
tag demo-1. Then the independent review, then pilots. **To investigate later, not now (founder,
2026-10-27):** CI's "Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489
and #492 but under 30 seconds on main's push run #490, likely a cache miss on PR runs, costing about
10 minutes per PR. On main's push run #498 (`d053cca`) it took 16 seconds. The 50,000-row cap and
IMP-003's wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs.
**Staging:** the database has 0039. `docflow-api-staging` runs v2, `ea6a988`'s image, and has no
address. `docflow-parse-staging` runs PR B's image (`b1af531`; Pillow 13.0.0); Fly moved its machine
to another host on 2026-10-29, and its canary passed there on 2026-11-01. `docflow-worker-staging`
runs v12, and its worker and beat machines are running for the founder's walkthrough, then scaled to
0. The first sweep raised four `tenant_ready_to_delete` alerts, which are open, so the leftover
line's start is now tenants 34, founder alerts 8, e-mails 34, scheduled jobs 35, admin rows 12, users
with no tenant 15. The API and parse machines are stopped, with auto-stop on. The Healthchecks.io
check is live while the worker runs, and paused when it stops.

## Replaced on 2026-11-01 (#88 merged at 4cadcaa; the worker deployed; the bind fix in its PR)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-28, with the warnings PR merged and the dev-tools PR open:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, at `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The warnings PR is merged**
(PR #87, `main` is `8d31b07`; main's push run #501 green, the API's tests `774 passed, 3 deselected,
1 warning in 64.38s (0:01:04)`, the one warning starlette's DeprecationWarning from
`testclient.py:53`, the `anyio.abc.BlockingPortal` alias): the API's test signing keys are at least
32 bytes and `InsecureKeyLengthWarning` fails a test; the two busy-retry `test_parse_client.py`
tests run on a fake clock, and a new test checks that a slow busy answer uses up the patience
(D-212 item 8). A PR that changes only tests runs the staging suites only if the staging
environment can change those tests' results in a way CI can't show (D-223 item 5). **The CI infrastructure flake
count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **Next, in order:** the dev-tools PR (open; D-225): the dev tools out of the API's and the worker's
images (D-219 item 7), openpyxl and lxml out of the API's, and the local API, worker and core
environments rebuilt on Python 3.13.17 from the locks and the new dev files; then the staging worker deploy (D-209) and the
API's redeploy, after that PR, so each app deploys once (founder); then M10 without the model IDs,
the 375 px fix, then counting rows before extraction, and the docs PR last (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder). Then E1
and E2a, then demo readiness (D-209). **To investigate later, not now (founder, 2026-10-27):** CI's
"Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but under
30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes per PR.
On main's push run #498 (`d053cca`) it took 16 seconds. The long catalog commit's test through a
public address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots,
only if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the fastener
rehearsal and never blocks it (D-208). **Staging:** the database has 0039. `docflow-api-staging`
runs v2, `ea6a988`'s image; no app code changed between `ea6a988` and `8d31b07`.
`docflow-parse-staging` runs PR B's image (Pillow 13.0.0). `docflow-worker-staging` runs `e95b095`'s
image (D-212) with no machines; certifi 2026.10.19 and M7's code reach it at its next deploy. **That
staging worker deploy is owed under D-209 (staging is redeployed after a merge, then the demo smoke
test) before anything relies on staging's worker, and it needs a Grant.** The API and parse
machines are stopped, with auto-stop on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-28 (#87 merged at 8d31b07; the dev-tools PR opened)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-27, with the sweep script merged and the warnings PR open:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, at `ea6a988`; D-223; main's push
run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK 11's
check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging suites
only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged). **The
sweep script is merged** (PR #86, `main` is `d053cca`; D-224; main's push run #498 green):
`scripts/sweep_test_tenants.py` lists old "Acme Test" tenants read-only and deletes one at a time
under a Grant, after a backup (RUNBOOK 12); the proof delete on staging removed one tenant, by id
only (D-224 item 12). The three staging suites passed on its head, the API's on a re-run alone: in
the parallel run `test_for_cause_is_immediate` took 15.49 s against its 5 s limit, a staging-suite
timing failure, not a CI flake (D-224 item 13). The sweep's prefix match is case-sensitive, so a
tenant named in lower case is never listed or swept (D-224 item 14). **The CI infrastructure flake
count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice install timing out at 15 min on
2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** Any change to a granted command, a flag
such as `--app` included, is told to the founder before it runs (founder, 2026-10-27). **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **Next, in order:** the warnings and the 32-byte test secret (open), with the two
thin-margin `test_parse_client.py` tests (D-212 item 8, measured first, on the current local
environment) and `InsecureKeyLengthWarning` made an error in the API's tests; then one PR with the
dev tools out of the API's and the worker's images (D-219 item 7, before `docflow-prod` exists) and,
after them, the local API environment built once from the lock and the new dev files (it holds 107
packages against the lock's 78 pins, certifi installed from a file among them, so the staging suites
don't run what ships; founder, 2026-10-27, D-223); then the staging worker deploy (D-209) and the
API's redeploy, after that PR, so each app deploys once (founder); then M10 without the model IDs,
the 375 px fix, then counting rows before extraction, and the docs PR last (it also takes
`apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started on
2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder). Then E1
and E2a, then demo readiness (D-209). **To investigate later, not now (founder, 2026-10-27):** CI's
"Install Playwright browser" step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but under
30 seconds on main's push run #490, likely a cache miss on PR runs, costing about 10 minutes per PR.
On main's push run #498 (`d053cca`) it took 16 seconds. The long catalog commit's test through a
public address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots,
only if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the fastener
rehearsal and never blocks it (D-208). **Staging:** the database has 0039. `docflow-api-staging`
runs v2, `ea6a988`'s image; no app code changed between `ea6a988` and `d053cca`.
`docflow-parse-staging` runs PR B's image (Pillow 13.0.0). `docflow-worker-staging` runs `e95b095`'s
image (D-212) with no machines; certifi 2026.10.19 and M7's code reach it at its next deploy. **That
staging worker deploy is owed under D-209 (staging is redeployed after a merge, then the demo smoke
test) before anything relies on staging's worker, and it needs a Grant.** The API and parse
machines are stopped, with auto-stop on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-27 (#86 merged at d053cca; the warnings PR opened)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-27, with the dnspython fix merged, the API deployed and the sweep script open:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, at `3e021a6`; D-220; main's push run #490 green): a
Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is merged** (PR #85, `main` is `ea6a988`; D-223; main's
push run #493 green): GHSA-45c3-73f7-pv4m in dnspython 2.8.0 had stopped the API's deploy at RUNBOOK
11's check; the API's lock moved to 2.9.0 and nothing else moved. A lock-only PR runs the staging
suites only when the changed package is on a path the suites exercise (founder, D-223). **The API is
deployed to `docflow-api-staging`** (release v2, `ea6a988`'s image
`deployment-01M5Z5Z0ER07M6JHKGSBKAA8XA`; rollback is v1's image
`deployment-01M48YSNPWSEXNDWZ3NEERM5QA`), and one refusal was recorded live through it: row
`036931c2-37e0-4b10-9b2c-0132c3563242`, AUTH-007, the code 460 s old (D-220 item 11). `fly proxy`
traffic doesn't keep the API machine up: a staging step through the proxy starts the machine and
expects it to stop after a few idle minutes (founder; RUNBOOK 11.2; fly.toml unchanged).
**The CI infrastructure flake count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice
install timing out at 15 min on 2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing
rule (D-213 item 6, tightened in D-214): a staging suite, a Fly action or any write to staging
starts only after a message from the founder that begins with "Grant" and names the step.** Any
change to a granted command, a flag such as `--app` included, is told to the founder before it runs
(founder, 2026-10-27). **Two new rules:** SQL the founder runs on staging is first checked by Claude
read-only (D-214 item 15), and before a file outside one app is changed, every reader of it is found
and its tests are run (D-216 item 6). **Next, in order:** the sweep script (open, D-224); then the
warnings and the 32-byte test secret (with the two thin-margin `test_parse_client.py` tests, D-212
item 8, and the dev tools out of the API's and the worker's images, D-219 item 7, before
`docflow-prod` exists, and the local API environment brought in line with its lock: it holds 107
packages against the lock's 78 pins, certifi installed from a file among them, so the staging suites
don't run what ships; founder, 2026-10-27, D-223), M10 without the model IDs, the 375 px fix, then
counting rows before extraction, and the docs PR last (it also takes `apps/web/AGENTS.md`, whose
first heading `next dev` rewrote from `#` to `##` when it started on 2026-10-27: either commit that
heading or stop `next dev` from rewriting the file; founder). Then E1 and E2a, then demo readiness
(D-209). **To investigate later, not now (founder, 2026-10-27):** CI's "Install Playwright browser"
step (web-live) took 9 to 11 minutes on PR runs #489 and #492 but under 30 seconds on main's push
run #490, likely a cache miss on PR runs, costing about 10 minutes per PR. The long catalog commit's
test through a public address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR,
before pilots, only if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the
fastener rehearsal and never blocks it (D-208). **Staging:** the database has 0039.
`docflow-api-staging` runs v2, `ea6a988`'s image. `docflow-parse-staging` runs PR B's image (Pillow
13.0.0). `docflow-worker-staging` runs `e95b095`'s image (D-212) with no machines; certifi
2026.10.19 and M7's code reach it at its next deploy. **That staging worker deploy is owed under
D-209 (staging is redeployed after a merge, then the demo smoke test) before anything relies on
staging's worker, and it needs a Grant.** The API and parse machines are stopped, with auto-stop on,
and the Healthchecks.io check is paused.

## Replaced on 2026-10-27 (#85 merged at ea6a988; the API deployed, v2; the sweep script opened, D-224)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-27, with D-178 merged and the dnspython fix open:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83; D-222): parse's LibreOffice
.debs are cached under a key that follows the runner image, each one checked against the signed
package lists before apt uses it; `main`'s cache is saved and every pull request restores from it
(on 2026-10-27 a new runner image restored the older image's entry and saved one under its own key,
as designed). **D-178 is merged** (PR #84, `main` is `3e021a6`; D-220; main's push run #490 green):
a Console request refused with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the
5th raises a high alert, the rest are logged), and every Console write records its outcome, so the
Audit tab says "Outcome unknown" rather than showing a stopped request as done. The step-up
measurement on staging gave 0 AUTH-006 and 1 AUTH-007, so the 5-in-15-minute threshold stays (D-220
item 7), and the three staging suites passed on its head with the six counts +0. Supabase limits MFA
code checks per IP only (GoTrue v2.197.0, no per-account lockout): per-account protection is a gate
before the first pilot. **The dnspython fix is open** (D-223): GHSA-45c3-73f7-pv4m (CVE-2026-114605,
high, published 2026-10-27) in dnspython 2.8.0 stopped the API's deploy at RUNBOOK 11's check. The
API's lock moves to 2.9.0 and nothing else moves; DocFlow's code never reaches dnspython (pydantic's
`EmailStr` validates without deliverability checks). It runs no staging suites: a lock-only PR runs
them only when the changed package is on a path the suites exercise (founder, D-223).
**The CI infrastructure flake count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice
install timing out at 15 min on 2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing
rule (D-213 item 6, tightened in D-214): a staging suite, a Fly action or any write to staging
starts only after a message from the founder that begins with "Grant" and names the step.** **Two
new rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15),
and before a file outside one app is changed, every reader of it is found and its tests are run
(D-216 item 6). **Next, in order:** the dnspython fix (above); then the API deploy to
`docflow-api-staging`, confirming one recorded refusal live (D-220's plan; its own Grant); then the
sweep script (its plan approved 2026-10-27), the warnings and the 32-byte test secret (with the two
thin-margin `test_parse_client.py` tests, D-212 item 8, and the dev tools out of the API's and the
worker's images, D-219 item 7, before `docflow-prod` exists, and the local API environment brought
in line with its lock: it holds 107 packages against the lock's 78 pins, certifi installed from a
file among them, so the staging suites don't run what ships; founder, 2026-10-27, D-223), M10
without the model IDs, the 375 px fix, then counting rows before extraction, and the docs PR last
(it also takes `apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it
started on 2026-10-27: either commit that heading or stop `next dev` from rewriting the file;
founder). Then E1 and E2a, then demo readiness (D-209). The long catalog commit's test through a
public address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots,
only if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the fastener
rehearsal and never blocks it (D-208). **Staging:** the database has 0039. `docflow-api-staging`
runs v1, 2026-10-06's image. `docflow-parse-staging` runs PR B's image (Pillow 13.0.0).
`docflow-worker-staging` runs `e95b095`'s image (D-212) with no machines; certifi 2026.10.19 and
M7's code reach it at its next deploy. **That staging worker deploy is owed under D-209 (staging is
redeployed after a merge, then the demo smoke test) before anything relies on staging's worker, and
it needs a Grant.** The API and parse machines are stopped, with auto-stop on, and the
Healthchecks.io check is paused.

## Replaced on 2026-10-27 (#84 merged at 3e021a6; the dnspython fix opened, D-223)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-27, with the LibreOffice cache PR merged and D-178's step-up measured:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, and its first scheduled run, 2026-10-27, both green); and a check before every
deploy (RUNBOOK 11). **The LibreOffice cache PR is merged** (PR #83, `main` is `051000c`; D-222):
parse's LibreOffice .debs are cached under a key that follows the runner image, each one checked
against the signed package lists before apt uses it; `main`'s cache is saved and every pull request
restores from it. **D-178 is PR #84** (D-220; its runs green on `a6d6b23` and on `954f0ef`, the
request row first for the seven same-transaction actions, D-220 item 10): a Console request refused
with AUTH-006 or AUTH-007 is recorded (5 rows per account per 15 min, the 5th raises a high alert,
the rest are logged), and every Console write records its outcome, so the Audit tab says "Outcome
unknown" rather than showing a stopped request as done. Supabase limits MFA code checks per IP only
(GoTrue v2.197.0, no per-account lockout): per-account protection is a gate before the first pilot.
**The step-up measurement is done** (2026-10-27, under the founder's Grant; D-220 item 7): a normal
sign-in and one stale step-up against staging gave 0 AUTH-006 and 1 AUTH-007, recorded with the
request row and a failed QUA-004 outcome, and no alert; the 5-in-15-minute threshold stays as
approved (founder). **What remains for PR #84, in order:** the three staging suites on its head,
under a Grant; then the merge, once they pass with the six counts +0 and that head's pull-request
run is green. No docs commit follows the suites; the status after the merge goes in the next PR.
**The CI infrastructure flake count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice
install timing out at 15 min on 2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing
rule (D-213 item 6, tightened in D-214): a staging suite, a Fly action or any write to staging
starts only after a message from the founder that begins with "Grant" and names the step.** **Two
new rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15),
and before a file outside one app is changed, every reader of it is found and its tests are run
(D-216 item 6). **Next, in order:** D-178 (above), the sweep script, the warnings and the 32-byte
test secret (with the two thin-margin `test_parse_client.py` tests, D-212 item 8, and the dev tools
out of the API's and the worker's images, D-219 item 7, before `docflow-prod` exists), M10 without
the model IDs, the 375 px fix, then counting rows before extraction, and the docs PR last (it also
takes `apps/web/AGENTS.md`, whose first heading `next dev` rewrote from `#` to `##` when it started
on 2026-10-27: either commit that heading or stop `next dev` from rewriting the file; founder). Then E1
and E2a, then demo readiness (D-209). The long catalog commit's test through a public address needs
its own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only if a pilot
prospect has more than 50,000 SKUs. The welding demo comes after the fastener rehearsal and never
blocks it (D-208). **Staging:** the database has 0039. `docflow-parse-staging` runs PR B's image
(Pillow 13.0.0). `docflow-worker-staging` runs `e95b095`'s image (D-212) with no machines; certifi
2026.10.19 and M7's code reach it at its next deploy. **That staging worker deploy is owed under
D-209 (staging is redeployed after a merge, then the demo smoke test) before anything relies on
staging's worker, and it needs a Grant.** The API and parse machines are stopped, with auto-stop
on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-27 (the step-up measured, D-220 item 7)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-27, with the LibreOffice cache PR merged and D-178 open as PR #84:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, green); and a check before every deploy (RUNBOOK 11). **The LibreOffice cache PR
is merged** (PR #83, `main` is `051000c`; D-222): parse's LibreOffice .debs are cached under a key
that follows the runner image, each one checked against the signed package lists before apt uses
it; `main`'s cache is saved and every pull request restores from it. **D-178 is PR #84, not merged**
(D-220; first runs green on `a6d6b23`, both restoring `main`'s cache; the request row first for
the seven same-transaction actions, D-220 item 10, pushed after them): a Console request refused with AUTH-006 or
AUTH-007 is recorded (5 rows per account per 15 min, the 5th raises a high alert, the rest are
logged), and every Console write records its outcome, so the Audit tab says "Outcome unknown"
rather than showing a stopped request as done. Supabase limits MFA code checks per IP only
(GoTrue v2.197.0, no per-account lockout): per-account protection is a gate before the first pilot.
**The CI infrastructure flake count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice
install timing out at 15 min on 2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing
rule (D-213 item 6, tightened in D-214): a staging suite, a Fly action or any write to staging
starts only after a message from the founder that begins with "Grant" and names the step.** **Two
new rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15),
and before a file outside one app is changed, every reader of it is found and its tests are run
(D-216 item 6). **Next, in order:** D-178 (D-220: its CI runs, then the step-up measurement and the
three staging suites, each under a Grant), the sweep script, the warnings and the 32-byte test
secret (with the two thin-margin `test_parse_client.py` tests, D-212 item 8, and the dev tools out
of the API's and the worker's images, D-219 item 7, before `docflow-prod` exists), M10 without the
model IDs, the 375 px fix, then counting rows before extraction, and the docs PR last. Then E1 and
E2a, then demo readiness (D-209). The long catalog commit's test through a public address needs its
own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only if a pilot prospect
has more than 50,000 SKUs. The welding demo comes after the fastener rehearsal and never blocks it
(D-208). **Staging:** the database has 0039. `docflow-parse-staging` runs PR B's image (Pillow
13.0.0). `docflow-worker-staging` runs `e95b095`'s image (D-212) with no machines; certifi
2026.10.19 and M7's code reach it at its next deploy. **That staging worker deploy is owed under
D-209 (staging is redeployed after a merge, then the demo smoke test) before anything relies on
staging's worker, and it needs a Grant.** The API and parse machines are stopped, with auto-stop
on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-27 (D-178 opened as PR #84; its first runs; the request row first, D-220 item 10)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-27, with the LibreOffice cache PR merged and D-178 built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82; D-219): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions; pip-audit 2.10.1 audits each app's requirements files; a daily audit of `main` (its
first run, by hand, green); and a check before every deploy (RUNBOOK 11). **The LibreOffice cache PR
is merged** (PR #83, `main` is `051000c`; D-222): parse's LibreOffice .debs are cached under a key
that follows the runner image, each one checked against the signed package lists before apt uses
it; `main`'s cache is saved and every pull request restores from it. **D-178 is built, not merged**
(D-220; branch `phase55/stage5-d178-audit-trail`): a Console request refused with AUTH-006 or
AUTH-007 is recorded (5 rows per account per 15 min, the 5th raises a high alert, the rest are
logged), and every Console write records its outcome, so the Audit tab says "Outcome unknown"
rather than showing a stopped request as done. Supabase limits MFA code checks per IP only
(GoTrue v2.197.0, no per-account lockout): per-account protection is a gate before the first pilot.
**The CI infrastructure flake count is 9** (D-199 item 10; flakes 5 to 9 are parse's LibreOffice
install timing out at 15 min on 2026-10-26, D-219 items 8 and 9, D-222 items 8 and 9). **A standing
rule (D-213 item 6, tightened in D-214): a staging suite, a Fly action or any write to staging
starts only after a message from the founder that begins with "Grant" and names the step.** **Two
new rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15),
and before a file outside one app is changed, every reader of it is found and its tests are run
(D-216 item 6). **Next, in order:** D-178 (D-220: its CI runs, then the step-up measurement and the
three staging suites, each under a Grant), the sweep script, the warnings and the 32-byte test
secret (with the two thin-margin `test_parse_client.py` tests, D-212 item 8, and the dev tools out
of the API's and the worker's images, D-219 item 7, before `docflow-prod` exists), M10 without the
model IDs, the 375 px fix, then counting rows before extraction, and the docs PR last. Then E1 and
E2a, then demo readiness (D-209). The long catalog commit's test through a public address needs its
own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only if a pilot prospect
has more than 50,000 SKUs. The welding demo comes after the fastener rehearsal and never blocks it
(D-208). **Staging:** the database has 0039. `docflow-parse-staging` runs PR B's image (Pillow
13.0.0). `docflow-worker-staging` runs `e95b095`'s image (D-212) with no machines; certifi
2026.10.19 and M7's code reach it at its next deploy. **That staging worker deploy is owed under
D-209 (staging is redeployed after a merge, then the demo smoke test) before anything relies on
staging's worker, and it needs a Grant.** The API and parse machines are stopped, with auto-stop
on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-27 (the LibreOffice cache PR merged; D-178 built; D-222, D-220)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-26, with the audit PR merged and the LibreOffice cache PR built:** Phase 5.5,
**Stage 5**. **M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts
(review backlog, confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5.
CLAUDE.md has the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots
matched and the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder
drops it. **Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is
merged** (PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories,
each fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI
PR is merged** (PR #81; D-218): the port-54322 before/after steps in the four jobs that start the
Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and parse's LibreOffice
install (15 min). **The audit PR is merged** (PR #82, `main` is `433d14b`; D-219): a known
vulnerability in what an app ships blocks only when that app's dependency files changed, a dev-only
one warns, and there are no exceptions; pip-audit 2.10.1 audits each app's requirements files; a
daily audit of `main`; and a check before every deploy (RUNBOOK 11). `main`'s push run on it is
green, and so is the daily audit's first run, started by hand (D-219 item 9). **The LibreOffice
cache PR is built, not merged** (PR #83; D-222; branch `phase55/stage5-libreoffice-deb-cache`):
parse's LibreOffice .debs are cached under a key that follows the runner image, each one is checked
against the signed package lists before apt uses it, and a miss downloads as before. **The CI
infrastructure flake count is 8** (D-199 item 10; flakes 5 to 8 are parse's LibreOffice install
timing out at 15 min on 2026-10-26, D-219 items 8 and 9 and D-222 item 8). **A standing rule (D-213 item 6, tightened
in D-214): a staging suite, a Fly action or any write to staging starts only after a message from
the founder that begins with "Grant" and names the step.** **Two new rules:** SQL the founder runs
on staging is first checked by Claude read-only (D-214 item 15), and before a file outside one app
is changed, every reader of it is found and its tests are run (D-216 item 6). **Next, in order:**
the cache PR (D-222), D-178 (D-220; the founder's Supabase Auth screenshots are in, and two
read-only checks come first: whether Supabase's auth server limits MFA code verification, and
whether sign-in and MFA verification reach Supabase from the browser or through our API), the
sweep script, the warnings and the 32-byte test secret (with the two thin-margin
`test_parse_client.py` tests, D-212 item 8, and the dev tools out of the API's and the worker's
images, D-219 item 7, before `docflow-prod` exists), M10 without the model IDs, the 375 px fix, then
counting rows before extraction, and the docs PR last. Then E1 and E2a, then demo readiness (D-209).
The long catalog commit's test through a public address needs its own grant. The 50,000-row cap and
IMP-003's wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. The
welding demo comes after the fastener rehearsal and never blocks it (D-208). **Staging:** the
database has 0039. `docflow-parse-staging` runs PR B's image (Pillow 13.0.0).
`docflow-worker-staging` runs `e95b095`'s image (D-212) with no machines; certifi 2026.10.19 and
M7's code reach it at its next deploy. **That staging worker deploy is owed under D-209 (staging is
redeployed after a merge, then the demo smoke test) before anything relies on staging's worker, and
it needs a Grant.** The API and parse machines are stopped, with auto-stop on, and the
Healthchecks.io check is paused.

## Replaced on 2026-10-26 (the audit PR merged; the LibreOffice cache PR built; D-219, D-222)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-26, with the CI PR merged and the audit PR built:** Phase 5.5, **Stage 5**.
**M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts (review backlog,
confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5. CLAUDE.md has
the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots matched and
the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder drops it.
**Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. **zod 4.6.6 is merged**
(PR #79; D-217) and **undici 7.30.1 is merged** (PR #80; D-221): new dev-only advisories, each fixed
in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR #77). **The CI PR is
merged** (PR #81, `main` is `7d374e6`; D-218): the port-54322 before/after steps in the four jobs
that start the Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every use) and
parse's LibreOffice install (15 min); `main`'s push run on it is green. **The audit PR is built, not
merged** (D-219; branch `phase55/stage5-dependency-audit`): a known vulnerability in what an app
ships blocks only when that app's dependency files changed, a dev-only one warns, and there are no
exceptions (the braces exception goes with the file); pip-audit 2.10.1 audits each app's
requirements files; a daily audit of `main`; and a check before every deploy (RUNBOOK 11). **The CI
infrastructure flake count is 4** (D-199 item 10; flake 4 is D-221 item 4). **A standing rule
(D-213 item 6, tightened in D-214): a staging suite, a Fly action or any write to staging starts
only after a message from the founder that begins with "Grant" and names the step.** **Two new
rules:** SQL the founder runs on staging is first checked by Claude read-only (D-214 item 15), and
before a file outside one app is changed, every reader of it is found and its tests are run (D-216
item 6). **Next, in order:** the audit PR (D-219), D-178 (D-220), the sweep script, the warnings and
the 32-byte test secret (with the two thin-margin `test_parse_client.py` tests, D-212 item 8, and
the dev tools out of the API's and the worker's images, D-219 item 7, before `docflow-prod`
exists), M10 without the model IDs, the 375 px fix, then counting rows before extraction, and the
docs PR last. Then E1 and E2a, then demo readiness (D-209). The long catalog commit's test through
a public address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR, before
pilots, only if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the
fastener rehearsal and never blocks it (D-208). **Staging:** the database has 0039.
`docflow-parse-staging` runs PR B's image (Pillow 13.0.0). `docflow-worker-staging` runs
`e95b095`'s image (D-212) with no machines; certifi 2026.10.19 and M7's code reach it at its next
deploy. **That staging worker deploy is owed under D-209 (staging is redeployed after a merge, then
the demo smoke test) before anything relies on staging's worker, and it needs a Grant.** The API
and parse machines are stopped, with auto-stop on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-26 (the CI PR merged; the audit PR built; D-218, D-219)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-26, with the undici fix merged and the CI PR built:** Phase 5.5, **Stage 5**.
**M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts (review backlog,
confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5. CLAUDE.md has
the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots matched and
the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder drops it.
**Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. The withdrawn braces
exception stays on file until the audit PR (D-219). **zod 4.6.6 is merged** (PR #79; D-217): one new
dev-only advisory, fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR
#77). **undici 7.30.1 is merged** (PR #80, `main` is `959d035`; D-221): one new dev-only advisory,
fixed in the lock file in its own PR; `main`'s push run on it is green. **The CI PR is built, not
merged** (D-218; branch `phase55/stage5-ci-port-libreoffice`): the port-54322 before/after steps in
the four jobs that start the Supabase stack, and step timeouts on `supabase/setup-cli` (5 min, every
use) and parse's LibreOffice install (15 min). **The CI infrastructure flake count is 4** (D-199 item
10; flake 4 is D-221 item 4). **A standing rule (D-213 item 6, tightened in D-214): a staging suite,
a Fly action or any write to staging starts only after a message from the founder that begins with
"Grant" and names the step.** **Two new rules:** SQL the founder runs on staging is first checked by
Claude read-only (D-214 item 15), and before a file outside one app is changed, every reader of it
is found and its tests are run (D-216 item 6). **Next, in order:** the CI PR (D-218), the audit PR
(D-219, with the braces exception's removal), D-178 (D-220), the sweep script, the warnings and the
32-byte test secret (with the two thin-margin `test_parse_client.py` tests, D-212 item 8), M10
without the model IDs, the 375 px fix, then counting rows before extraction, and the docs PR last.
Then E1 and E2a, then demo readiness (D-209). The long catalog commit's test through a public
address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only
if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the fastener rehearsal
and never blocks it (D-208). **Staging:** the database has 0039. `docflow-parse-staging` runs PR
B's image (Pillow 13.0.0). `docflow-worker-staging` runs `e95b095`'s image (D-212) with no
machines; certifi 2026.10.19 and M7's code reach it at its next deploy. **That staging worker
deploy is owed under D-209 (staging is redeployed after a merge, then the demo smoke test) before
anything relies on staging's worker, and it needs a Grant.** The API and parse machines are
stopped, with auto-stop on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-26 (the undici fix merged; the CI PR built; D-218, D-221)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-26, with D-215 merged and the undici fix built:** Phase 5.5, **Stage 5**.
**M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts (review backlog,
confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5. CLAUDE.md has
the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots matched and
the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder drops it.
**Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. The withdrawn braces
exception stays on file until its own core PR. **zod 4.6.6 is merged** (PR #79; D-217): one new
dev-only advisory, fixed in the lock file. **D-215 (CLAUDE.md capped at 28k tokens) is merged** (PR
#77, `main` is `089296c`). **`main`'s push run on it is red at the web audit only:** a new dev-only
undici advisory (D-221). **The undici fix is built, not merged** (D-221; branch
`phase55/stage5-undici-7.30.1`): undici 7.30.1 in the web lock file, in its own PR so `main` turns
green without waiting on the CI PR. **The CI infrastructure flake count is 3** (D-214 item 13). **A
standing rule (D-213 item 6, tightened in D-214): a staging suite, a Fly action or any write to
staging starts only after a message from the founder that begins with "Grant" and names the
step.** **Two new rules:** SQL the founder runs on staging is first checked by Claude read-only
(D-214 item 15), and before a file outside one app is changed, every reader of it is found and its
tests are run (D-216 item 6). **Next, in order:** the undici PR (D-221), the CI PR (D-218: the
port-54322 step, D-214 item 13, and a 15-minute timeout on parse's LibreOffice install), the audit
PR (D-219), D-178 (D-220), the sweep script, the warnings and the 32-byte test secret (with the two
thin-margin `test_parse_client.py` tests, D-212 item 8, and the braces exception's core PR, D-216
item 4), M10 without the model IDs, the 375 px fix, then counting rows before extraction, and the
docs PR last. Then E1 and E2a, then demo readiness (D-209). The long catalog commit's test through a
public address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots,
only if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the fastener
rehearsal and never blocks it (D-208). **Staging:** the database has 0039.
`docflow-parse-staging` runs PR B's image (Pillow 13.0.0). `docflow-worker-staging` runs
`e95b095`'s image (D-212) with no machines; certifi 2026.10.19 and M7's code reach it at its next
deploy. **That staging worker deploy is owed under D-209 (staging is redeployed after a merge, then
the demo smoke test) before anything relies on staging's worker, and it needs a Grant.** The API
and parse machines are stopped, with auto-stop on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-26 (D-215 merged; the undici fix built; D-221)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-23, with M7, Next.js 16.4.1 and zod 4.6.6 merged:** Phase 5.5, **Stage 5**.
**M7 is merged** (PR #76; D-214). The nightly rollup raises the three missing alerts (review backlog,
confidence drift, staging TTL). INT-011 names Postmark's retry API, with RUNBOOK 2.5. CLAUDE.md has
the Grants rules. **Migration 0039 is applied to staging** (2026-10-23): both snapshots matched and
the three staging suites passed (D-214 item 14). `backup_0039` stays until the founder drops it.
**Next.js 16.4.1 is merged** (PR #78; D-216): three new advisories fixed. The withdrawn braces
exception stays on file until its own core PR. **zod 4.6.6 is merged** (PR #79, `main` is
`b5736a2`; D-217): one new dev-only advisory, fixed in the lock file. **The CI infrastructure flake
count is 3** (D-214 item 13). **D-215 (CLAUDE.md capped at 28k tokens) is rebased onto `main`, not
merged** (PR #77). **A standing rule (D-213 item 6, tightened in D-214): a staging suite, a Fly
action or any write to staging starts only after a message from the founder that begins with
"Grant" and names the step.** **Two new rules:** SQL the founder runs on staging is first checked by
Claude read-only (D-214 item 15), and before a file outside one app is changed, every reader of it
is found and its tests are run (D-216 item 6). **Next, in order:** D-178, the sweep script, the
warnings and the 32-byte test secret (with the two thin-margin `test_parse_client.py` tests, D-212
item 8, the port-54322 CI step, D-214 item 13, and the braces exception's core PR, D-216 item 4),
M10 without the model IDs, the 375 px fix, then counting rows before extraction, and the docs PR
last. Then E1 and E2a, then demo readiness (D-209). The long catalog commit's test through a public
address needs its own grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only
if a pilot prospect has more than 50,000 SKUs. The welding demo comes after the fastener rehearsal
and never blocks it (D-208). **Staging:** the database has 0039. `docflow-parse-staging` runs PR B's
image (Pillow 13.0.0). `docflow-worker-staging` runs `e95b095`'s image (D-212) with no machines;
certifi 2026.10.19 and M7's code reach it at its next deploy. **That staging worker deploy is owed
under D-209 (staging is redeployed after a merge, then the demo smoke test) before anything relies
on staging's worker, and it needs a Grant.** The API and parse machines are stopped, with auto-stop
on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-23 (M7 merged; 0039 applied to staging; D-214)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-21, with Stage 5's M7 built:** Phase 5.5, **Stage 5**. **PR B, Pillow
13.0.0 for the parse service, is merged** (PR #75, `main` is `7f3b318`; main's push run
39665677873 green). Its parse Fly gate passed on 2026-10-21 (image `sha256:83d911eb`; D-214 item
10). **M7 is built, not merged** (D-214; branch `phase55/stage5-m7-alerts`). The nightly rollup
raises the three missing alerts (review backlog, confidence drift, staging TTL). INT-011 names
Postmark's retry API, with RUNBOOK 2.5. CLAUDE.md gains the Grants rules. **Migration 0039** shuts
Supabase's Data API roles out of `public` and gives the four Advisor functions an empty
`search_path`. **The founder applies 0039 to staging after the backup and snapshot at the top of the
PR message. The PR doesn't merge until Claude has checked both snapshots and the three staging
suites have passed, each on its own grant.** **A standing rule (D-213 item 6, tightened in D-214):
a staging suite, a Fly action or any write to staging starts only after a message from the founder
that begins with "Grant" and names the step.** **Next, in order, after M7:** D-178, the sweep
script, the warnings and the 32-byte test secret (with the two thin-margin `test_parse_client.py`
tests, D-212 item 8), M10 without the model IDs, the 375 px fix, then counting rows before
extraction, and the docs PR last. Then E1 and E2a, then demo readiness (D-209). The long catalog
commit's test through a public address needs its own grant. The 50,000-row cap and IMP-003's
wording: one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. The welding demo
comes after the fastener rehearsal and never blocks it (D-208). **Staging:** `docflow-parse-staging`
runs PR B's image (Pillow 13.0.0). `docflow-worker-staging` runs `e95b095`'s image (D-212) with no
machines; certifi 2026.10.19 reaches it at its next deploy. The API and parse machines are stopped,
with auto-stop on, and the Healthchecks.io check is paused. The parse gate's evidence is in
`docs/spikes/3c-fly-staging/2026-10-21-sha256-83d911eb/` (D-214 item 10).

## Replaced on 2026-10-21 (Stage 5's PR B merged; the parse gate passed; M7 built; D-214)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-21, with Stage 5's Pillow PR built:** Phase 5.5, **Stage 5**. **PR 3, the
worker-start fix, is merged** (PR #73, `40435a3`). Its Fly proof passed on 2026-10-21: at start 2,
each worker received its first waiting task 0.005 s and 0.058 s after `ready` (D-213 item 4). **A
dependency-audit fix in two PRs comes before M7** (D-213). **PR A is merged** (PR #74, `main` is
`9c4286b`; certifi and vite), on a one-off exception: parse was red only at its audit step. **PR B,
Pillow 13.0.0 for the parse service, is built, not merged** (branch `phase55/stage5-pillow-13`, a
draft PR). After its green pull-request run comes the parse Fly gate: Claude drafts the plan, and
nothing runs without a grant. **A standing rule since a breach on 2026-10-21 (D-213 item 6): a
staging suite, a Fly action or any write to staging starts only after a message from the founder
containing the word "grant".** **Next, in order, after PR B:** M7 (the three missing alerts and
INT-011's wording), D-178, the sweep script, the warnings and the 32-byte test secret (with the two
thin-margin `test_parse_client.py` tests, D-212 item 8), M10 without the model IDs, the 375 px fix,
then counting rows before extraction, and the docs PR last. Then E1 and E2a, then demo readiness
(D-209). The long catalog commit's test through a public address needs its own grant. The 50,000-row
cap and IMP-003's wording: one PR, before pilots, only if a pilot prospect has more than 50,000
SKUs. The welding demo comes after the fastener rehearsal and never blocks it (D-208). **Staging:**
`docflow-parse-staging` runs the image from before PR B (Pillow 12.3.0). `docflow-worker-staging`
runs `e95b095`'s image (D-212) with no machines; certifi 2026.10.19 reaches it at its next deploy.
The API and parse machines are stopped, with auto-stop on, and the Healthchecks.io check is paused.
D-212's evidence is in `docs/spikes/d212-worker-start-fly-proof/` (D-213 item 7).

## Replaced on 2026-10-21 (Stage 5's PR 3 and PR A merged; PR B, Pillow 13, built; D-213)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-19, with Stage 5's worker-start PR built:** Phase 5.5, **Stage 5**. **PR 2,
test hygiene, is merged** (PR #72, `main` is `cdc5c5d`; D-211, with its runs, the parallel proof and
Nano's connection limits in D-212 item 6): RUNBOOK 1.4's parallel rule stays, and no worker suite
starts if it would run past 23:20Z (D-212 item 7). **PR 3, the worker-start fix, is built, not
merged** (D-212; branch `phase55/stage5-worker-start`, a draft PR): both Celery workers start
without mingle and gossip. No staging suites for it (founder). After CI, the PR's head is deployed
as the owed worker deploy and proven on Fly; **the proof gates the merge**, and each Fly step
(deploy, the worker's stop and start, the scale-down) waits for its own grant after the pre-deploy
report (D-212 item 4). **Next, in order, after PR 3:** M7, D-178, the sweep script, the warnings
and the 32-byte test secret (with the two thin-margin `test_parse_client.py` tests, D-212 item 8),
M10 without the model IDs, the 375 px fix, then counting rows before extraction, and the docs PR
last; INT-011's wording goes into the first of them that touches the core catalog. Then E1 and
E2a, then demo readiness (D-209). The long catalog commit's test through a public address waits
until after the worker-start PR; it needs its own grant. The 50,000-row cap and IMP-003's wording:
one PR, before pilots, only if a pilot prospect has more than 50,000 SKUs. The welding demo comes
after the fastener rehearsal and never blocks it (D-208). **Staging:** `docflow-parse-staging`
runs `main`'s image; `docflow-worker-staging` has no machines and is still v10 with the old
`parse_client`, which must not be started against it (read 2026-10-19 22:25Z: worker no machines,
API and parse stopped). Its memory is read straight after the deploy creates the machine
(`fly.toml` says 1 GB, and stays so: D-212 item 5). The API's and the parse machine's auto-stop
are on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-19 (Stage 5's PR 2 merged; PR 3, the worker-start fix, built; D-212)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-19, with Stage 5's test-hygiene PR built:** Phase 5.5, **Stage 5**. **PR 1,
blocker 4, is merged** (PR #71, `main` is `f1d11e0`; D-210 item 9). **PR 2, test hygiene, is built,
not merged** (D-211; branch `phase55/stage5-test-hygiene`, a draft PR). Every suite's conftest now
refuses a database off this machine unless the run carries `DOCFLOW_STAGING_RUN=1`, after two local
runs reached staging's run lock on 2026-10-19 (an incident, D-211 item 1). The counts and the two
deletes that could catch another suite's rows are scoped; the tests that touch the database's global
state hold a shared-state lock; core, worker and API can run together through
`scripts/run_suites_parallel.py`. No product code. It runs CI, the serial staging suites, then one
parallel proof; RUNBOOK 1.4's parallel rule stays only if the proof equals the serial results with
`+0`. **Next, in order:** the worker-start fix with `main`'s worker deploy, M7, D-178, the sweep
script, the warnings and the 32-byte test secret, M10 without the model IDs, the 375 px fix, then
counting rows before extraction, and the docs PR last; INT-011's wording goes into the first of them
that touches the core catalog. Then E1 and E2a, then demo readiness (D-209). The long catalog
commit's test through a public address waits until after the worker-start PR; it needs its own
grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only if a pilot prospect has
more than 50,000 SKUs. The welding demo comes after the fastener rehearsal and never blocks it
(D-208). **Staging:** `docflow-parse-staging` runs `main`'s image;
`docflow-worker-staging` has no machines and is still v10 with the old `parse_client`, which must
not be started against it: deploy `main`'s worker first, read the machine's memory (raised to 2 GB;
`fly.toml` says 1 GB), then start it. The API's and the parse machine's auto-stop are on, and the
Healthchecks.io check is paused.

## Replaced on 2026-10-19 (Stage 5's PR 1 merged; PR 2, test hygiene, built; D-211)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-19, with Stage 5 started:** Phase 5.5, **Stage 5** (the founder's "go" for
it, 2026-10-19; Stage 4's checkpoint record is PR #70, `main` is `ca5a726`). **PR 1, blocker 4, is
built, not merged** (D-210; branch `phase55/stage5-intake-fault-alert`, a draft PR): when the inbound
webhook fails on our side for a known tenant, the keyed `intake_failed` alert (INT-011) is raised once
per state, and the answer is the same 500 as before, so Postmark keeps resending. A fault before the
tenant is known is logged only (the gap is recorded in D-210). No migration. The three staging suites
run on its final head before it is marked ready. **Next, in order:** the test-hygiene PR, the
worker-start fix with `main`'s worker deploy, M7, D-178, the sweep script, the warnings and the
32-byte test secret, M10 without the model IDs, the 375 px fix, then counting rows before
extraction, and the docs PR last. Then E1 and E2a, then demo readiness (D-209). The long catalog
commit's test through a public address waits until after the worker-start PR; it needs its own
grant. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only if a pilot prospect has
more than 50,000 SKUs. The welding demo comes after the fastener rehearsal and never blocks it
(D-208). **Staging:** `docflow-parse-staging` runs `main`'s image;
`docflow-worker-staging` has no machines and is still v10 with the old `parse_client`, which must
not be started against it: deploy `main`'s worker first, read the machine's memory (raised to 2 GB;
`fly.toml` says 1 GB), then start it. The API's and the parse machine's auto-stop are on, and the
Healthchecks.io check is paused.

## Replaced on 2026-10-19 (Stage 5 started; PR 1, blocker 4, built; D-210)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-19, at the Stage 4 checkpoint:** Phase 5.5, **Stage 4 is complete and waits
for the founder's "go"** (`CHECKPOINTS.md`, Stage 4). Every Stage 4 PR is merged (#60 to #69; `main`
is `9d3a453`). The checkpoint's runs: both CI push runs on `9d3a453` are green; the staging suites
on `f8cc120` count (main's code is the same); the three live tests passed for $0.049510. **Next:
Stage 5 in full, its first PR being the alert for a PO that fails on our side at intake; then E1 and
E2a; then demo readiness (D-209).** The long catalog commit: a one-hour test through a public
address is planned and not run (it needs its own grant); if the cut is confirmed, the fix goes
before demo readiness. The 50,000-row cap and IMP-003's wording: one PR, before pilots, only if a
pilot prospect has more than 50,000 SKUs. The welding demo comes after the fastener rehearsal and
never blocks it (D-208). **Staging:** `docflow-parse-staging` runs `main`'s image;
`docflow-worker-staging` has no machines and is still v10 with the old `parse_client`, which must
not be started against it: deploy `main`'s worker first, read the machine's memory (raised to 2 GB;
`fly.toml` says 1 GB), then start it. The API's and the parse machine's auto-stop are on, and the
Healthchecks.io check is paused.

## Replaced on 2026-10-19 (the Stage 4 checkpoint, awaiting "go"; CHECKPOINTS.md, D-208, D-209)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-19, with PR 1b-ii merged:** Phase 5.5, **Stage 4** (the audit of the broad
`except` blocks). **Every Stage 4 PR is merged:** PR 1b-ii is PR #68 (`main` is `143fd2b`; D-207
item 14), after PR 1a, PR 1b-i, the Stripe test's fix and Q14 (#64 to #67). What an `.eml` or `.msg`
unwrap couldn't read is now a warning the reviewer must acknowledge (VAL-017 for an attachment,
VAL-018 for the body), and an import that fails on our side is IMP-011 with an `import_failed` alert.
Before the merge the three staging suites passed on its head (core `1051 passed, 1 skipped`, worker
`242 passed, 7 skipped`, API `673 passed, 1 skipped, 3 deselected`, the leftover line `+0` on all
six), and so did the Fly gate (every line PASS, A-net IPv6 PASS; evidence in
`docs/spikes/3c-fly-staging/2026-10-18-sha256-e1ac59b6/`). **Staging:** `docflow-parse-staging` runs
1b-ii's image, which is `main`'s code; `docflow-worker-staging` has no machines and is still v10 with
the old `parse_client`, which must not be started against it. Deploy order for 1b-ii: the worker,
then the parse app, and the parse app is done; so the next worker start is: deploy `main`'s worker
first, read the machine's memory (raised to 2 GB; `fly.toml` says 1 GB), then start it. Next: **the
Stage 4 checkpoint.** Not fixed, each its own PR, timed at the Stage 4 checkpoint: IMP-003's
wording, the 50,000-row cap, the long catalog commit, and a PO that fails on our side at intake
raising no alert. If Stage 4 is not merged by 2026-11-02, D-195's two changes go next. The API's and
the parse machine's auto-stop are on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-19 (PR 1b-ii merged as PR #68; D-207 item 14)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-18, with Q14 merged and PR 1b-ii built:** Phase 5.5, **Stage 4** (the audit
of the broad `except` blocks). **Q14 is merged** (PR #67; `main` is `24e0204`; D-206 item 11): `0038`
is on staging, and the PR merged before the worker and API staging suites finished; both then passed.
**PR 1b-ii is built, not merged** (branch `phase55/stage4-pr1b-ii-unwrap-and-import`; D-207): what an
`.eml` or `.msg` unwrap couldn't read becomes a warning the reviewer must acknowledge, VAL-017 naming
the attachment (signature logos the body references, contact cards and invites excepted) and VAL-018
for the body, written with the extraction and left alone by validation; an uploaded email whose
attachment or body hits our own bug fails whole as DOC-030 (on purpose; intake keeps its
per-attachment rule); an import whose parse job fails on our side is IMP-011 with a new
`import_failed` alert ("Catalog import, no document" in the Console); BLAMES_INPUT and DROPS_CONTENT
are retired from the guard. No migration. **The Fly gate** (the parse service changed) waits for a
green pull-request run and the founder's word; the PR is opened as a draft. **Recorded:** one local
core run reached staging Storage on 2026-10-18 (seven probe objects written and deleted); every later
run blocks every outside service (D-207 item 11). **Staging:** `docflow-parse-staging` runs 1b-i;
`docflow-worker-staging` has no machines and is still v10 with the old `parse_client`, which must not be
started against it. The next worker start: deploy `main`'s worker first, read the machine's memory
(raised to 2 GB; `fly.toml` says 1 GB), then start it. Deploy order for 1b-ii: the worker, then the
parse app. Next: the Stage 4 checkpoint. Not fixed, each its own PR, timed at the Stage 4 checkpoint:
IMP-003's wording, the 50,000-row cap, the long catalog commit, and a PO that fails on our side at
intake raising no alert. If Stage 4 is not merged by 2026-11-02, D-195's two changes go next. The
API's and the parse machine's auto-stop are on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-18 (Q14 merged as PR #67; PR 1b-ii built; D-206 item 11, D-207)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-16, with the Stripe test's fix merged and Q14 built:** Phase 5.5, **Stage 4**
(the audit of the broad `except` blocks). **PR 1a, PR 1b-i and the Stripe test's fix are merged** (PRs
#64, #65 and #66; `main` is `37c914c`; D-203 item 14, D-204 item 10, D-205 item 3). **Q14, the database
retry, is built and not merged** (branch `phase55/stage4-q14-database-retry`; D-206): a step of the
worker that loses the database (the exact classes D-206 names) waits it out as `wait_cause = 'database'`
instead of failing, retried on the model provider's steps for up to 6 hours, then DOC-021 and its
alert; a lost save re-runs the extraction at most once, counted from the cost record so the limit holds
through the stuck sweep; other database errors fail as before, keyed by their SQLSTATE, and the UTC
date is gone from connection errors' keys. **Migration `0038`** (the fourth wait cause and the
item-delete index on `document_lines`) is **not applied on staging**: backup waived on staging
(founder); it waits for the founder's word after the push run is green, and then the staging suites
run before the PR merges (RUNBOOK 1, 1.10). **Staging:** `docflow-parse-staging` runs 1b-i;
`docflow-worker-staging` has no machines and is still v10 with the old `parse_client`, which must not be
started against it. The next worker start: deploy `main`'s worker first, read the machine's memory
(raised to 2 GB; `fly.toml` says 1 GB), then start it. Deploy order for Q14: `0038`, then the code.
Next: **PR 1b-ii** (VAL-017 and VAL-018, IMP-011 and the import's own code; until then an import that
hits our bug shows IMP-004), then the Stage 4 checkpoint. Not fixed, each its own PR, timed at the
Stage 4 checkpoint: IMP-003's wording, the 50,000-row cap, the long catalog commit, and a PO that fails
on our side at intake raising no alert. If Stage 4 is not merged by 2026-11-02, D-195's two changes go
next. The API's and the parse machine's auto-stop are on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-16 (the Stripe test's fix merged as PR #66; Q14 built; D-205 item 3, D-206)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-16, with PR 1b-i merged and the Stripe test's fix built:** Phase 5.5,
**Stage 4** (the audit of the broad `except` blocks). **PR 1a is merged** (PR #64, D-203 item 14) and
**PR 1b-i is merged** (PR #65; `main` is `c1589a3`; D-204 item 10): a parsing block catches only what
its library raises on a malformed file; anything else is ours, the job answers `error`, and the
document fails with DOC-030 and the keyed alert; the mutation corpus runs in CI through the real
image; a library's stdout no longer corrupts the job's answer (a bug since 3c), and a job's stderr is
logged only as length, hash and category. The Fly gate passed on its head (every self-test PASS,
A-net IPv6 PASS; evidence `docs/spikes/3c-fly-staging/2026-10-16-sha256-2e5d20af/`). **The Stripe
test's fix is built and not merged** (branch `phase55/stage4-stripe-test-race`; D-205): the exact
300-second edge pinned in core on a fixed clock, the API tests given a 30-second margin; no product
code. **Staging:** `docflow-parse-staging` runs 1b-i; `docflow-worker-staging` has no machines and is
still v10 with the old `parse_client`, which must not be started against it. The next worker start:
deploy `main`'s worker first, read the machine's memory (raised to 2 GB; `fly.toml` says 1 GB), then
start it. Deploy order for 1b-i: the worker, then the parse app (the parse app is already on it).
Next: the database retry with migration `0038` (Q14), then **PR 1b-ii** (VAL-017 and VAL-018, IMP-011
and the import's own code; until then an import that hits our bug shows IMP-004), then the Stage 4
checkpoint. Not fixed, each its own PR, timed at the Stage 4 checkpoint: IMP-003's wording, the
50,000-row cap, the long catalog commit, and a PO that fails on our side at intake raising no alert.
If Stage 4 is not merged by 2026-11-02, D-195's two changes go next. The API's and the parse
machine's auto-stop are on, and the Healthchecks.io check is paused.

## Replaced on 2026-10-16 (PR 1b-i merged as PR #65; the Stripe test's fix built; D-204 item 10, D-205)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-16, with PR 1b-i built:** Phase 5.5, **Stage 4** (the audit of the broad
`except` blocks). **PR 1a is merged** (PR #64; `main` is `5df2ddb`; D-203 item 14): the alert raised
once per state, the guard over every broad `except`, migration `0037` (on staging, not deployed in
code). **PR 1b is split (founder; D-204). PR 1b-i is built and not merged** (branch
`phase55/stage4-pr1b-i-parse-error`, uncommitted): every parsing block that blamed the file for any
exception now catches only what its library raises on a malformed file, a generic type only from the
library's own frame; anything else is ours, and the parse job answers a new outcome, `error`. A
document whose parse fails on our side, or whose job crashed, fails with **DOC-030** ("a problem on
our side, not with your file") and the keyed alert, keyed by the parse app's release, never DOC-005.
The zip scan and the unguarded ODF `mimetype` read catch exactly what a broken zip raises. Email
intake refuses only the attachment whose check failed on our side, as DOC-030, and reads the rest,
with the keyed alert naming the intake rejection ("Email intake, no document" in the Console). **The mutation corpus:** 369
broken copies of every fixture, each held to the answer it gave before this PR and never `error`, in
CI through the real image too; it found three crashes from the unguarded read (now DOC-005), one
bug of this PR's own (fixed), and **a bug present since 3c**: a library printing to stdout (xlrd, on
an `.xls` whose size is not a whole number of 512-byte sectors, valid ones included) corrupted the
job's answer, so the file failed as DOC-005 or IMP-004; the job now keeps its real stdout for the
answer alone, and the service logs a job's stderr only as length, hash and category, never as text
(7.10; D-204 item 4a). RUNBOOK 8.6 says what to do with a DOC-030. No migration. Local runs
without staging green (parse 534, core 162 of its non-database files, worker 8, web); **the database
tests run in CI only (founder), and the Fly gate (RUNBOOK 8.1) waits for a green pull-request run and
the founder's word.** Deploy order:
the worker, then the parse app. **PR 1b-ii follows Q14:** VAL-017 and VAL-018, IMP-011 and the
import's own code (until then an import that hits our bug shows IMP-004). After 1b-i: the Stripe
test's fix, then the database retry with migration `0038` (Q14), then 1b-ii and the Stage 4
checkpoint. Not fixed, each its own PR, timed at the Stage 4 checkpoint: IMP-003's wording, the
50,000-row cap, the long catalog commit, and a PO that fails on our side at intake raising no alert.
If Stage 4 is not merged by 2026-11-02, D-195's two changes go next. The Fly worker and beat are at
0 with PR A's code (v10), the API's and the parse machine's auto-stop are on, and the Healthchecks.io
check is paused. On the next worker start the machine's memory is read before anything else.

## Replaced on 2026-10-16 (PR 1a merged as PR #64; PR 1b split; PR 1b-i built, not merged; D-204)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-14, with PR 1a built and its staging run done:** Phase 5.5, **Stage 4** (matching performance, then
the audit of the broad `except` blocks). PR A is on `main` (PR #62) and the bench tenant is removed
(PR #63; `main` is `d9b7e8f`). **PR 1, the audit, is split in two (founder; D-203), and PR 1a is
built and not merged** (branch `phase55/stage4-pr1a-except-audit`). It holds: the alert for a fault
in one of our own steps, with the exception's type and our code location and never its message;
**that alert raised once per state**, so an acknowledged one is not raised again until the
exception, the place, the release or the tenant changes (the re-raised-alert defect of D-198 does
not apply to it); the documents a state has hit, counted on the Console, with a list of acknowledged
alerts that are still happening; an alert for a model call's cost record that could not be written;
the guard test over every broad `except` in `apps/` and `packages/` (84 blocks: 16 exempt by rule,
68 listed with a kind); two request blocks narrowed; sixteen log calls that printed an
exception's message changed to its type, our location and the frames; and the five kind 3 log
lines given the frames as well (founder, 2026-10-14). **It carries migration
`0037`** (two nullable columns and their indexes, a reverse script, the CI round trip), **applied on
staging by the founder on 2026-10-14 and verified.** The staging suites from the branch: core and
worker green; the API's one failure was a fault in a test of the PR's own, fixed in the test and its
file green alone (D-203 item 13). The migration goes before the code. No Fly gate applies to 1a. **PR 1b follows:** the parse service's
blocks, DOC-030, VAL-017 and VAL-018, a new job outcome, the mutation corpus and the Fly gate
(RUNBOOK 8.1). After it, inside Stage 4: the Stripe test's fix, and the database retry with
migration `0038`. **The product cannot import more than 50,000 items** (the parse service's row cap,
D-108); the 250,000 measurement on the Fly worker moves to the cap's own PR. **Not fixed, each for
its own PR, with its timing decided at the Stage 4 checkpoint and not inside Stage 4 (founder):**
IMP-003's wording, which would retire items (a pilot blocker); the 50,000-row cap (a candidate); the
catalog commit, which held one request for 4 min 14 s (a pilot blocker, pending a test through a
public address); and, new, **a PO that fails on our side at intake raises no alert** (a pilot
blocker). A known limit stands: a 40-line order on a 250,000-item catalog took 4.86 s on a CI
runner, against the 4 s mark, until the catalog is kept between documents (PR B, not now). After
Stage 4: Stage 5, demo readiness with D-195's model work, the Phase 6 pilot-blocker subset, then
pilots with two or three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not
merged by 2026-11-02, D-195's two changes go next, before Stage 5; the estimate today ends Stage 4
around 2026-10-29 to 2026-10-30. The Fly worker and beat are at 0 with PR A's merged code as the
release (v10), the API's and the parse machine's auto-stop are on, and the Healthchecks.io check is
paused. On the next worker start the machine's memory is read before anything else.

## Replaced on 2026-10-13 (PR 1 split in two; PR 1a built, not merged, with migration `0037`; D-203)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-13, after PR A merged:** Phase 5.5, **Stage 4** (matching performance). **PR
A is merged** (PR #62; `main` is `2988661`; D-202): the fuzzy step in `matching.py` scores the whole
catalog in one call a field and runs the per-item code on the best 100 by each; the loop as it was
is kept as the definition, and 21 parity tests hold the two to the same result. Its merge gate
(founder, 2026-10-13) was a 50,000-item tenant measured on staging through the product, plus the
green 250,000-item CI bench run, and both were met: no difference from the full scan on 100 lines at
250,000 items (run 38424636633); on the Fly worker at 50,000 items, 12.3 ms a line at p50 and 14.8
ms at p95 (the mark is 100 ms), a 40-line order in 1.7 s (the mark is 4 s) and no difference on 30
lines; and one real 40-line PO to `needs_review` with its lines matched in 3.4 s, for $0.0694. CI
was green on the merged head (13 checks) and on `main` (six jobs), each on its first attempt. **The
bench tenant is removed from staging** (D-202 item 18): cancelled by the founder, suspended by one
sweep run locally, made due by one statement the founder ran, and deleted by the founder in the
Console, its 50,000 items in about 4.1 s through a local API. Its Stripe test customer is deleted,
and nothing of any other tenant changed. The database is 43 MB by SQL (58 of 500 MB on the
dashboard). **The next step is PR 1, the broad-`except` audit, on top of `main`; nothing of it is
built.** After it, inside Stage 4: the Stripe test's fix and the database retry, as their own small
PRs. **The product cannot import more than 50,000 items** (the parse service's row cap, D-108),
which the measurement found; the 250,000 measurement on the Fly worker moves to the cap's own PR.
**Three findings are not fixed, each for its own PR, timed at the Stage 4 checkpoint:** IMP-003's
wording, which would retire items (a pilot blocker); the 50,000-row cap (a candidate); and the
catalog commit, which held one request for 4 min 14 s (a pilot blocker, pending a test through a
public address). A known limit stands: a 40-line order on a 250,000-item catalog took 4.86 s on a CI
runner, against the 4 s mark, until the catalog is kept between documents (PR B, not now). After
Stage 4: Stage 5, demo readiness with D-195's model work, the Phase 6 pilot-blocker subset, then
pilots with two or three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not
merged by 2026-11-02, D-195's two changes go next, before Stage 5. The Fly worker and beat are at 0
with PR A's merged code as the release (v10; no redeploy is owed), the API's and the parse machine's
auto-stop are on, and the Healthchecks.io check is paused. On the next worker start the machine's
memory is read before anything else.

## Replaced on 2026-10-13 (PR A merged as PR #62; the bench tenant removed from staging; D-202 items 16 to 18)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-13, after the staging measurement:** Phase 5.5, **Stage 4** (matching
performance). **PR A is built, measured and not merged** (PR #62, branch `phase55/stage4-fast-scan`;
D-202): the fuzzy step in `matching.py` scores the whole catalog in one call a field and runs the
per-item code on the best 100 by each; the loop as it was is kept as the definition, and 21 parity
tests hold the two to the same result. **Its merge gate (founder, 2026-10-13): a 50,000-item tenant
measured on staging through the product, plus the green 250,000-item CI bench run.** Both are done.
The CI bench run (38424636633): no difference from the full scan on 100 lines, 36.5 ms a line at
p50. On staging: 50,000 items imported through the Console, then on the Fly worker 12.3 ms a line at
p50 and 14.8 ms at p95 (the mark is 100 ms), a 40-line order in 1.7 s (the mark is 4 s), no
difference on 30 lines; then one real 40-line PO reached `needs_review` with its lines matched in
3.4 s, for $0.0694. **Merging is the founder's.** The gate had been a 250,000-item tenant on
staging: **the product cannot import more than 50,000 items** (the parse service's row cap, D-108),
which the measurement found; a direct load was declined, and the 250,000 measurement on the Fly
worker moves to the cap's own PR. **Three findings are not fixed, each for its own PR, timed at the
Stage 4 checkpoint:** IMP-003's wording, which would retire items (a pilot blocker); the 50,000-row
cap (a candidate); and the catalog commit, which held one request for 4 min 14 s (a pilot blocker,
pending a test through a public address). A known limit stands: a 40-line order on a 250,000-item
catalog took 4.86 s on a CI runner, against the 4 s mark, until the catalog is kept between
documents (PR B, not now). **Staging holds the bench tenant** `Acme Test Bench 01 (50000 items)`
(about 30 MB; the dashboard read 78 of 500 MB) until PR A merges; the founder then removes it with
the Console's delete. **Nothing of PR 1 is built;** PR 1 (the broad-`except` audit) follows on top
of PR A, then the Stripe test's fix and the database retry as their own small PRs. After Stage 4:
Stage 5, demo readiness with D-195's model work, the Phase 6 pilot-blocker subset, then pilots with
two or three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not merged by
2026-11-02, D-195's two changes go next, before Stage 5. The Fly worker and beat are at 0 with PR
A's image as the release (v10; if PR A does not merge, `main`'s image is deployed again before
staging is used for anything else), and the Healthchecks.io check is paused.

## Replaced on 2026-10-13 (after the staging measurement at 50,000 items; PR A's merge gate changed; D-202 items 13 to 15)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-13:** Phase 5.5, **Stage 4** (matching performance, measured on a
250,000-item tenant). The measurements and what they retire are on `main` (PR #61, `2b9c163`;
D-201): the approved design's two trigram indexes, migration `0037`, the cut-off guard and the
fallback count are not built, and matching keeps scoring every item. **PR A is built and not
merged** (branch `phase55/stage4-fast-scan`; D-202): the fuzzy step in `matching.py` scores the
whole catalog in one call a field and runs the per-item code on the best 100 by each; the loop as
it was is kept as the definition, and 21 parity tests hold the two to the same result. On the
development machine at 250,000 items: 55 ms a line against 10.1 s, no difference on 12 lines.
**Before PR A merges:** the CI bench run on `250000` from its branch (zero differences on 100
lines), and the measurement on the Fly worker against the 250,000 tenant loaded on staging, which
stays on Nano (about 125 MB, under the 350 MB guard), then one real 40-line PO end to end. The
founder is asked before each staging step. **Nothing is loaded on staging, and nothing of PR 1 is
built;** PR 1 (the broad-`except` audit) follows on top of PR A. A known limit is recorded: a
40-line order on a 250,000-item catalog is expected to take about 6 s, against the 4 s mark,
until the catalog is kept between documents (PR B, not now). After Stage 4: Stage 5, demo
readiness with D-195's model work, the Phase 6 pilot-blocker subset, then pilots with two or
three customers ("The plan after the Stage 3 "go"", below). If Stage 4 is not merged by
2026-11-02, D-195's two changes go next, before Stage 5. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused.

## Replaced on 2026-10-13 (the records merged as PR #61; PR A built, not merged; D-202)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-12:** Phase 5.5, **Stage 4** (matching performance, measured on a
250,000-item tenant). The sizing job is on `main` (PR #60, `c30926b`) and has run for the 250,000
tenant and the 800,000 mix; the 2.9 million mix has not been run. **Its first run showed that the
approved design's candidate query reads its whole trigram index on a fastener catalog, and no index
variant measured after it keeps parity with today's full scan** (D-201). **Decided (founder):
matching keeps scoring every item, and is made fast instead.** Measured in the bench on a CI
runner at 250,000 items: 57 ms a line against 13.9 s today, with no difference on 100 lines.
**Retired from the approved design:** migration `0037`, both trigram indexes, the cut-off guard
and the fallback count. **The order now:** this records PR (the bench scripts, D-201 and the
measurements; no product code); then **PR A**, the exact fast scan in `matching.py` with no state
kept between documents; then PR 1 (the broad-`except` audit) on top of it. Before PR A merges it
is measured on the Fly worker against the 250,000 tenant loaded on staging, which stays on Nano
(about 125 MB without the indexes, under the 350 MB guard). **Nothing is loaded on staging, and
nothing of PR A or PR 1 is built.** A known limit is recorded: a 40-line order on a 250,000-item
catalog is expected to take about 6 s, against the 4 s mark, until the catalog is kept between
documents (PR B, not now). After Stage 4: Stage 5, demo readiness with D-195's model work, the
Phase 6 pilot-blocker subset, then pilots with two or three customers ("The plan after the Stage
3 "go"", below). If Stage 4 is not merged by 2026-11-02, D-195's two changes go next, before
Stage 5. The Fly worker and beat are at 0 and the Healthchecks.io check is paused.

## Replaced on 2026-10-12 (the candidate indexes measured and retired; an exact, faster full scan instead; D-201)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-11 (evening):** Phase 5.5, **Stage 4 has started** (matching performance,
at 800,000 catalog items). Stage 3 is complete, "go" given 2026-10-11 (D-199), and the leftover
count is a failure since PR #59 (`main` `c462565`). **Stage 4's order (founder, 2026-10-11;
D-200): the catalog sizing job as its own PR, then PR 1 (the broad-`except` audit), then PR 2
(matching).** **Built: the sizing job** (`scripts/bench/`, `.github/workflows/catalog-sizing.yml`;
RUNBOOK 1.8): fake catalogs with fastener families from a fixed seed, loaded into a CI runner's own
database with the two trigram indexes, and the sizes printed. Once it is on `main` the founder runs
it for three mixes, smallest first (the 250,000 tenant alone, the 800,000 mix, then the 2.9 million
mix after a prediction of its run time from the first two), and chooses, from the measured sizes,
between Supabase Pro with the 800,000 mix and Nano with the 250,000 tenant alone. **Nothing is loaded on staging** (Nano on Supabase's Free plan, 500 MB) and nothing of PR 1
or PR 2 is built. After Stage 4: Stage 5, demo readiness with D-195's model work, the Phase 6
pilot-blocker subset, then pilots with two or three customers ("The plan after the Stage 3 "go"",
below). If Stage 4 is not merged by 2026-11-02, D-195's two changes go next, before Stage 5. The
Fly worker and beat are at 0 and the Healthchecks.io check is paused.

## Replaced on 2026-10-11, in the evening (Stage 4 started with the catalog sizing job; D-200)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-11:** Phase 5.5. **Stage 3 is complete: the founder gave the "go" on
2026-10-11**, after merging the checkpoint's record (PR #58, `main` `d674d4a`; D-199). **First
after it:** an API run that does not leave the database as it found it now fails (D-199; RUNBOOK
1.4); on staging at `b278490` the suite read `641 passed, 1 skipped, 3 deselected` with `+0` on all
six counts. **The order from here (founder, 2026-10-11; "The plan after the Stage 3 "go"",
below):** Stage 4 (matching performance, now at 800,000 catalog items; the design is being revised
to that load, and nothing is built before the founder approves the revision); Stage 5 (cleanup,
with the fix for the 10 seconds after a worker start as its own PR there, not before Stage 4); demo
readiness, with D-195's model work; the Phase 6 pilot-blocker subset; then pilots with two or three
customers. If Stage 4 is not merged by 2026-11-02, D-195's two changes go next, before Stage 5.
Staging's database is Nano on Supabase's Free plan (500 MB): nothing is loaded for Stage 4 until a
CI job has measured the sizes and the founder has chosen. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused.

## Replaced on 2026-10-11 (the Stage 3 "go"; D-199)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-09:** Phase 5.5, Stage 3, **checkpoint written and waiting for the founder's
"go"** (CHECKPOINTS.md; D-198). Slices 3a to 3e are merged, the logins cutover (RUNBOOK 10.2) is
done with step 7, and the first worker deploy (RUNBOOK 9.7) is done through step 15. **Done on
2026-10-09:** the three backups and the old role `docflow_app` dropped by the founder; one purge for
every throwaway tenant the API suite makes, with a printed count of what a run leaves (PR #57); the
test leftovers on staging cleaned up (tenant `db4495fb` deleted in the Console; test tenants'
pending jobs cancelled, their alerts and held mail removed; four leftover test admins revoked and
six user rows made inactive). Staging holds 0 pending jobs, 0 held mail rows and 0 alerts belonging
to a test tenant; 30 held rows and 4 alerts with no tenant are kept on purpose. **The checkpoint's
runs on `main` `5a09632`:** core `806 passed, 1 skipped`, worker `191 passed, 7 skipped`, API `630
passed, 1 skipped, 3 deselected` with nothing left behind, and the three live tests passed for
$0.0496 on `claude-sonnet-5`; CI green on the same commit. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused. **Next, after the "go":** the leftover count becomes a failure, the
fix for the 10 seconds after a worker start (its own PR), the model policy (D-195), then Stage 4.
Nothing of Stage 4 is built before the "go".

## Replaced on 2026-10-09 (the Stage 3 checkpoint's runs done; D-198)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-09:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged and the logins
cutover (RUNBOOK 10.2) is done, step 7 included. The first worker deploy (RUNBOOK 9.7) is done
through step 15 and the founder accepted the 500 + 1 run (D-194, D-196). **Merged since:** the
recycle threshold at 500 MiB (PR #52), the Redis idle change (PR #53) and the document-cost fix
(PR #54; D-163's addendum). **The stop drills and the second idle hour are done (D-197):** an idle
hour is 6,205 Redis commands, so production stays on Pay as You Go; a deploy during a short read
lets the read finish; a worker killed during a read held another tenant's order for 31 minutes 16
seconds, which is the Phase 6 launch blocker, measured. The deployed worker has read 537 documents
on Fly staging; recorded model spend is $6.97. The Fly worker and beat are at 0 and the
Healthchecks.io check is paused. **Dropped by the founder on 2026-10-09,** each checked read-only
by Claude before and afterwards: `backup_0034`, `backup_0035` and `backup_3b` (RUNBOOK 1.3; no
backup schema is left on staging), and the old role `docflow_app` (RUNBOOK 10.2 step 7, with the
SQL as run). Going back past `0036` now means recreating that role first (RUNBOOK 10.2, "Going
back", step 2). **Built for the checkpoint's list:** one purge for every throwaway tenant the API
suite makes (files, rows, the tenant; it refuses a tenant the run did not create) and a printed
count of what a run left behind (RUNBOOK 1.4; four tests added, API 631 in CI). **Next:** the
Stage 3 checkpoint, whose list also holds the three `live_api` tests and the test leftovers'
cleanup (CHECKPOINTS.md). Nothing of Stage 4 is built before its "go", and the model policy (D-195) waits
for the same "go".

## Replaced on 2026-10-09 (the stop drills and the second idle hour done, three PRs merged; D-197)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-08:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged and the logins
cutover (RUNBOOK 10.2) is done through step 6. **The first worker deploy (RUNBOOK 9.7) is done
through step 15: every gate passed, and the founder accepted the 500 + 1 run on 2026-10-08**
(D-194, D-196; CHECKPOINTS.md, Stage 3, "The first worker deploy"). The deployed worker has read
532 documents on Fly staging; recorded model spend is $6.91. The Fly worker and beat are at 0 and
the Healthchecks.io check is paused. **Next:** two PRs proposed for the founder's review, neither
merged without the founder: the recycle threshold at about 500 MiB, and a longer queue-poll
interval with task-result storage off, followed by one idle hour measured again (D-196). Now due,
the founder's actions: dropping `backup_0034`, `backup_0035`, `backup_3b` and the role
`docflow_app` (RUNBOOK 1.3; 10.2 step 7). Then the Stage 3 checkpoint. Nothing of Stage 4 is
built before its "go", and the model policy (D-195) waits for the same "go".

## Replaced on 2026-10-08 (the first worker deploy's steps 9 to 15 done, the 500 + 1 run accepted; D-196)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-06:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged and the logins
cutover (RUNBOOK 10.2) is done through step 6. **The first worker deploy (RUNBOOK 9.7) is under
way: steps 2 to 8 are done and passed.** The parse service, the worker and the API are deployed on
Fly staging, one machine each, nothing public (D-194). No document has been processed and nothing
is spent on the model. Step 7's log showed the worker writing its heartbeat URL on every ping:
fixed here, with a line the launcher now prints when its login check passes (D-194). **Next:** the
founder replaces the Healthchecks.io check (RUNBOOK 9.4), Claude deploys the fixed worker and
repeats step 7's checks, then step 9 (G) on the founder's word. The Fly worker and beat are at 0
whenever they are not being tested. Then the Stage 3 checkpoint; nothing of Stage 4 is built
before its "go". `backup_0034`, `backup_0035`, `backup_3b` and the role `docflow_app` are dropped
only after that deploy has processed real documents on staging (RUNBOOK 1.3; 10.2 step 7).

## Replaced on 2026-10-06 (the first worker deploy's steps 2 to 8 done; D-194)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-05:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged. The logins
cutover (RUNBOOK 10.2) is done through step 6 on staging: `0036` applied, the four logins in use,
`docflow_app` set `NOLOGIN` and refused, its URL gone from the root `.env` (CHECKPOINTS.md, Stage
3, "The cutover"; D-188, D-189, D-190). **Next: the first worker deploy. The founder approved its
plan and budget on 2026-10-05** (D-193; the procedure is RUNBOOK 9.7; "Gates for the first worker
deploy" below). Nothing is set on Fly, deployed or spent yet. It starts with the founder's steps 1
and 3, and no deploy command runs before the founder confirms the secret names at STOP 1. Then the
Stage 3 checkpoint. Nothing of Stage 4 is built before that checkpoint's "go". `backup_0034`,
`backup_0035`, `backup_3b` and the role `docflow_app` are dropped only after that deploy has
processed real documents on staging (RUNBOOK 1.3; 10.2 step 7).

## Replaced on 2026-10-05 (later the same day, when the first worker deploy's plan was approved)

<!-- the paragraph as it stood, starting on the next line -->
**Status, as of 2026-10-05:** Phase 5.5, Stage 3. Slices 3a to 3e are all merged. The logins
cutover (RUNBOOK 10.2) is done through step 6 on staging: `0036` applied, the four logins in use,
`docflow_app` set `NOLOGIN` and refused, its URL gone from the root `.env` (CHECKPOINTS.md, Stage
3, "The cutover"; D-188, D-189, D-190). **Next: the first worker deploy, which waits for the
founder's go** ("Gates for the first worker deploy" below; RUNBOOK 9.2), then the Stage 3
checkpoint. Nothing of Stage 4 is built before that checkpoint's "go". `backup_0034`,
`backup_0035`, `backup_3b` and the role `docflow_app` are dropped only after that deploy has
processed real documents on staging (RUNBOOK 1.3; 10.2 step 7).

## Replaced on 2026-10-05

<!-- the paragraph as it stood, starting on the next line -->
**Keeping this file current:** update it at the end of every slice, in the same
commit as the slice. Statuses below are as of **2026-10-05** (latest: **the logins cutover (RUNBOOK 10.2) is done through step 6 on staging: `0036` applied, the four logins in use, every step 5 check passing on `main` `6cac1bd`, `docflow_app` set `NOLOGIN` and refused (21:35 UTC), its URL gone from the root `.env`; step 7 not done; the first worker deploy waits for the founder's go**. Earlier the same day: **step 5 found two worker test defects on the founder's machine, not in `0036`, fixed in PR #42 (D-189)**. Earlier the same day: **the logins cutover (RUNBOOK 10.2) started: step 1 passed, then stopped before `0036` on a gap in step 5 (CI's forward state was kept nowhere); the forward state is now a committed file, `0036_post_snapshot.json`, asserted in CI (D-188); staging still at `0035`**. Earlier the same day: **the two backup checks run at `f191e27` as `docflow_app`: core 746 passed and 1 skipped, worker 161 passed and 6 skipped, API 586 passed, 1 skipped and 3 deselected, nothing failed; alerts empty, which carries no weight; `backup_0034`, `backup_0035` and `backup_3b` kept until the first worker deploy has processed real documents on staging (founder); staging confirmed at `0035`**. Earlier, 2026-10-02: **Stage 3 checkpoint DRAFTED (CHECKPOINTS.md, gaps for the cutover, the backup checks and the first worker deploy) and the Stage 4 design APPROVED WITH CHANGES (founder, 2026-10-02: Q1-Q15 decided; DOC-030, VAL-017 and VAL-018 final; PR 1's content final at `444dc78`, blocked only on the Stage 3 checkpoint's "go"), nothing built. The checkpoint waits for the first worker deploy, scheduled straight after the cutover is verified. Before that: 3e MERGED 2026-10-02 17:43 UTC (PR #35, `e99fbb9`, D-185) and the CI runner pin merged 17:48 UTC (PR #36, main `d004186`); the backup checks run the staging suites at `f191e27`, in a separate worktree as `docflow_app` (founder, PR #37; RUNBOOK 1.3); `0036` not applied to staging before the `backup_0035` check, on or after 2026-10-05 03:18 UTC**. Earlier the same day: 3d merged, PR #33, main `4a2b907`, 2026-10-02 03:18 UTC (D-184), recorded in PR #34; the two two-at-once sweep tests go to 3e). Earlier, 2026-10-01: 3d built, `0035` on staging, staging suites green. Earlier the same day: 3c merged, PR #32, main `085a2a5`. Earlier, as of 2026-09-30: security PR #30 and 3b (#31) merged; the 3c design proposed. Earlier summary, as of 2026-09-29 (Phase 5.5: Stages 0, 1 and 2 done -- 2a-2d merged (PRs #14, #15, #18, #20, plus #21 and #22), the audit-findings design (#23) and the D-170 clock PR (#24) merged, Stage 2 checkpoint written; **Stage 3 design agreed 2026-09-29; 3a merged (PR #26, D-179; `0030` on staging); the test-run lock merged (PR #27, D-180); card billing built (D-181), migration `0031` awaiting staging; 3b next**; `0029` row counts confirmed by the founder (the only difference: 52 `stripe_webhook_events` test ids from post-migration runs); D-150 settled -- Fly.io, proof spike PASSED 2026-09-28).
