# Walkthrough — Phase 4: Export

Purpose: get an approved order out of DocFlow in each of the four formats, and
check each file says exactly what was approved: every digit, every line, the
order details on every row. Also check that exporting twice gives the
identical file. About 20 minutes. No model calls.

**Phase 4's exit criteria** (Section 6): the exported file, parsed back,
equals the approved data exactly (the round-trip test, automated for all four
formats and also run on every real export); exporting twice produces
byte-identical files.

## Before you start

- Phase 3 done: **BCH-2291** approved on Acme Test Distributor.
- Sign in as `walkthrough@example.test`.
- Have Excel (or LibreOffice) and Notepad to hand. Downloads land in your
  Downloads folder.

---

## Part 1 — Four formats (10 min)

Open the approved **BCH-2291**. The **Export** panel sits above the order
details: "Download this approved order to import into your own system." There
are four buttons, each with a tooltip.

| # | Click | Open the file and expect |
|---|---|---|
| 1 | **CSV** | "Preparing…", then the file downloads by itself. In Notepad: a heading row, then **4 rows, one per line item**, with the PO number, buyer, dates and total **repeated on every row**. Amounts exactly as approved: `47.50`, not `47.5`. Line 3's quantity is **24** (your correction), not 2. |
| 2 | **Excel** | The same layout as a spreadsheet. Amounts exact to the cent. Nothing turned into a date or a formula. |
| 3 | **JSON** | In Notepad: the order details once, then the lines. Money is in quotes (`"47.50"`), text, never a rounded number. |
| 4 | **QuickBooks (IIF)** | A text file with `TRNS` / `SPL` rows: an estimate for QuickBooks Desktop. |

After each: the export history under the buttons gains a row with the format,
time, who (`walkthrough@example.test`) and **Download (size)**.

- **Still open:** nobody has imported the IIF into real QuickBooks Desktop
  yet (UAT TC-26). If you find someone with it, this file is the one to try.

## Part 2 — Same order, same bytes (5 min)

1. Click **CSV** again. A second CSV row appears in the history.
2. Download both (each row's **Download**). The second may be saved as
   `…(1).csv`.
3. In PowerShell, in your Downloads folder:

   ```
   Get-FileHash .\<first>.csv, .\<second>.csv
   ```

   **Expect:** the two hashes are identical. Do the same for **Excel**, which
   was the format that once differed between Windows and Linux.
4. Back on **Purchase orders**, BCH-2291 is now under **Exported to file**.

## Part 3 — Approve and export in one go (3 min)

1. Open another Needs review order with no checks, e.g. **NOR-4001** or
   **CED-4004**.
2. Beside **Approve** is **Approve & export** with a format picker. Choose
   **Excel**, click **Approve & export**.
   - **Expect:** "Approved" and the Excel file downloads. The order goes
     straight to **Exported to file**.
3. Open another clean order. **Expect** the picker still says **Excel**: it
   remembers your last choice in this browser.

## Part 4 — QuickBooks refuses what it can't import (3 min)

1. Open **BCH-UAT-04** (the `.xlsx` from Phase 1, which carries no order date).
   Tick its checks and **Approve**.
2. Click **QuickBooks (IIF)**.
   - **Expect:** a red box, **QuickBooks can't import this order yet**
     (EXP-006). It says QuickBooks Desktop only imports an order with a buyer
     name and an order date whose lines add up to the total, and suggests
     checking those or downloading CSV or Excel. No IIF file is made.
   - This is the defect found by driving the real app in Phase 4: an order
     with no date used to produce an IIF with a blank date, which QuickBooks
     rejects.
3. Click **CSV** on the same order. **Expect** it works; CSV has no such
   rule.
4. Now fix it properly: **Reopen for review** → **Reopen**, fill **Order
   date** `2026-03-14`, save, approve, and click **QuickBooks (IIF)** again.
   **Expect** the IIF file this time. The CSV from step 3 stays in the history,
   marked **Earlier approval**.

## Part 5 — Only approved orders export (1 min)

Open any **Needs review** order. **Expect: no Export panel at all.** Nothing
unapproved can leave DocFlow.

---

## Results

| Part | Step | Pass / fail | Notes / screenshot |
|---|---|---|---|
| | | | |
