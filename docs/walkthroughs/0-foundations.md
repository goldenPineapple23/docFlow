# Walkthrough — Phase 0: Foundations

Purpose: check the walls the rest of the product stands on. Nobody can sign
themselves up, a customer never sees the Console, and one tenant can never see
another's orders. About 15 minutes. No model calls.

**Phase 0's exit criteria** (build prompt Section 6): the founder creates a
tenant from the Console and a customer signs in by invite; `/signup` is 404; a
tenant user's cross-tenant read fails and the founder's succeeds, leaving an
audit row; CI blocks a failing test. The invite part is walked in 5.1 and 5.3.

## Before you start

- All four processes running (see [README](README.md)).
- Set a known password on the reviewer login, and add a fresh golden order for
  Phase 3 at the same time. From the repo folder:

  ```
  apps\api\.venv\Scripts\python.exe scripts\seed_review_walkthrough.py walkthrough@example.test DemoPass-2026
  ```

  **Expect:** it ends with a box saying "Ready", the login URL, the email, the
  password, and a line that the order "says 24 on line 3; the data says 2".
  Keep that for Phase 3.

---

## Part 1 — Signing in (4 min)

| # | Do this | Expect |
|---|---|---|
| 1 | Go to `http://localhost:3000/signup` | The standard 404 page ("This page could not be found"). There is no signup page anywhere. |
| 2 | Go to `http://localhost:3000/login` | "Sign in to DocFlow" and a line saying accounts are created by invite only. No "create an account" link. |
| 3 | Sign in as `walkthrough@example.test` with a wrong password | "We couldn't sign you in with that email and password." It doesn't say whether the email exists. |
| 4 | Sign in with `DemoPass-2026` | You land on **Purchase orders** for **Acme Test Distributor**. The header shows the company name, "Powered by" and the DocFlow logo. |
| 5 | Next time you type a password, click **Show** in the password box | The password becomes readable and the button says **Hide**. |

## Part 2 — A customer can't find the Console (3 min)

Still signed in as `walkthrough@example.test`:

| # | Do this | Expect |
|---|---|---|
| 6 | Go to `http://localhost:3000/admin` | The same 404 page. Not "access denied": a customer mustn't even learn the Console exists. |
| 7 | Go to `/admin/tenants` and `/admin/lifecycle` | The same 404 for both. |
| 8 | Sign out (header, top right) | You're back at sign-in. |
| 9 | In a private window, signed out, go to `/review` | You're sent to sign in, and after signing in you come back to `/review` (D-134). |

## Part 3 — One tenant can't see another's orders (5 min)

1. In your normal window, sign in as **yourself**. **Expect:** you land in the
   Console (dark bar, "Console" next to the logo, links **Attention · Intakes ·
   Tenants · Lifecycle · Outbox**).
2. Open a different tenant's order in the Console: **Tenants** → **Acme Test
   Prospect** → **Orders →** (first link under "Catalog, customers and rules",
   added 25 Sept, D-147). Open any order and copy its id: the last part of the
   address.
3. In the private window, signed in as `walkthrough@example.test`, go to
   `http://localhost:3000/review/<that order id>`.
   - **Expect:** "We can't find that order" (REV-006). It says nothing about
     the order or its owner. It must not show any of Acme Test Prospect's data.
4. Also try a made-up id, e.g. `/review/12345`. **Expect** the same message,
   not an error page.
5. Back as yourself: **Tenants** → **Acme Test Prospect** → **Audit →**. Tick
   **Also show Console page views**.
   - **Expect:** a recent row for your read of that order, by you, labelled
     **DocFlow support**. Every look across the wall leaves a record.

## Part 4 — Housekeeping checks (3 min)

| # | Do this | Expect |
|---|---|---|
| 10 | Open the repo on GitHub → **Actions** | The latest run on `main` is green. (Several local commits aren't pushed yet; CI has only seen what's pushed.) |
| 11 | Ask me to run `git ls-files .env` | No output: the real `.env` has never been committed. `.env.example` is committed and documents every setting. |

---

## Results

| Part | Step | Pass / fail | Notes / screenshot |
|---|---|---|---|
| | | | |
