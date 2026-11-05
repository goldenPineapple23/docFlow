"use client";

import { useEffect, useState } from "react";
import { documentText, type CatalogError, type DocumentText as StoredText } from "@/lib/review";
import { CatalogMessage, DownloadOriginal, Panel, catalogEntryOf } from "./viewerParts";

/**
 * The text DocFlow stored from a document, shown in the page (D-228 items 1
 * and 11; D-233): a Word file's, a spreadsheet's or an e-mail's text preview,
 * or a TXT, CSV or MD file's text as the worker read it -- never the sender's
 * bytes.
 *
 * **Escaped, never HTML** (CLAUDE.md 7.12): the text is a React text child,
 * so markup in a document is shown as the characters it is. Its colours and
 * wrapping are set here, not left to the browser, which showed the frame's
 * text white-on-black on a Mac in dark mode (A0).
 */
export function DocumentText({
  documentId,
  format,
  filename,
}: {
  documentId: string;
  format: string | null;
  filename: string;
}) {
  const [stored, setStored] = useState<StoredText | null>(null);
  const [failure, setFailure] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    documentText(documentId)
      .then((result) => {
        if (!cancelled) setStored(result);
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
      <Panel testId="viewer-text-unavailable">
        <CatalogMessage entry={failure} />
        <DownloadOriginal documentId={documentId} filename={filename} />
      </Panel>
    );
  }

  if (stored === null) {
    return (
      <Panel>
        <p className="text-sm text-gray-500">Loading the text DocFlow read…</p>
      </Panel>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      {/* Said, not hidden: this is DocFlow's reading, not the layout (D-092). */}
      <p data-testid="preview-kind" className="rounded-md bg-gray-100 px-3 py-1.5 text-xs text-gray-600">
        {`Text DocFlow read from ${format ?? "this file"} — the wording is the document's, the layout isn't.`}
      </p>
      {stored.truncated ? (
        <div data-testid="text-cut" className="rounded-md bg-amber-50 px-3 py-1.5 text-xs text-amber-900">
          <p>
            {`Cut at ${stored.cut_at.toLocaleString("en-US")} characters for viewing; download the original for the rest.`}
          </p>
          <DownloadOriginal documentId={documentId} filename={filename} />
        </div>
      ) : null}
      <pre
        data-testid="document-text"
        className="min-h-0 w-full flex-1 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-gray-200 bg-white p-4 font-mono text-xs leading-relaxed text-gray-900"
      >
        {stored.text}
      </pre>
    </div>
  );
}
