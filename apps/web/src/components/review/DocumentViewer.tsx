"use client";

import { useEffect, useState } from "react";
import {
  ReviewApiError,
  UNEXPECTED,
  originalDocumentUrl,
  triggerDownload,
  type CatalogError,
} from "@/lib/review";

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
 * the file itself when it is not (DECISIONS.md D-091). A PDF is one of these
 * too: browsers refuse to show one in a sandboxed frame (D-228).
 *
 * The file itself is always **saved, never opened**. A link's `download`
 * attribute does nothing across origins, so the API sends it as an attachment,
 * and a PDF never reaches a tab outside the sandbox (D-228).
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
  unavailable: CatalogError | null;
  notShown: CatalogError | null;
};

function catalogEntryOf(error: unknown): CatalogError {
  return error instanceof ReviewApiError ? error.catalog : UNEXPECTED;
}

export function DocumentViewer({
  documentId,
  filename,
}: {
  documentId: string;
  filename: string;
}) {
  const [minted, setMinted] = useState<Minted | null>(null);
  const [failure, setFailure] = useState<CatalogError | null>(null);

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
            unavailable: result.unavailable ?? null,
            notShown: result.not_shown ?? null,
          });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailure(catalogEntryOf(error));
      });

    return () => {
      cancelled = true;
    };
  }, [documentId, filename]);

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

  // The stored copy can't be read at all (Stage 3b, DOC-027). Said in the
  // catalog's words, and no link: it could only lead to a 404.
  if (minted.unavailable) {
    return (
      <Panel testId="viewer-original-unavailable">
        <CatalogMessage entry={minted.unavailable} />
      </Panel>
    );
  }

  if (!minted.previewable) {
    return (
      <Panel testId="viewer-not-previewable">
        {minted.notShown ? (
          <CatalogMessage entry={minted.notShown} />
        ) : (
          <>
            <p className="text-sm font-medium text-gray-900">
              This order came in as {minted.format ?? "a format"} DocFlow can read but a browser
              can&apos;t display
            </p>
            <p className="mt-1 text-sm text-gray-600">
              Everything DocFlow read from it is shown beside this. Download the original if you
              need to check a value against it.
            </p>
          </>
        )}
        <DownloadOriginal documentId={documentId} filename={minted.filename} />
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

/**
 * Mints its link on the click, not with the panel: a link lasts a few minutes
 * and the panel can sit open for longer, and an expired one is a bare 404.
 */
function DownloadOriginal({ documentId, filename }: { documentId: string; filename: string }) {
  const [working, setWorking] = useState(false);
  const [failure, setFailure] = useState<CatalogError | null>(null);

  async function download() {
    setWorking(true);
    setFailure(null);
    try {
      const fresh = await originalDocumentUrl(documentId);
      triggerDownload(`${API_BASE_URL}${fresh.download_url}`);
    } catch (error) {
      setFailure(catalogEntryOf(error));
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={download}
        disabled={working}
        data-testid="download-original"
        className="mt-3 inline-block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-60"
      >
        Download {filename}
      </button>
      {failure ? (
        <div data-testid="download-failed" className="mt-3">
          <CatalogMessage entry={failure} />
        </div>
      ) : null}
    </>
  );
}

function CatalogMessage({ entry }: { entry: CatalogError }) {
  return (
    <>
      <p className="text-sm font-medium text-gray-900">{entry.title}</p>
      <p className="mt-1 text-sm text-gray-600">{entry.message}</p>
      <p className="mt-1 text-sm text-gray-600">{entry.action}</p>
      <p className="mt-2 text-xs text-gray-400">{entry.code}</p>
    </>
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
