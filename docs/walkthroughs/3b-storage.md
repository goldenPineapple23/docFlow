# Walkthrough — Stage 3b, files in Supabase Storage

For: the founder, with me. About 30 minutes. Everything runs against
`docflow-staging` and its bucket `docflow-files`, with test data only.
Design and decisions: BUILD-STATUS "3b detailed design" and "3b build",
DECISIONS.md D-182.

## Before we start (done by me)

- The API (port 8000), the worker, beat (the 5-minute sweeps) and the web app
  (port 3000) are running from branch `phase55/stage3b-design`.
- Open **http://localhost:3000**, not 127.0.0.1 (the page never becomes
  clickable on 127.0.0.1).
- Sample files, outside the repo:
  - POs: `DocFlow\walkthrough-files\good\`
  - catalogs: `DocFlow\catalog-samples\`
  - customer list: `DocFlow\walkthrough-files\onboarding\`

After each step, tell me it's done. I then check the bucket and the database
behind it, and say what I found.

## 1 — Upload a PO, see the original in review

1. Sign in as `walkthrough@example.test` / `DemoPass-2026`.
2. Upload `walkthrough-files\good\06-po-BCH-UAT-06.txt`. If it says it's a
   possible duplicate, upload `03-po-BCH-UAT-03.docx` instead.
3. **Expect:** it reaches review within about a minute.
4. Open it. **Expect:** the original shows in the left-hand viewer.
5. I check: the original is in the bucket under this tenant's `uploads/`
   folder. Its preview and extracted text are under
   `derived/{document id}/`, the fixed names.

## 2 — Approve, export, download

1. Approve it (acknowledge any check it raises).
2. Export as **CSV**, then download it.
3. **Expect:** the file downloads, and its PO number and lines match what
   you approved.
4. I check: the export is under this tenant's `exports/` folder, and its
   SHA-256 matches the one recorded.

## 3 — Import a catalog in the Console

1. Sign in to `/admin` with your own account and your authenticator.
2. Pick any test tenant → **Catalog** → upload
   `catalog-samples\1-clean-catalog.xlsx`.
3. Go through the preview and mapping, then **Commit**.
4. **Expect:** the import commits with its row counts.
5. I check: the file is in the bucket under that tenant's `uploads/` folder.

## 4 — Create a tenant from a staged intake

1. Console → **Intakes** → new intake, prospect name
   **"Acme Test Storage Walkthrough"**.
2. Upload `walkthrough-files\onboarding\customer-list.csv` and
   `catalog-samples\2-mistakes-catalog.csv`.
3. I check: both files are in the bucket under `staging/{intake id}/`.
4. **Create tenant** from that intake, with the same name and any tier.
5. **Expect:** the tenant is created.
6. I check both of these:
   - the files were copied inside Storage to the new tenant's
     `onboarding/` folder;
   - the `staging/{intake id}/` originals are gone.

## 5 — Hard delete that tenant; its folder must be empty

1. The new tenant → **Lifecycle** → reason **For cause**, a note of 20 or
   more characters → **Cancel this tenant…** → confirm with your
   authenticator.
2. Wait for the sweep, up to 5 minutes. **Expect:** it appears under
   **Lifecycle → Wind-down queue**.
3. Tell me. I move its deletion date into the past in the database, as the
   5.6 walkthrough did, because the real window is 30 days.
4. Reload **Lifecycle**. **Expect:** it is under **Ready to delete**.
5. Click **Delete…**, type the exact name
   `Acme Test Storage Walkthrough` and a reason, then click **Permanently
   delete** (with your authenticator).
6. **Expect:** it leaves Ready to delete. Its tenant row and lifecycle
   events stay, by design (D-123).
7. I check both of these:
   - `tenants/{its id}/` is **empty** in the bucket;
   - the deletion event records how many objects were removed.

   Before 3b, this step would have failed with LIFE-007 every time; this
   check is the one that proves the fix.

## 6 (optional) — An order whose stored path is bad

1. Console → tenant **Acme Test Coffee Supply** → **Test batch** → open any
   order named `seed_merge_demo.txt`. Its stored path is made up, left by
   an old seed script.
2. **Expect:** the review screen opens normally, and the viewer says the
   original can't be shown. No error page, no SYS-001.
3. I check: the API log has `storage_path_refused` for that order, and no
   `storage_path_cross_tenant` alert was raised.

## Results

| Step | Pass / fail | Notes |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
