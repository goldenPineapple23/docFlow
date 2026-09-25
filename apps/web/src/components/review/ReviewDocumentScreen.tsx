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
  saveEdits,
  type DocumentDetail,
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
import { WarningsPanel } from "@/components/review/WarningsPanel";
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
  kind: "error" | "success";
  title: string;
  message: string;
  action?: string;
  link?: { href: string; label: string };
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
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [rejectNote, setRejectNote] = useState("");

  const dirty = Object.keys(headerEdits).length > 0 || Object.keys(lineEdits).length > 0;
  // The format "Approve & export" produces. Only rendered after the order
  // has loaded in the browser, so reading storage here cannot disagree with
  // server-rendered markup.
  const [exportFormat, setExportFormat] = useState<ExportFormat>(() => readPreferredFormat());

  // Adopting a freshly-read document: one place, so the mount path and the
  // after-a-write path cannot drift about which local state gets cleared.
  const applyDetail = useCallback((next: DocumentDetail) => {
    setDetail(next);
    setBanner(null);
    setHeaderEdits({});
    setLineEdits({});
    setAcknowledged(new Set());
  }, []);

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
  const load = useCallback(async () => {
    try {
      applyDetail(await getDocument(id));
    } catch (e) {
      showError(e, setBanner);
    }
  }, [id, applyDetail]);

  const exportable =
    detail?.document.status === "approved" || detail?.document.status === "exported";
  // The first file moves the order to "exported"; re-read it so the badge says so.
  const onExported = useCallback(() => void load(), [load]);
  const exportState = useExports(id, exportable, onExported);

  const openWarnings = useMemo(
    () => (detail?.warnings ?? []).filter((w) => w.status === "open"),
    [detail],
  );
  const everyWarningAcknowledged = openWarnings.every((w) => acknowledged.has(w.id));

  const save = useCallback(async () => {
    if (!detail || !dirty || busy) return;
    setBusy(true);
    try {
      await saveEdits(id, {
        header: headerEdits,
        lines: Object.entries(lineEdits).map(([line_id, fields]) => ({ line_id, fields })),
        expected_version: detail.version,
      });
      await load();
      setBanner({ kind: "success", title: "Saved", message: "Your changes are recorded." });
    } catch (e) {
      showError(e, setBanner);
    } finally {
      setBusy(false);
    }
  }, [detail, dirty, busy, id, headerEdits, lineEdits, load]);

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
        openWarnings
          .filter((w) => acknowledged.has(w.id))
          .map((w) => ({
            warning_id: w.id,
            code: w.code,
            // The text recorded is what the reviewer saw on screen.
            text: describeWarning(w.code, w.detail),
            note: null,
          })),
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
      showError(e, setBanner);
      return false;
    } finally {
      setBusy(false);
    }
  }, [detail, busy, dirty, id, openWarnings, acknowledged, load, scope.tenantId, back.href, back.long]);

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
              remaining: openWarnings.filter((w) => !acknowledged.has(w.id)).length,
            })}
          </p>
        </div>
      </div>

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
                await load();
              } catch (e) {
                showError(e, setBanner);
              }
            }}
          />

          <WarningsPanel
            warnings={detail.warnings}
            acknowledged={acknowledged}
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
}: {
  readOnly: boolean;
  status: string;
  canEdit: boolean;
  dirty: boolean;
  remaining: number;
}): string {
  if (!canEdit) {
    return "Your account can view orders but not change them.";
  }
  if (status === "approved" || status === "exported") {
    return "Already approved. Editing any value reopens it for review.";
  }
  if (status === "rejected") {
    return "This order was rejected. Editing any value reopens it for review.";
  }
  if (readOnly) {
    return `This order is ${status}, so it can't be reviewed yet.`;
  }
  if (remaining > 0) {
    return `${remaining} check${remaining === 1 ? "" : "s"} still to tick below before you can approve.`;
  }
  if (dirty) {
    return "You have unsaved changes — save them, then approve.";
  }
  return "Everything checked. Ready to approve.";
}

function BannerView({ banner }: { banner: Banner }) {
  return (
    <div
      role="alert"
      data-testid={`banner-${banner.kind}`}
      className={[
        "rounded border p-3 text-sm",
        banner.kind === "error" ? "border-red-300 bg-red-50" : "border-green-300 bg-green-50",
      ].join(" ")}
    >
      <p className="font-medium">{banner.title}</p>
      <p className="mt-1">{banner.message}</p>
      {banner.action ? <p className="mt-1 text-gray-700">{banner.action}</p> : null}
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

/**
 * The text recorded alongside an acknowledgement (Section 7.3 requires the
 * warning text, not just its id). Built from the code and the occurrence's
 * own numbers, which is what the reviewer was looking at when they ticked it.
 */
function describeWarning(code: string, detail: Record<string, string>): string {
  const specifics = Object.entries(detail ?? {})
    .filter(([, v]) => v !== null && v !== "")
    .map(([k, v]) => `${k}: ${v}`)
    .join(", ");
  return specifics ? `${code} (${specifics})` : code;
}
