# Walkthrough — Phase 2: Matching and post-processing

Purpose: check what DocFlow does with an order after reading it. It links the
buyer, matches each line to the catalog, runs checks that warn but never
correct, and notices resends and revisions. Then teach it one SKU and watch
the next order use what it learned. About 25 minutes, two cents of model
calls.

**Phase 2's exit criteria** (Section 6): a corrected mapping on document 1
auto-matches document 2; the same PO number twice is flagged; totals that
don't reconcile warn and are not corrected.

## Before you start

- Phase 1 done: the `BCH-UAT-…` orders are in **Needs review** on Acme Test
  Distributor.
- Sign in as `walkthrough@example.test`.
- On the review screen each line has a small arrow (▾) at its right end. It
  opens the line's catalog match and checks; you'll use it a lot here.

---

## Part 1 — How lines match (5 min)

Open **BCH-UAT-06** (the `.txt` one). Open each line with its arrow.

| # | Line | Expect |
|---|---|---|
| 1 | CF-1001, CF-2210, SY-0045 | "Matched to the catalog (exact sku) · score 1.0000". The SKU on the order is in the catalog. |
| 2 | CUP-12 | "No catalog match." This tenant's catalog has **CUP-12X** "12oz Paper Cups 1000ct". DocFlow doesn't guess that the order means it. It may list CUP-12X under "Possible matches — none applied, because none scored high enough to be certain", or show nothing. Either way it's **not applied**. |

Two seeded orders show a match made on the description when the SKU is wrong:

| # | Order | Expect on line 1 |
|---|---|---|
| 3 | **BEL-4006** | SKU on the order `CHO-756`, matched by description to the catalog's Drinking Chocolate 2lb: "(fuzzy) · score …" |
| 4 | **SOU-4014** | SKU `LID-722`, matched by description to the 12oz Cup Lids |

Judge: is it clear on screen that line 1 was matched by description, not by
SKU, so a reviewer knows to glance at it?

## Part 2 — Teach it one SKU (5 min)

1. Still on **BCH-UAT-06**, open the CUP-12 line.
2. If CUP-12X is listed under possible matches, click **This one**. If not,
   type `cups` in **Search the catalog by SKU or description** → **Search** →
   click **Use this & remember it** beside **CUP-12X**.
3. **Expect:** the line now says it's matched to CUP-12X. Nothing else on the
   order changed.
4. Open **BCH-UAT-07** (the `.csv`). Its CUP-12 line is **still unmatched**.
   Teaching applies to new orders, never back to ones already read.

## Part 3 — The next order uses it (5 min)

1. **Upload** `walkthrough-files\later\22-po-BCH-UAT-22.txt`. Wait for it to
   reach **Needs review** (under a minute).
2. Open **BCH-UAT-22**, then the CUP-12 line.
   - **Expect:** matched to **CUP-12X** straight away, with the note **Filled
     in by a saved rule**. This is the Phase 2 exit criterion: a correction on
     one order matches the next one.
3. The rule is scoped to this buyer, Bella's Coffee House. The same wording
   from a different buyer would not be matched by it. The rule is visible in
   the Console (walkthrough [5.4](5.4-operator-screens.md)).

## Part 4 — Checks warn, and never change a value (5 min)

Each of these seeded orders has one deliberate problem. For each, open it,
read the **Checks** box (amber), and confirm **the values are exactly as
printed on the original**. DocFlow must not have "fixed" anything to make the
numbers add up.

| # | Order | The problem | Expect under Checks |
|---|---|---|---|
| 5 | **HAR-4009** | The printed total doesn't equal the lines | Order total doesn't match the line items (VAL-002). The total shown is the printed one. |
| 6 | **BEL-4012** | A quantity with a digit dropped | Line total doesn't match quantity times price (VAL-001), on that line |
| 7 | **NOR-4019** | Ordered in EA where the catalog sells CS | Unit of measure disagrees with the catalog (VAL-008). The line says "This line's unit doesn't match the catalog item it matched (catalog says …)". The unit is **not** converted. |
| 8 | **NOR-4007** | Currency only from a `$` sign | Currency was inferred from a symbol (VAL-011); currency's confidence is at most 60% |
| 9 | **MIL-4017** | A poor scan | Several "below our confidence threshold" checks (VAL-010); the order's overall confidence is low |

## Part 5 — Resends and revisions (5 min)

1. **Seeded resend:** in the queue, **MIL-4005** appears twice. The second
   carries the flag **Possible duplicate**. Open it: "This looks like a
   document we already have" (VAL-012). Both are kept; the first is already
   approved and untouched.
2. **Seeded revision:** **SOU-4002** also appears twice, one flagged **Possible
   change order**. Open it: "This may revise an earlier order" (VAL-013).
3. **Live resend:** **Upload** `good\06-po-BCH-UAT-06.txt` again (the same
   file as in Phase 1).
   - **Expect** on the upload page: "Received… This looks like one you already
     have — both are kept."
   - In the queue, a second BCH-UAT-06 with **Possible duplicate**.
4. **Live revision:** **Upload** `later\24-po-BCH-UAT-06-REVISED.txt`: the same
   PO number, 30 bags of Colombian instead of 12, total 2,211.00.
   - **Expect:** a third BCH-UAT-06 flagged **Possible change order**, with
     its own values (30, 2,211.00). The original still shows 12 and 1,356.00.
     Nothing was overwritten or deleted.
   - Its CUP-12 line is also pre-matched by Part 2's rule.
5. Reject the live duplicate from step 3: open it → **Reject** → reason
   `walkthrough: resend` → **Confirm rejection**. **Expect:** it moves to the
   **Rejected** tab, with the reason recorded.

---

## Results

| Part | Step | Pass / fail | Notes / screenshot |
|---|---|---|---|
| | | | |
