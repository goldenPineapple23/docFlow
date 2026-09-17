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
 * Nothing from the document is ever rendered as HTML by this app. The only
 * thing that touches document bytes is the iframe, and the iframe cannot
 * execute them.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function DocumentViewer({
  documentId,
  filename,
}: {
  documentId: string;
  filename: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  // No synchronous reset here: the caller passes `key={documentId}`, so a
  // different document remounts this component with fresh state rather than
  // briefly showing the previous document's URL.
  useEffect(() => {
    let cancelled = false;

    originalDocumentUrl(documentId)
      .then((minted) => {
        if (!cancelled) setUrl(`${API_BASE_URL}${minted.url}`);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (failed) {
    return (
      <div
        data-testid="viewer-unavailable"
        className="flex h-full items-center justify-center rounded border border-gray-200 bg-gray-50 p-6 text-center"
      >
        <div>
          <p className="text-sm font-medium">We couldn&apos;t load the original document</p>
          <p className="mt-1 text-sm text-gray-600">
            The extracted values are still shown beside this. Reload the page to try again.
          </p>
        </div>
      </div>
    );
  }

  if (url === null) {
    return (
      <div className="flex h-full items-center justify-center rounded border border-gray-200 bg-gray-50">
        <p className="text-sm text-gray-500">Loading the original document…</p>
      </div>
    );
  }

  return (
    <iframe
      data-testid="document-viewer"
      title={`Original document: ${filename}`}
      src={url}
      // No allow-scripts, no allow-same-origin, no allow-popups, no
      // allow-forms. The document is a picture of a page, not a program.
      sandbox=""
      referrerPolicy="no-referrer"
      className="h-full w-full rounded border border-gray-200 bg-white"
    />
  );
}
