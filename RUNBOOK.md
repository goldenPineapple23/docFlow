# DocFlow — Runbook

Step-by-step procedures for running DocFlow. Written for the founder: every
step says exactly what to click or paste, and what you should see. Phase 6
completes this file (restore drill, parser upgrades, constants); Phase 5.5
starts it with the procedures that were needed first.

---

## 1. Applying a database migration

Migrations are SQL files in `supabase/migrations/`, applied in number order,
never edited after they have been applied (build prompt Section 5). They are
applied by hand in the Supabase SQL Editor, because the app's own database
role deliberately cannot change the schema (DECISIONS.md D-013, D-017).

**Order of events for any pull request that needs a migration:**

1. Take the backup below, **before** anything else.
2. Check the row counts.
3. Apply the migration file to `docflow-staging`.
4. Tell Claude it is applied. **Do not merge the pull request until Claude has
   run the tests against staging and confirmed** (the PR message says so at the
   top).
5. Merge. Keep the backup until you decide to drop it.
6. From Phase 6 on: only after all of this on staging, apply the same file to
   `docflow-prod`, again with its own backup first.

### 1.1 The standard backup (run before the migration)

Replace `NNNN` with the migration's number and list every table the migration
touches (the PR message names them). In the Supabase SQL Editor:

```sql
create schema if not exists backup_NNNN;

create table backup_NNNN.<table_one> as table public.<table_one>;
alter table backup_NNNN.<table_one> enable row level security;

create table backup_NNNN.<table_two> as table public.<table_two>;
alter table backup_NNNN.<table_two> enable row level security;
```

**Why RLS on a backup table:** Supabase's security advisor flags any table
without row-level security. Turning it on with no policies means only the
project owner (you, in the SQL Editor) can read it; the app and the public API
cannot. A backup holds customer data and gets exactly the same protection as
the table it copies.

### 1.2 The row-count check

```sql
select 'table_one' as t, (select count(*) from public.table_one) as live,
       (select count(*) from backup_NNNN.table_one) as backup
union all
select 'table_two', (select count(*) from public.table_two),
       (select count(*) from backup_NNNN.table_two);
```

`live` and `backup` must match on every row. If they don't, stop and say so;
don't run the migration.

### 1.3 Dropping a backup

Only when you decide to, never automatically, and never while the change it
protects is still being checked:

```sql
drop schema backup_NNNN cascade;
```

---

## 2. Inbound email (Postmark) — arrives with Phase 5.5 Stage 2

Two procedures are written when the webhook authentication is built (the
founder asked for both, D-155; tracked in `docs/BUILD-STATUS.md`):

- confirming the addresses Postmark's inbound webhook really comes from, then
  switching the IP allowlist from log-only to enforcing;
- rotating the webhook's credentials.
