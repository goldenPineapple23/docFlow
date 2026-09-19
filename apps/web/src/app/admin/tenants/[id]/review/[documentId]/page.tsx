"use client";

import { use } from "react";
import { ReviewDocumentScreen } from "@/components/review/ReviewDocumentScreen";

/** One order, in the normal review screen, as DocFlow support (D-111). */
export default function ConsoleReviewDocumentPage({
  params,
}: {
  params: Promise<{ id: string; documentId: string }>;
}) {
  const { documentId } = use(params);
  return <ReviewDocumentScreen id={documentId} />;
}
