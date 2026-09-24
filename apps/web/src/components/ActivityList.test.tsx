import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityList } from "./ActivityList";
import type { ActivityItem } from "@/lib/home";

/** Team rows (slice 5.8d, D-132) are about a person, not an order: they name
 * the person and link nowhere, where an order row links to the order. */

const at = new Date().toISOString();

function item(overrides: Partial<ActivityItem>): ActivityItem {
  return {
    at,
    kind: "approved",
    document_id: null,
    document_name: null,
    po_number: null,
    by: "admin@example.test",
    by_docflow_support: false,
    detail: null,
    ...overrides,
  };
}

describe("ActivityList", () => {
  it("names the person invited or removed, with no order link", () => {
    render(
      <ActivityList
        items={[
          item({ kind: "invited", detail: "new.reviewer@example.test" }),
          item({ kind: "removed", detail: "old.reviewer@example.test" }),
        ]}
      />,
    );
    expect(screen.getByText("invited")).toBeTruthy();
    expect(screen.getByText("new.reviewer@example.test")).toBeTruthy();
    expect(screen.getByText("old.reviewer@example.test")).toBeTruthy();
    expect(screen.queryByText("an order")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    // The address is the subject of the row, not a parenthetical detail.
    expect(screen.queryByText("(new.reviewer@example.test)")).toBeNull();
  });

  it("labels the founder's own first invite as DocFlow support", () => {
    render(
      <ActivityList
        items={[item({ kind: "invited", by: null, by_docflow_support: true, detail: "owner@example.test" })]}
      />,
    );
    expect(screen.getByText("DocFlow support")).toBeTruthy();
  });

  it("still links an order row to its order", () => {
    render(<ActivityList items={[item({ kind: "approved", document_id: "d1", po_number: "PO-1" })]} />);
    expect(screen.getByRole("link", { name: "PO-1" }).getAttribute("href")).toBe("/review/d1");
  });
});
