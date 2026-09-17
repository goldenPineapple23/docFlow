"use client";

import { useEffect, useState } from "react";
import { originalDocumentUrl } from "@/lib/review";

/**
 * The original document, beside the extracted data (CLAUDE.md Section 7.12).
 *
 * Two rules from that section, both structural rather than advisory:
 *
 *   * The document renders inside a **sandboxed iframe**. `sandbox` with no
 *     `allow-scripts` means a file that somehow carried active content
 *     cannot run it, cannot navigate the page it is embedded in, and has no
 *     origin of its own to reach back with.
 *   * The URL is **signed and short-lived**, minted per view. The storage
 *     path never reaches this component -- it asks the API for a token and
 *     is handed an opaque path.
 *
 * **Not every format can be shown.** Buyers send Word files, Excel files,
 * raw email and TIFF faxes, and no browser renders any of those. Serving
 * them into the frame produces a blank panel that reads as a broken screen,
 * so the API says up front whether the file is previewable and this offers
 * the file itself when it is not (DECISIONS.md D-091).
 *
 * Nothing from the document is ever rendered as HTML by this app. The only
 * thing that touches document bytes is the iframe, and the iframe cannot
 * execute them.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Minted = {
  url: string;
  previewable: boolean;
  format: string | null;
  filename: string;
  previewKind: "converted_image" | "extracted_text" | null;
};

export function DocumentViewer({
  documentId,
  filename,
}: {
  documentId: string;
  filename: string;
}) {
  const [minted, setMinted] = useState<Minted | null>(null);
  const [failed, setFailed] = useState(false);

  // No synchronous reset here: the caller passes `key={documentId}`, so a
  // different document remounts this component with fresh state rather than
  // briefly showing the previous document's URL.
  useEffect(() => {
    let cancelled = false;

    originalDocumentUrl(documentId)
      .then((result) => {
        if (!cancelled) {
          setMinted({
            url: `${API_BASE_URL}${result.url}`,
            previewable: result.previewable ?? true,
            format: result.format ?? null,
            filename: result.filename ?? filename,
            previewKind: result.preview_kind ?? null,
          });
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
    };
  }, [documentId, filename]);

  if (failed) {
    return (
      <Panel testId="viewer-unavailable">
        <p className="text-sm font-medium text-gray-900">
          We couldn&apos;t load the original document
        </p>
        <p className="mt-1 text-sm text-gray-600">
          The extracted values are still shown beside this. Reload the page to try again.
        </p>
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

  if (!minted.previewable) {
    return (
      <Panel testId="viewer-not-previewable">
        <p className="text-sm font-medium text-gray-900">
          This order came in as {minted.format ?? "a format"} DocFlow can read but a browser
          can&apos;t display
        </p>
        <p className="mt-1 text-sm text-gray-600">
          Everything DocFlow read from it is shown beside this. Open the original if you need to
          check a value against it.
        </p>
        <a
          href={minted.url}
          target="_blank"
          rel="noreferrer noopener"
          download={minted.filename}
          data-testid="download-original"
          className="mt-3 inline-block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 hover:bg-gray-50"
        >
          Open {minted.filename}
        </a>
      </Panel>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      {/*
        Say which of three things the reviewer is looking at. "Text DocFlow
        read from this file" matters most: it is not the original layout, and
        someone checking a number against it should know they are reading
        DocFlow's rendering rather than the document itself (D-092).
      */}
      {minted.previewKind ? (
        <p
          data-testid="preview-kind"
          className="rounded-md bg-gray-100 px-3 py-1.5 text-xs text-gray-600"
        >
          {minted.previewKind === "converted_image"
            ? `Converted for viewing from ${minted.format ?? "the original"} — this is the page as it was sent.`
            : `Text DocFlow read from ${minted.format ?? "this file"} — the wording is the document's, the layout isn't.`}
        </p>
      ) : null}
      <iframe
        data-testid="document-viewer"
        title={`Original document: ${minted.filename}`}
        src={minted.url}
        // No allow-scripts, no allow-same-origin, no allow-popups, no
        // allow-forms. The document is a picture of a page, not a program.
        sandbox=""
        referrerPolicy="no-referrer"
        className="min-h-0 w-full flex-1 rounded-xl border border-gray-200 bg-white"
      />
    </div>
  );
}

function Panel({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className="flex h-full items-center justify-center rounded-xl border border-gray-200 bg-gray-50/60 p-6 text-center"
    >
      <div className="max-w-sm">{children}</div>
    </div>
  );
}
