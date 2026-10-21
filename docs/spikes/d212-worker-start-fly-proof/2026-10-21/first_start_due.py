"""Read-only, counts only: what a first start of the staging worker and beat
would find due, task by task (D-212's pre-deploy report). One READ ONLY
transaction through the admin login (platform_session), rolled back.
The window is now + 2 hours: the deploy, the proof and the scale-down.
Run from apps/api with its venv:  python first_start_due.py"""

from docflow_core.constants import (
    DISPATCHER_STALE_MIN,
    PROVIDER_MAX_WAIT_HOURS,
    QUARANTINE_TTL_DAYS,
    STUCK_PROCESSING_TIMEOUT_MIN,
    WORKER_RESTART_WINDOW_MIN,
)
from docflow_core.db import platform_session
from sqlalchemy import text

W = "now() + interval '2 hours'"  # the window's end
T = STUCK_PROCESSING_TIMEOUT_MIN  # 30

QUERIES = [
    # ── documents that would reach the model (dispatch, stuck sweep, a probe) ──
    ("D1 waiting, claimable now or inside the window (dispatch pass)",
     "SELECT count(*) FROM documents WHERE deleted_at IS NULL AND status = 'pending' "
     f"AND dispatched_at IS NULL AND (retry_at IS NULL OR retry_at <= {W})"),
    ("D2 waiting, retry_at after the window (only a probe could send one)",
     "SELECT count(*) FROM documents WHERE deleted_at IS NULL AND status = 'pending' "
     f"AND dispatched_at IS NULL AND retry_at > {W}"),
    ("D3 dispatched, not claimed (stuck sweep returns them; a leftover job could claim one)",
     "SELECT count(*) FROM documents WHERE deleted_at IS NULL AND status = 'pending' AND dispatched_at IS NOT NULL"),
    ("D4 processing (stuck sweep retries or fails one past the timeout)",
     "SELECT count(*) FROM documents WHERE deleted_at IS NULL AND status = 'processing'"),
    # model_provider_state has no policy for docflow_admin (it would read as
    # no rows); provider_state() is granted to it (0036).
    ("D5 provider state (down = the dispatcher sends a probe)",
     "SELECT coalesce(status, '(no row)') FROM public.provider_state('anthropic')"),
    ("D6 waiting on the provider past the limit (stuck sweep: DOC-024 + alert, no call)",
     "SELECT count(*) FROM documents WHERE deleted_at IS NULL AND status = 'pending' "
     f"AND wait_cause = 'model_provider' AND waiting_since < {W} - make_interval(hours => {PROVIDER_MAX_WAIT_HOURS})"),
    ("D7 dispatcher_status(): last pass, age s, waiting",
     "SELECT last_pass_at::text || ' | ' || coalesce(age_seconds::text, 'never') || ' | ' || waiting "
     "FROM public.dispatcher_status()"),
    # ── scheduled jobs the sweep would run (each writes e-mail rows) ──
    ("J1 scheduled jobs due inside the window, by type",
     "SELECT coalesce(string_agg(job_type || '=' || n, ', '), 'none') FROM ("
     "SELECT job_type, count(*) AS n FROM scheduled_jobs WHERE "
     f"(status = 'pending' AND run_at <= {W}) "
     "OR (status = 'running' AND started_at < now() - interval '30 minutes') GROUP BY job_type) j"),
    ("J2 the earliest pending job's run_at, and how many are pending at all",
     "SELECT coalesce(min(run_at)::text, 'none') || ' | ' || count(*) FROM scheduled_jobs WHERE status = 'pending'"),
    # ── the lifecycle sweep ──
    ("L1 Stripe cancels owed (pass 0: Stripe calls)",
     "SELECT count(*) FROM tenants WHERE stripe_cancel_pending_at IS NOT NULL"),
    ("L2 cancelling tenants due inside the window (pass 1: suspend, Stripe, owner e-mail)",
     f"SELECT count(*) FROM tenants WHERE status = 'cancelling' AND cancellation_effective_at <= {W}"),
    ("L3 pending_deletion tenants by deletion date (UTC)",
     "SELECT coalesce(string_agg(d || '=' || n, ', '), 'none') FROM (SELECT (deletion_scheduled_at AT TIME ZONE 'UTC')::date::text AS d, "
     "count(*) AS n FROM tenants WHERE status = 'pending_deletion' GROUP BY 1 ORDER BY 1) p"),
    ("L4 pending_deletion due inside the window with no open ready-to-delete alert (pass 2: one alert each; never a delete)",
     f"SELECT count(*) FROM tenants t WHERE t.status = 'pending_deletion' AND t.deletion_scheduled_at <= {W} "
     "AND NOT EXISTS (SELECT 1 FROM founder_alerts a WHERE a.dedupe_key = 'tenant_ready_to_delete:' || t.id::text "
     "AND a.acknowledged_at IS NULL)"),
    ("L5 grace intake addresses past their end (pass 3: retired, a write, no e-mail)",
     f"SELECT count(*) FROM intake_addresses WHERE status = 'grace' AND grace_ends_at < {W}"),
    ("L6 tenants with held documents past retention and no open alert (pass 3: one alert each)",
     "SELECT count(DISTINCT d.tenant_id) FROM documents d WHERE d.status = 'quarantined' AND d.deleted_at IS NULL "
     f"AND d.quarantined_at < {W} - make_interval(days => {QUARANTINE_TTL_DAYS}) "
     "AND NOT EXISTS (SELECT 1 FROM founder_alerts a WHERE a.dedupe_key = 'quarantine_ttl:' || d.tenant_id::text "
     "AND a.acknowledged_at IS NULL)"),
    # ── the stuck sweep's exports and imports ──
    ("S1 exports pending past the timeout (EXP-009, no call)",
     f"SELECT count(*) FROM exports WHERE status = 'pending' AND deleted_at IS NULL "
     f"AND requested_at < {W} - make_interval(mins => {T})"),
    ("S2 imports parsing past the timeout (IMP-009, no call)",
     f"SELECT count(*) FROM catalog_imports WHERE status = 'parsing' AND deleted_at IS NULL "
     f"AND created_at < {W} - make_interval(mins => {T})"),
    # ── the worker's own start ──
    # worker_starts has no policy for docflow_admin; the function is granted (0036).
    (f"W1 worker starts in the last {WORKER_RESTART_WINDOW_MIN} min (the alert fires at 3)",
     "SELECT public.worker_starts_last_hour()"),
    # ── e-mail ──
    ("E1 the outbox by status",
     "SELECT coalesce(string_agg(status || '=' || n, ', '), 'none') FROM "
     "(SELECT status, count(*) AS n FROM email_outbox GROUP BY status ORDER BY status) o"),
    # ── the six leftover counts ──
    ("C tenants", "SELECT count(*) FROM tenants"),
    ("C founder alerts", "SELECT count(*) FROM founder_alerts"),
    ("C e-mails", "SELECT count(*) FROM email_outbox"),
    ("C scheduled jobs", "SELECT count(*) FROM scheduled_jobs"),
    ("C platform-admin rows", "SELECT count(*) FROM platform_admins"),
    ("C users with no tenant", "SELECT count(*) FROM users WHERE tenant_id IS NULL"),
]

with platform_session() as session:
    session.execute(text("SET TRANSACTION READ ONLY"))
    mode = session.execute(text("SHOW transaction_read_only")).scalar_one()
    print("transaction_read_only:", mode)
    if mode != "on":
        raise SystemExit("not read-only: nothing read")
    print("read at", session.execute(text("SELECT now()::text")).scalar_one(), f"(stale after {DISPATCHER_STALE_MIN} min)")
    for label, query in QUERIES:
        savepoint = session.begin_nested()
        try:
            print(f"{label}: {session.execute(text(query)).scalar_one()}")
            savepoint.commit()
        except Exception as exc:  # noqa: BLE001 -- named, not hidden
            savepoint.rollback()
            print(f"{label}: NOT READ ({type(exc).__name__})")
    session.rollback()
