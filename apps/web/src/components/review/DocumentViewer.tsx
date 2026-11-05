"use client";

import { useEffect, useState } from "react";
import { originalDocumentUrl, type CatalogError, type OriginalDocument } from "@/lib/review";
import { DocumentText } from "./DocumentText";
import { PageViewer } from "./PageViewer";
import { API_BASE_URL, CatalogMessage, DownloadOriginal, Panel, catalogEntryOf } from "./viewerParts";

/**
 * The original document, beside the extracted data (CLAUDE.md Section 7.12).
 *
 * **What the panel shows is the API's answer, `view` (D-233):**
 *
 *   * `pages` -- the page images the parse job drew (A2, A3), in the page,
 *     with DocFlow's own zoom and pan. They are DocFlow's output, not the
 *     sender's bytes (7.12's clarification; D-228 item 11).
 *   * `text` -- the text DocFlow stored from the document, in the page as
 *     escaped text, never HTML.
 *   * `frame` -- the sandboxed iframe, used only while a drawn format has no
 *     pages yet: a sender's image, or a TIFF's or HEIC's stored preview (D-228
 *     item 1's answer). Its removal is proposed at A6, after the backfill.
 *   * `panel` -- a message in the catalog's words, and the download.
 *
 * Two rules from Section 7.12 hold for the frame, both structural rather
 * than advisory:
 *
 *   * The original renders inside a **sandboxed iframe**. `sandbox` with no
 *     `allow-scripts` means a file that somehow carried active content
 *     cannot run it, cannot navigate the page it is embedded in, and has no
 *     origin of its own to reach back with.
 *   * The URL is **signed and short-lived**, minted per view. The storage
 *     path never reaches this component -- it asks the API for a token and
 *     is handed an opaque path.
 *
 * The file itself is always **saved, never opened**. A link's `download`
 * attribute does nothing across origins, so the API sends it as an attachment,
 * and a PDF never reaches a tab outside the sandbox (D-228).
 *
 * Nothing from the document is ever rendered as HTML by this app.
 */

export function DocumentViewer({
  documentId,
  filename,
}: {
  documentId: string;
  filename: string;
}) {
  const [minted, setMinted] = useState<OriginalDocument | null>(null);
  const [failure, setFailure] = useState<CatalogError | null>(null);

  // No synchronous reset here: the caller passes `key={documentId}`, so a
  // different document remounts this component with fresh state rather than
  // briefly showing the previous document's URL.
  useEffect(() => {
    let cancelled = false;

    originalDocumentUrl(documentId)
      .then((result) => {
        if (!cancelled) setMinted(result);
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(catalogEntryOf(error));
      });

    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (failure) {
    return (
      <Panel testId="viewer-unavailable">
        <CatalogMessage entry={failure} />
      </Panel>
    );
  }

  if (minted === null) {
    return (
      <Panel>
        <p className="text-sm text-gray-500">Loading the original document…</p>
      </Panel>
    );
  }

  const name = minted.filename ?? filename;
  const format = minted.format ?? null;

  if (minted.view === "pages" && minted.pages && minted.page_unavailable) {
    return (
      <PageViewer
        documentId={documentId}
        filename={name}
        initialPages={minted.pages}
        pageUnavailable={minted.page_unavailable}
      />
    );
  }

  if (minted.view === "text") {
    return <DocumentText documentId={documentId} format={format} filename={name} />;
  }

  // The stored copy can't be read at all (Stage 3b, DOC-027). Said in the
  // catalog's words, and no link: it could only lead to a 404.
  if (minted.unavailable) {
    return (
      <Panel testId="viewer-original-unavailable">
        <CatalogMessage entry={minted.unavailable} />
      </Panel>
    );
  }

  if (minted.view !== "frame") {
    const entry = minted.not_shown ?? minted.no_text ?? null;
    return (
      <Panel testId="viewer-not-previewable">
        {entry ? (
          <CatalogMessage entry={entry} />
        ) : (
          <>
            <p className="text-sm font-medium text-gray-900">
              This order came in as {format ?? "a format"} DocFlow can read but a browser
              can&apos;t display
            </p>
            <p className="mt-1 text-sm text-gray-600">
              Everything DocFlow read from it is shown beside this. Download the original if you
              need to check a value against it.
            </p>
          </>
        )}
        <DownloadOriginal documentId={documentId} filename={name} />
      </Panel>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      {/*
        A TIFF or HEIC shown from its stored preview says so: it is the page
        as it was sent, re-encoded (D-092).
      */}
      {minted.preview_kind === "converted_image" ? (
        <p
          data-testid="preview-kind"
          className="rounded-md bg-gray-100 px-3 py-1.5 text-xs text-gray-600"
        >
          {`Converted for viewing from ${format ?? "the original"} — this is the page as it was sent.`}
        </p>
      ) : null}
      <iframe
        data-testid="document-viewer"
        title={`Original document: ${name}`}
        src={`${API_BASE_URL}${minted.url}`}
        // No allow-scripts, no allow-same-origin, no allow-popups, no
        // allow-forms. The document is a picture of a page, not a program.
        sandbox=""
        referrerPolicy="no-referrer"
        className="min-h-0 w-full flex-1 rounded-xl border border-gray-200 bg-white"
      />
    </div>
  );
}
