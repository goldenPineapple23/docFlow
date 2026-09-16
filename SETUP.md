# DocFlow — Setup Guide

This walks you through creating every external account DocFlow needs and filling in your `.env` file. You don't need to know how any of these services work internally — just follow the clicks in order. Budget about 30–45 minutes the first time.

Do this now, in parallel with Phase 0 development, since the app can't run without these.

---

## Before you start

You'll create four things:
1. A Supabase project (database, login system, and file storage) — **free**
2. An Anthropic API key (the AI that reads purchase orders) — **pay-as-you-go, pennies per document**
3. A Stripe account in **test mode** (billing, no real money moves) — **free**
4. Local copies of the resulting keys in a file called `.env`

---

## 1. Create the Supabase project (`docflow-staging`)

Supabase gives us the database, login system, and file storage in one place. We're creating a **staging** project now — this is what all development and testing points at. We will *not* create the real production project (`docflow-prod`) until Phase 6, right before your first paying customer.

1. Go to [supabase.com](https://supabase.com) and sign up (GitHub login is fastest).
2. Click **New Project**.
3. Fill in:
   - **Name:** `docflow-staging`
   - **Database password:** click "Generate a password," then **copy it somewhere safe** (a password manager, not a sticky note) — you won't see it again.
   - **Region:** pick whichever is closest to you.
4. Click **Create new project**. This takes a minute or two to provision.
5. Once it's ready, go to **Project Settings → API** (gear icon in the left sidebar, then "API"). You'll need four values from this page — the exact labels vary a bit by Supabase account, so look for whichever of these appears:
   - **Project URL** (looks like `https://xxxxxxxx.supabase.co` — not the dashboard page URL you see in your browser's address bar, the actual project URL shown on this settings page)
   - **Publishable key** (starts with `sb_publishable_...`) — or, on older accounts, the **`anon` `public` key** (starts with `eyJ...`). Either is fine.
   - **Secret key** (starts with `sb_secret_...`) — or, on older accounts, the **`service_role` `secret` key** (starts with `eyJ...`). This one is powerful, treat it like a password, never share it or put it in frontend code.
   - **Legacy JWT Secret** (further down the same page, sometimes under a "JWT Keys" or "Legacy" section) — the backend uses this to verify that a login session is genuinely from your Supabase project. If your project only shows asymmetric signing keys with no legacy secret option, that's fine too — leave this one blank and say so, there's a fallback for that case.
6. **Create a dedicated database role for the app to use — don't skip this.** Supabase's default connection string logs in as the `postgres` role, which is allowed to bypass every row-level-security rule in the database. If our app connected as that role, the tenant-isolation protections in the schema would silently do nothing. Instead:
   1. In the Supabase dashboard, go to **SQL Editor** → **New query**.
   2. Paste and run this (replace `CHOOSE-A-STRONG-PASSWORD` with a real generated password, saved the same way as your database password above):
      ```sql
      CREATE ROLE docflow_app WITH LOGIN PASSWORD 'CHOOSE-A-STRONG-PASSWORD' NOBYPASSRLS;
      GRANT USAGE ON SCHEMA public TO docflow_app;
      GRANT ALL ON ALL TABLES IN SCHEMA public TO docflow_app;
      GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO docflow_app;
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO docflow_app;
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO docflow_app;
      ```
   3. Go to **Project Settings → Database → Connection string**, copy the string under **"Connection pooling"** (transaction mode), then edit it: replace the username (`postgres` or `postgres.xxxxxxxx`) with `docflow_app` and the password with the one you just chose. This edited string — logging in as `docflow_app`, not `postgres` — is what goes in `.env` as `DATABASE_URL`.
   4. This is a one-time step per Supabase project (repeat it for `docflow-prod` in Phase 6).

You'll paste all of these into `.env` in Step 4 below.

## 2. Create your Anthropic API key

This is the AI that reads purchase orders.

1. Go to [console.anthropic.com](https://console.anthropic.com) and sign up or log in.
2. Add a payment method under **Settings → Billing** (usage is pay-as-you-go; a handful of test documents costs a few cents).
3. Go to **Settings → API Keys** → **Create Key**. Give it a name like `docflow-staging`.
4. Copy the key immediately (it starts with `sk-ant-...`) — like the Supabase service key, this won't be shown again.

## 3. Create a Stripe account (test mode)

Stripe handles billing. We're only using **test mode** for now — no real charges happen until you flip a switch much later, right before your first real customer.

1. Go to [stripe.com](https://stripe.com) and sign up.
2. Once in the dashboard, make sure the toggle in the top-right says **"Test mode"** (it's on by default for a new account).
3. Go to **Developers → API keys**. Copy:
   - **Publishable key** (starts with `pk_test_...`)
   - **Secret key** (starts with `sk_test_...` — click "Reveal" first)
4. We'll set up the webhook signing secret later in the build (Phase 5), once there's an endpoint for Stripe to call — no action needed from you yet.

## 4. Fill in your `.env` file

1. In the project root, copy `.env.example` to a new file named `.env`:
   - On Windows (PowerShell): `Copy-Item .env.example .env`
2. Open `.env` and paste in the values you collected above next to the matching variable name. `.env.example` has a comment above each line explaining what it is and where it came from.
3. **Never commit `.env`** — it's already in `.gitignore`, and it should stay that way. If you ever see it show up in `git status` as a file to be committed, stop and tell me before proceeding.

## 5. Apply the database migration and make yourself a platform admin

1. In the Supabase dashboard, go to **SQL Editor** → **New query**, paste in the entire contents of `supabase/migrations/0001_foundations.sql`, and run it. This creates the Phase 0 tables.
2. Sign up once through the app's normal login screen with your own email (this creates your Supabase Auth account — there's no separate "admin signup," you use the same login everyone does).
3. From the repo root, with your `.env` filled in: `python scripts/seed_platform_admin.py you@example.com` (use the email you just signed up with). This is the one-time step that makes your account able to see `/admin` — see the script's own comments for why this can't just be an API call.

## 6. Confirm everything's wired up

Once Phase 0's scaffolding is in place, I'll run a quick check that reads each of these values and confirms the app can reach Supabase and Anthropic. If anything's missing or wrong, I'll tell you exactly which value and where to find it again — I will never silently fake a credential to keep moving (see `CLAUDE.md` Section 5).

---

## What happens later (no action needed now)

- **Phase 6:** you'll create a second, separate Supabase project called `docflow-prod` right before your first real customer. It shares nothing with staging — separate keys, separate storage, separate `.env`. I'll walk you through this the same way when we get there.
- **Phase 6:** Stripe test mode gets switched to live mode for real billing — another guided step, not something that happens automatically.
- **Ongoing:** if the Anthropic API ever changes its recommended model, or a library needs a security update, I'll flag it rather than silently upgrading something in the payment or extraction path.
