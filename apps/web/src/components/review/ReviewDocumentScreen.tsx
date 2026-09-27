"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ReviewApiError,
  approveDocument,
  createMapping,
  fieldStates,
  getDocument,
  rejectDocument,
  reopenDocument,
  saveEdits,
  type DocumentDetail,
  type DocumentWarning,
  type ExportFormat,
} from "@/lib/review";
import { DocumentViewer } from "@/components/review/DocumentViewer";
import {
  ExportPanel,
  FORMATS,
  readPreferredFormat,
  useExports,
  writePreferredFormat,
} from "@/components/review/ExportPanel";
import { HeaderFields } from "@/components/review/HeaderFields";
import { LineTable } from "@/components/review/LineTable";
import { TrailPanel } from "@/components/review/TrailPanel";
import { WarningsPanel, focusWarning } from "@/components/review/WarningsPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { ReviewChrome } from "@/components/review/ReviewChrome";
import { useReviewScope } from "@/lib/reviewScope";

/**
 * The review screen (CLAUDE.md Section 7.3 — "the most important UX" in
 * Section 6's phase plan).
 *
 * Side-by-side: the original document on the left, everything extracted from
 * it on the right. Nothing from the document is rendered as HTML anywhere on
 * this page — React escapes every value, the original is confined to a
 * sandboxed iframe, and there is no `dangerouslySetInnerHTML` in this
 * surface (Section 10).
 *
 * Keyboard-first, because a reviewer doing this fifty times a day should
 * never need the mouse: ⌘/Ctrl+S saves, ⌘/Ctrl+Enter approves, Escape
 * abandons an unsaved edit. Every one of those is also a visible button —
 * the shortcut is an accelerator, never the only way.
 *
 * The screen holds `version` from the last read and sends it with every
 * save. If someone else changed the order in the meantime the save is
 * refused as REV-005 rather than quietly overwriting their correction.
 */

type Banner = {
  // `warning` is for a save that went through and left something to look
  // at. Green next to a dead Approve button is the lie that started this:
  // the founder saved a bad unit price, saw "Saved", and had no way to know
  // a new check had appeared below the fold.
  kind: "error" | "success" | "warning";
  title: string;
  message: string;
  action?: string;
  link?: { href: string; label: string };
  /** Scrolls to one check and focuses its tick box. */
  jump?: { warningId: string; label: string };
};

