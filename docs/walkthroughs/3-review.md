# Walkthrough — Phase 3: The review screen

Purpose: the screen a customer's reviewer uses fifty times a day. Check that
a mistake can be found and fixed quickly, that nothing is approved without a
person saying so, and that every change is recorded with its before and
after. About 30 minutes. No model calls.

**Phase 3's exit criteria** (Section 6, met 17 Sept): a non-technical person
corrects and approves the golden fixture in under 2 minutes, and the audit
trail shows exactly what changed. This is the second look, and the first for
the fixes since.

## Before you start

- Phase 0's seed script added a fresh golden order to Acme Test Distributor:
  PO **BCH-2291**, Bella's Coffee House, with one planted misread. The original
  says **24** on line 3; the data says **2**.
- **Best of all:** hand Part 2 to someone who has never seen DocFlow, and time
  them. Otherwise do it yourself.
- Sign in as `walkthrough@example.test`.

---

## Part 1 — The queue (4 min)

Header → **Purchase orders**.

| # | Check | Expect |
|---|---|---|
| 1 | The tabs | **Needs review · Approved · Rejected · Exported to file · Couldn't be read · All**, each with a one-line explanation beneath when chosen |
| 2 | The columns | PO number, Buyer, Total, Received, Status, Confidence, Checks, Flags |
| 3 | Order | Oldest first: the order a buyer has waited longest for is at the top |
| 4 | Flags | Possible duplicate, Possible change order and Embedded instruction show on the row, without opening it |
| 5 | Paging | Below the table, page controls and the total count. Acme Test Distributor now has more than 50 orders, so there's a second page. |
| 6 | Low confidence | A low-confidence order (e.g. MIL-4017) says so in the Confidence column |

## Part 2 — The two-minute test (3 min, timed)

Start the clock when the order opens; stop it when it says Approved.

1. Open the newest **BCH-2291** (Needs review).
2. Compare line 3 with the original on the left. The original says 24; the
   field says 2. Click the quantity, type `24`.
3. **Save changes** (or Ctrl+S). **Expect:** a green "Saved — Your changes are
   recorded." The field is marked **Edited by a person**.
4. Look at **Checks — N to tick**. The line-total and order-total warnings
   came from the misread. **They don't clear themselves after a fix** (a known
   open item, D-115). Tick each one after reading it. **Expect** the hint under
   the buttons to count down: "N checks still to tick below before you can
   approve."
5. **Approve** (or Ctrl+Enter). **Expect:** "Approved — This order is ready to
   export." The status badge says **Approved**.

**Record the time.** Under 2 minutes is the bar. Also record where the person
hesitated, even if they made it.

## Part 3 — The history says exactly what changed (3 min)

Scroll to **History** on the same order.

- **Expect**, in order: **Edited** (line 3, quantity, 2 → 24, by
  walkthrough@example.test, with the time), a record of each check you ticked
  **with the warning's own text**, and **Approved** by the same person.
- **Fail if** any entry lacks who or when, or the edit doesn't show both the
  old and the new value.

## Part 4 — Nothing gets approved by accident (8 min)

Use any other **Needs review** order that has checks, e.g. **HAR-4009**.

| # | Do this | Expect |
|---|---|---|
| 7 | Arrive without changing anything | **Save** is greyed out. Hovering it says "Nothing to save yet — this saves changes you make to the values". **Approve** is greyed out while checks are unticked. |
| 8 | Tick a check, then press Save | Nothing to save: ticking a check isn't an edit. The note in the Checks box says so: "These don't need saving. Tick each one…" |
| 9 | Change a value but don't save; tick every check; click Approve | "Save your changes first": DocFlow won't approve what you haven't saved. |
| 10 | Press **Escape** | Your unsaved edit is dropped; the value is back as it was. |
| 11 | **Reject** → leave the reason empty | **Confirm rejection** stays greyed out. A reason is required. |
| 12 | Click **Reject** again to close it without rejecting | The order is still Needs review. |

## Part 5 — Editing after approval reopens it (4 min)

Section 7.3: if an order needs changing after approval, it goes back to
Needs review and must be approved again; the old approved copy is kept.
Fields stay locked on a decided order, so a stray keystroke can't unapprove it.
**Reopen for review** is the deliberate way back in (added 25 Sept, D-144).

1. Go back to the **BCH-2291** you approved in Part 2. **Expect** every field
   locked, and the hint "Already approved. To change a value, reopen it for
   review first."
2. **Reopen for review**. **Expect** an amber box asking you to confirm: it goes
   back to Needs review, has to be approved again before it can be exported,
   and the approved copy and any exported file are kept. **Cancel** closes it
   with nothing changed.
3. **Reopen for review** → **Reopen**. **Expect:** "Reopened for review", status
   **Needs review**, the fields editable, and **Reopened for review** in
   History, by you.
4. Change **Notes** to `walkthrough reopen`, save, tick any checks, **Approve**
   again. Phase 4 will show exports from the first approval marked **Earlier
   approval**.
5. Open the **Rejected** resend from Phase 2. **Expect** the hint "This order
   was rejected. Reopen it to review it again." and the same button. Its
   confirmation says the rejection stays in its history. You can leave it
   rejected.

## Part 6 — Two people, one order (3 min)

1. Open the same Needs review order in two browser tabs.
2. In tab A, change a value and **Save**.
3. In tab B (not reloaded), change a *different* value and **Save**.
   - **Expect:** "Someone else changed this order first" (REV-005). Tab B's
     save is refused, so A's work isn't silently overwritten. Reload tab B:
     A's change is there.

## Part 7 — The planted instruction (2 min)

Open **BCH-UAT-23** from Phase 1: the order with the embedded instruction.

- **Expect:** the red banner, and the embedded-instruction check (VAL-009) in
  red among the Checks.
- Approving it would take a deliberate tick on that check. Instead **Reject**
  it with the reason `walkthrough: planted instruction`.

## Part 8 — Feel (2 min)

Not pass/fail. Note anything that slowed you down.

- On a narrow window (under ~1280 px wide) the original stacks above the
  fields. Wider, they sit side by side.
- The document viewer zooms and scrolls on its own. You said you like it; it
  should be unchanged.
- Colours: lavender = order details, light blue = line items, amber = checks.

---

## Results

| Part | Step | Pass / fail | Notes / screenshot |
|---|---|---|---|
| | | | |

Two-minute test: ______ seconds, by ______ (first time seeing DocFlow? yes / no)
