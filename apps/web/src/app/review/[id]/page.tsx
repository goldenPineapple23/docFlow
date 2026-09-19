"use client";

import { use } from "react";
import { ReviewDocumentScreen } from "@/components/review/ReviewDocumentScreen";

/** A tenant user's review of one order. The screen itself is shared with the
 * Console's acting-as route (D-111). */
export default function ReviewDocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ReviewDocumentScreen id={id} />;
}