export function ReviewDocumentScreen({ id }: { id: string }) {
  const scope = useReviewScope();
  // Where "back" goes. In the Console the founder is here from the tenant's
  // onboarding page and returns there to carry on (Mark complete, Go live);
  // the order list was a detour (founder feedback, D-116).
  const back = scope.tenantId
    ? { href: `/admin/tenants/${scope.tenantId}`, label: "← Tenant", long: "Back to the tenant" }
    : { href: scope.href(), label: "← Queue", long: "Back to the queue" };

  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [headerEdits, setHeaderEdits] = useState<Record<string, string | null>>({});
  const [lineEdits, setLineEdits] = useState<Record<string, Record<string, string | null>>>({});
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set());
  // Checks the reviewer's last save raised. Marked "New" in the panel and
  // counted in the banner; cleared by any read that isn't a save.
  const [appeared, setAppeared] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  // The one-step confirmation before a decided order goes back to review.
  const [reopening, setReopening] = useState(false);
  const [rejectNote, setRejectNote] = useState("");

  const dirty = Object.keys(headerEdits).length > 0 || Object.keys(lineEdits).length > 0;
  // The format "Approve & export" produces. Only rendered after the order
  // has loaded in the browser, so reading storage here cannot disagree with
  // server-rendered markup.
  const [exportFormat, setExportFormat] = useState<ExportFormat>(() => readPreferredFormat());

  // Adopting a freshly-read document: one place, so the mount path and the
  // after-a-write path cannot drift about which local state gets cleared.
  //
  // `keep` carries the ticks forward. A check whose numbers changed gets a
  // new fingerprint, and therefore a new row and a new id, on the server
  // (D-074) -- so it arrives here unticked, which is exactly what the
  // Section 7.3 gate is for. A check that is still the same row is still
  // the same statement about the same values, and making the reviewer tick
  // all of them again after every unrelated edit taught them only that
  // Approve turns off for no reason.
  const applyDetail = useCallback(
    (next: DocumentDetail, keep?: { acknowledged: Set<string>; appeared: Set<string> }) => {
      setDetail(next);
      setBanner(null);
      setHeaderEdits({});
      setLineEdits({});
      const stillOpen = new Set(next.warnings.filter((w) => w.status === "open").map((w) => w.id));
      setAcknowledged(new Set([...(keep?.acknowledged ?? [])].filter((id) => stillOpen.has(id))));
      setAppeared(new Set([...(keep?.appeared ?? [])].filter((id) => stillOpen.has(id))));
    },
    [],
  );

  // The mount read. Written as `.then` rather than an awaited call so no
  // state is set synchronously in the effect body, which would cascade a
  // render before the fetch had even started.
  useEffect(() => {
    let cancelled = false;
    getDocument(id)
      .then((next) => {
        if (!cancelled) applyDetail(next);
      })
      .catch((e) => {
        if (cancelled) return;
        setDetail(null);
        showError(e, setBanner);
      });
    return () => {
      cancelled = true;
    };
  }, [id, applyDetail]);

  // The re-read after a write. Called from event handlers, never an effect.
  // Returns what it read so a caller can say what changed.
  const load = useCallback(
    async (keep?: { acknowledged: Set<string>; appeared: Set<string> }) => {
      try {
        const next = await getDocument(id);
        applyDetail(next, keep);
        return next;
      } catch (e) {
        showError(e, setBanner);
        return null;
      }
    },
    [id, applyDetail],
  );

  const exportable =
    detail?.document.status === "approved" || detail?.document.status === "exported";
  // The first file moves the order to "exported"; re-read it so the badge says so.
  const onExported = useCallback(() => void load(), [load]);
  const exportState = useExports(id, exportable, onExported);

  const openWarnings = useMemo(
    () => (detail?.warnings ?? []).filter((w) => w.status === "open"),
    [detail],
  );
  // The checks the Approve button is waiting on. One list, so the hint by
  // the button, the strip below it and the gate itself can never disagree
  // about how many are left or which one is first. Naming that check on
  // screen is the defect this fixes: a disabled Approve with no reason.
  const blockingWarnings = openWarnings.filter((w) => !acknowledged.has(w.id));
  const everyWarningAcknowledged = blockingWarnings.length === 0;

  const save = useCallback(async () => {
    if (!detail || !dirty || busy) return;
    setBusy(true);
    // The checks as they stood before the write, to diff against.
    const before = openWarnings;
    try {
      await saveEdits(id, {
        header: headerEdits,
        lines: Object.entries(lineEdits).map(([line_id, fields]) => ({ line_id, fields })),
        expected_version: detail.version,
      });
      // The API re-runs Section 7.7 in the same transaction as the edit, so
      // this read is what the document now says about itself.
      const next = await load({ acknowledged, appeared: new Set() });
      if (!next) return;
      const nowOpen = next.warnings.filter((w) => w.status === "open");
      const beforeIds = new Set(before.map((w) => w.id));
      const nowIds = new Set(nowOpen.map((w) => w.id));
      const raised = nowOpen.filter((w) => !beforeIds.has(w.id));
      const cleared = before.filter((w) => !nowIds.has(w.id));
      setAppeared(new Set(raised.map((w) => w.id)));
      setBanner(savedBanner(raised, cleared));
    } catch (e) {
      showError(e, setBanner);
    } finally {
      setBusy(false);
    }
  }, [detail, dirty, busy, id, headerEdits, lineEdits, load, openWarnings, acknowledged]);

  const approve = useCallback(async (): Promise<boolean> => {
    if (!detail || busy) return false;
    if (dirty) {
      setBanner({
        kind: "error",
        title: "Save your changes first",
        message: "This order has edits that haven't been saved, so there's nothing to approve yet.",
        action: "Save, check the values, then approve.",
      });
      return false;
    }
    setBusy(true);
    try {
      await approveDocument(
        id,
        openWarnings.filter((w) => acknowledged.has(w.id)).map((w) => ({ warning_id: w.id, note: null })),
        detail.version,
      );
      await load();
      setBanner({
        kind: "success",
        title: "Approved",
        message: "This order is ready to export.",
        ...(scope.tenantId ? { link: { href: back.href, label: `${back.long} to carry on →` } } : {}),
      });
      return true;
    } catch (e) {
      // The API brings the checks up to date in its own transaction before
      // it decides (H2), so a refusal can be about a check this screen has
      // never seen. Re-read before saying anything, or the reviewer is told
      // "no" and shown nothing.
      const before = new Set(openWarnings.map((w) => w.id));
      const next = await load({ acknowledged, appeared });
      if (next) {
        const raised = next.warnings.filter((w) => w.status === "open" && !before.has(w.id));
        if (raised.length > 0) setAppeared(new Set(raised.map((w) => w.id)));
      }
      showError(e, setBanner);
      return false;
    } finally {
      setBusy(false);
    }
  }, [detail, busy, dirty, id, openWarnings, acknowledged, appeared, load, scope.tenantId, back.href, back.long]);

  // Back to Needs review, on purpose (D-144). Fields stay locked on a decided
  // order so a stray keystroke can never unapprove it; this is the way in.
  const reopen = useCallback(async () => {
    if (!detail || busy) return;
    const wasRejected = detail.document.status === "rejected";
    setBusy(true);
    try {
      await reopenDocument(id);
      setReopening(false);
      await load();
      setBanner({
        kind: "success",
        title: "Reopened for review",
        message: wasRejected
          ? "The rejection stays in the order's history."
          : "The approved copy is kept, and so is any file already exported from it.",
        action: "Change what you need, then approve it again.",
      });
    } catch (e) {
      showError(e, setBanner);
    } finally {
      setBusy(false);
    }
  }, [detail, busy, id, load]);

  // One click for the common case: approve, then download in the format
  // this browser last chose. Approval stays a separate, explicit action
  // underneath (Section 7.3) -- this only saves the second click, and an
  // approval that fails exports nothing.
  const approveAndExport = useCallback(async () => {
    if (await approve()) await exportState.run(exportFormat);
  }, [approve, exportState, exportFormat]);

  // Keyboard shortcuts. Every one has a visible button too.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      } else if (meta && e.key === "Enter") {
        e.preventDefault();
        void approve();
      } else if (e.key === "Escape" && dirty) {
        e.preventDefault();
        setHeaderEdits({});
        setLineEdits({});
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [save, approve, dirty]);

  if (detail === null) {
    return (
      <>
      <ReviewChrome />
      <main className="mx-auto max-w-6xl p-6">
        {banner ? <BannerView banner={banner} /> : <p className="text-sm text-gray-500">Loading…</p>}
        <Link href={back.href} className="mt-4 inline-block text-sm text-blue-700 underline">
          {back.long}
        </Link>
      </main>
      </>
    );
  }

  const readOnly = !detail.can_edit || detail.document.status !== "needs_review";
  const canReopen = detail.can_edit && REOPENABLE.has(detail.document.status);

  return (
    <>
    <ReviewChrome />
    <main className="mx-auto max-w-[110rem] p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <Link href={back.href} data-testid="review-back" className="text-sm text-blue-700 underline">
            {back.label}
          </Link>
          <h1 className="text-lg font-semibold">
            {detail.header.po_number ?? detail.document.original_filename}
          </h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-gray-600">
            <span>{detail.header.buyer_name ?? "Buyer not read"}</span>
            <StatusBadge status={detail.document.status} />
          </p>
        </div>

        <div className="flex flex-col items-end gap-1">
          <div className="flex flex-wrap items-center gap-2">
            {canReopen ? (
              <button
                type="button"
                onClick={() => setReopening((r) => !r)}
                disabled={busy}
                data-testid="reopen-button"
                className="rounded border border-slate-400 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-50 disabled:opacity-50"
              >
                Reopen for review
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void save()}
              disabled={!dirty || busy || readOnly}
              data-testid="save-button"
              title={
                !dirty ? "Nothing to save yet — this saves changes you make to the values" : undefined
              }
              className="rounded border border-gray-300 px-3 py-1.5 text-sm disabled:text-gray-400"
            >
              {dirty ? "Save changes" : "Save"} <kbd className="text-xs text-gray-500">⌘S</kbd>
            </button>
            <button
              type="button"
              onClick={() => setRejecting((r) => !r)}
              disabled={busy || readOnly}
              className="rounded border border-gray-300 px-3 py-1.5 text-sm disabled:text-gray-400"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={() => void approve()}
              disabled={busy || readOnly || !everyWarningAcknowledged}
              data-testid="approve-button"
              className="rounded bg-green-700 px-3 py-1.5 text-sm font-medium text-white disabled:bg-gray-300"
            >
              Approve <kbd className="text-xs opacity-80">⌘↵</kbd>
            </button>
            <span className="inline-flex items-stretch overflow-hidden rounded bg-green-800 text-sm font-medium text-white has-[button:disabled]:bg-gray-300">
              <button
                type="button"
                onClick={() => void approveAndExport()}
                disabled={busy || readOnly || !everyWarningAcknowledged || exportState.working !== null}
                data-testid="approve-export-button"
                className="px-3 py-1.5 disabled:text-white"
              >
                Approve &amp; export
              </button>
              <label htmlFor="approve-export-format" className="sr-only">
                Export format
              </label>
              <select
                id="approve-export-format"
                data-testid="approve-export-format"
                value={exportFormat}
                onChange={(e) => {
                  const next = e.target.value as ExportFormat;
                  setExportFormat(next);
                  writePreferredFormat(next);
                }}
                disabled={busy || readOnly}
                className="border-l border-white/30 bg-transparent px-1.5 text-sm text-white disabled:text-white [&>option]:text-gray-900"
              >
                {FORMATS.map(({ format, label }) => (
                  <option key={format} value={format}>
                    {label}
                  </option>
                ))}
              </select>
            </span>
          </div>

          {/*
            A disabled button with no reason reads as a broken button. The
            first walkthrough tester ticked a check, pressed Save, and saw
            nothing happen -- Save was disabled because ticking a check is
            not an edit, and nothing on screen said so.
          */}
          <p data-testid="action-hint" className="text-right text-xs text-gray-600">
            {actionHint({
              readOnly,
              status: detail.document.status,
              canEdit: detail.can_edit,
              dirty,
              remaining: blockingWarnings.length,
              canReopen,
            })}
          </p>
        </div>
      </div>

      {detail.document.failure ? (
        <div
          data-testid="failure-banner"
          role="alert"
          className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900"
        >
          <p className="font-medium">{detail.document.failure.title}</p>
          <p className="mt-1">{detail.document.failure.message}</p>
          <p className="mt-1 text-red-900/80">{detail.document.failure.action}</p>
        </div>
      ) : null}

      {reopening ? (
        <div
          data-testid="reopen-confirm"
          className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm"
        >
          <p>
            {detail.document.status === "rejected"
              ? "Reopen this order? It goes back to Needs review, to be checked and approved or rejected again. The rejection stays in its history."
              : "Reopen this order? It goes back to Needs review and has to be approved again before it can be exported. The approved copy, and any file already exported from it, is kept."}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => void reopen()}
              disabled={busy}
              data-testid="reopen-confirm-button"
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Reopen
            </button>
            <button
              type="button"
              onClick={() => setReopening(false)}
              disabled={busy}
              className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {detail.document.injection_suspected ? (
        <div
          data-testid="injection-banner"
          role="alert"
          className="mt-3 rounded border border-red-400 bg-red-50 p-3 text-sm"
        >
          <p className="font-medium">This document contains an embedded instruction</p>
          <p className="mt-1">
            Text in this document tried to give DocFlow&apos;s extraction instructions instead of
            being order data. It was ignored, not followed. Read every field against the original
            before approving.
          </p>
        </div>
      ) : null}

      {detail.document.examples_used > 0 ? (
        <p
          data-testid="examples-note"
          className="mt-3 rounded border border-gray-200 bg-gray-50 p-2 text-xs text-gray-700"
        >
          Read with {detail.document.examples_used} earlier approved{" "}
          {detail.document.examples_used === 1 ? "order" : "orders"} from this customer as examples
          of their layout. Every value still comes from this document alone; check it against the
          original as usual.
        </p>
      ) : null}

      {banner ? <div className="mt-3"><BannerView banner={banner} /></div> : null}

      {/*
        Why Approve is off, in the reviewer's line of sight. The checks panel
        is below the line table and on a normal window it is never on screen
        at the same time as the button it controls -- so a check raised by an
        edit used to show up as nothing but a button that stopped working.
      */}
      {!readOnly && blockingWarnings.length > 0 ? (
        <div
          data-testid="approve-blocked"
          role="status"
          className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg border border-amber-400 bg-amber-100/80 p-3 text-sm text-amber-950"
        >
          <span className="font-medium">
            Approve is off until every check is ticked — {blockingWarnings.length}{" "}
            {blockingWarnings.length === 1 ? "check" : "checks"} left.
          </span>
          <span data-testid="approve-blocked-by">First: {warningLabel(blockingWarnings[0])}</span>
          <button
            type="button"
            onClick={() => focusWarning(blockingWarnings[0].id)}
            data-testid="approve-blocked-show"
            className="font-medium text-blue-800 underline"
          >
            Show it →
          </button>
        </div>
      ) : null}

      {rejecting ? (
        <form
          className="mt-3 rounded border border-gray-300 p-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await rejectDocument(id, rejectNote);
              setRejecting(false);
              setRejectNote("");
              await load();
            } catch (err) {
              showError(err, setBanner);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label htmlFor="reject-note" className="block text-sm font-medium">
            Why are you rejecting this order?
          </label>
          <input
            id="reject-note"
            value={rejectNote}
            onChange={(e) => setRejectNote(e.target.value)}
            required
            data-testid="reject-note"
            className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
          />
          <button
            type="submit"
            disabled={busy || !rejectNote.trim()}
            className="mt-2 rounded border border-gray-300 px-3 py-1 text-sm disabled:text-gray-400"
          >
            Confirm rejection
          </button>
        </form>
      ) : null}

      {/* Side by side only where both halves have room (xl, 1280px); below
          that the original stacks above the fields, each at full width. The
          fields get the larger share: the line table needs it more than the
          viewer, which scrolls and zooms on its own. */}
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[5fr_7fr]">
        <div className="xl:sticky xl:top-4 xl:h-[calc(100vh-6rem)]">
          <DocumentViewer key={id} documentId={id} filename={detail.document.original_filename} />
        </div>

        <div className="min-w-0 space-y-6">
          <ExportPanel state={exportState} exportable={exportable} />

          <HeaderFields
            header={detail.header}
            edits={headerEdits}
            states={fieldStates(detail.field_schema, "header")}
            disabled={readOnly}
            onChange={(field, value) => setHeaderEdits((prev) => ({ ...prev, [field]: value }))}
          />

          <LineTable
            lines={detail.lines}
            edits={lineEdits}
            warnings={detail.warnings}
            disabled={readOnly}
            onChange={(lineId, field, value) =>
              setLineEdits((prev) => ({
                ...prev,
                [lineId]: { ...(prev[lineId] ?? {}), [field]: value },
              }))
            }
            onConfirmMapping={async (lineId, itemId) => {
              try {
                await createMapping(id, lineId, itemId);
                // Same rule as a save: a check the mapping did not change is
                // still ticked afterwards.
                await load({ acknowledged, appeared });
              } catch (e) {
                showError(e, setBanner);
              }
            }}
          />

          <WarningsPanel
            warnings={detail.warnings}
            acknowledged={acknowledged}
            appeared={appeared}
            disabled={readOnly}
            onToggle={(warningId, checked) =>
              setAcknowledged((prev) => {
                const next = new Set(prev);
                if (checked) next.add(warningId);
                else next.delete(warningId);
                return next;
              })
            }
          />

          <TrailPanel trail={detail.trail} />
        </div>
      </div>
    </main>
    </>
  );
}

// Decided orders a reviewer may send back to Needs review (D-144).
const REOPENABLE = new Set(["approved", "exported", "rejected"]);

// A status in words, for the "can't be reviewed yet" hint -- never the raw
// column value.
const NOT_READY: Record<string, string> = {
  staged: "uploaded but not run yet",
  pending: "waiting to be read",
  processing: "being read",
  quarantined: "held for review",
};

/**
 * Why the buttons are in the state they are in, in one sentence.
 *
 * Every branch here corresponds to something a walkthrough tester actually
 * hit and could not explain from the screen.
 */
function actionHint({
  readOnly,
  status,
  canEdit,
  dirty,
  remaining,
  canReopen,
}: {
  readOnly: boolean;
  status: string;
  canEdit: boolean;
  dirty: boolean;
  remaining: number;
  canReopen: boolean;
}): string {
  if (!canEdit) {
    return "Your account can view orders but not change them.";
  }
  // The hint used to say "Editing any value reopens it" while every field was
  // locked -- a promise the screen couldn't keep (D-144).
  if ((status === "approved" || status === "exported") && canReopen) {
    return "Already approved. To change a value, reopen it for review first.";
  }
  if (status === "rejected" && canReopen) {
    return "This order was rejected. Reopen it to review it again.";
  }
  if (status === "failed") {
    return "DocFlow couldn't read this order, so there's nothing to review. The reason is above.";
  }
  if (readOnly) {
    return `This order is ${NOT_READY[status] ?? status}, so it can't be reviewed yet.`;
  }
  if (remaining > 0) {
    return `${remaining} check${remaining === 1 ? "" : "s"} still to tick below before you can approve.`;
  }
  if (dirty) {
    return "You have unsaved changes — save them, then approve.";
  }
  return "Everything checked. Ready to approve.";
}

/** One check in a sentence: where it is and what it says. */
function warningLabel(warning: DocumentWarning): string {
  const where = warning.line_number !== null ? `Line ${warning.line_number} · ` : "";
  return `${where}${warning.title || warning.code}`;
}

/**
 * What a save did to the checks.
 *
 * The screen used to say "Saved · Your changes are recorded" whatever
 * happened, in green, while Approve went dead and the check that killed it
 * sat a thousand pixels below the fold. A save that raises a check is
 * reported as such, in amber, with a way to get to it.
 */
function savedBanner(raised: DocumentWarning[], cleared: DocumentWarning[]): Banner {
  const clearedNote =
    cleared.length > 0
      ? ` ${cleared.length} earlier ${cleared.length === 1 ? "check no longer applies" : "checks no longer apply"}.`
      : "";

  if (raised.length === 0) {
    return {
      kind: "success",
      title: "Saved",
      message: `Your changes are recorded.${clearedNote}`,
    };
  }

  return {
    kind: "warning",
    title: `Saved — ${raised.length} new ${raised.length === 1 ? "check" : "checks"} to look at`,
    message:
      `Your change raised ${raised.length === 1 ? "a check" : `${raised.length} checks`}: ` +
      `${raised.map(warningLabel).join("; ")}.${clearedNote}`,
    action: "Nothing was altered for you — read each one against the original, then tick it.",
    jump: {
      warningId: raised[0].id,
      label: raised.length === 1 ? "Show the check →" : "Show the first one →",
    },
  };
}

const BANNER_STYLES: Record<Banner["kind"], string> = {
  error: "border-red-300 bg-red-50",
  success: "border-green-300 bg-green-50",
  warning: "border-amber-400 bg-amber-100/80 text-amber-950",
};

function BannerView({ banner }: { banner: Banner }) {
  return (
    <div
      role="alert"
      data-testid={`banner-${banner.kind}`}
      className={["rounded border p-3 text-sm", BANNER_STYLES[banner.kind]].join(" ")}
    >
      <p className="font-medium">{banner.title}</p>
      <p className="mt-1">{banner.message}</p>
      {banner.action ? <p className="mt-1 text-gray-700">{banner.action}</p> : null}
      {banner.jump ? (
        <button
          type="button"
          onClick={() => focusWarning(banner.jump!.warningId)}
          data-testid="banner-jump"
          className="mt-2 inline-block font-medium text-blue-800 underline"
        >
          {banner.jump.label}
        </button>
      ) : null}
      {banner.link ? (
        <Link href={banner.link.href} className="mt-2 inline-block font-medium text-blue-700 underline">
          {banner.link.label}
        </Link>
      ) : null}
    </div>
  );
}

/**
 * Renders a backend failure from its catalog entry. The UI never writes its
 * own sentence for a backend failure (Section 7.16.5).
 */
function showError(e: unknown, setBanner: (b: Banner) => void) {
  const error = e as ReviewApiError;
  setBanner({
    kind: "error",
    title: error?.catalog?.title ?? "Something needs your attention",
    message: error?.catalog?.message ?? "",
    action: error?.catalog?.action,
  });
}

