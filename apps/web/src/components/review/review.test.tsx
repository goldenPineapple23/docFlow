import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfidenceBadge, isLowConfidence } from "./confidence";
import { HeaderFields } from "./HeaderFields";
import { LineTable } from "./LineTable";
import { TrailPanel } from "./TrailPanel";
import { WarningsPanel } from "./WarningsPanel";
import type { DocumentHeader, DocumentLine, DocumentWarning, TrailEntry } from "@/lib/review";
import { AppHeader } from "@/components/AppHeader";

// The app router only exists inside a Next app; these tests render
// components on their own.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

/**
 * The review surface's own guarantees (CLAUDE.md Sections 7.1, 7.3, 7.6, 7.12).
 *
 * These are not coverage-for-its-own-sake: each one corresponds to a rule
 * that, if broken, would let a reviewer approve a wrong number without
 * noticing.
 */

const header: DocumentHeader = {
  po_number: "BCH-2291",
  order_date: "2025-05-28",
  requested_delivery_date: null,
  buyer_name: "Bella's Test Coffee House",
  buyer_contact_email: null,
  ship_to_address: null,
  payment_terms: null,
  order_total: "570.00",
  currency: "USD",
  notes: null,
  currency_inferred: false,
  confidence: { po_number: 0.98, order_total: 0.42 },
  provenance: {},
};

const line: DocumentLine = {
  id: "11111111-1111-1111-1111-111111111111",
  line_number: 1,
  sku: "CF-1001",
  description: "Colombian Whole Bean 5lb",
  unit: "CS",
  quantity: "12.0000",
  unit_price: "47.5000",
  line_total: "570.00",
  confidence: "0.97",
  matched_item_id: null,
  match_method: null,
  match_score: null,
  matched_uom: null,
  uom_mismatch: false,
  match_candidates: [],
  provenance: {},
};

describe("confidence flagging (Section 7.1)", () => {
  it("treats anything below 0.80 as low", () => {
    expect(isLowConfidence(0.79)).toBe(true);
    expect(isLowConfidence("0.42")).toBe(true);
    expect(isLowConfidence(0.8)).toBe(false);
    expect(isLowConfidence(null)).toBe(false);
  });

  it("flags a low value with words, not only a colour", () => {
    // A reviewer who cannot distinguish the colour, or who prints the page,
    // still has to be able to see that the value is doubtful.
    render(<ConfidenceBadge value="0.42" />);
    expect(screen.getByTestId("confidence-badge")).toHaveTextContent(/low confidence/i);
  });

  it("does not shout about a confident value", () => {
    render(<ConfidenceBadge value="0.98" />);
    expect(screen.getByTestId("confidence-badge")).toHaveTextContent("98%");
    expect(screen.getByTestId("confidence-badge")).not.toHaveTextContent(/low/i);
  });
});

describe("header fields (Sections 7.1, 7.12)", () => {
  it("renders money as the exact string it was given", () => {
    render(<HeaderFields header={header} edits={{}} disabled={false} onChange={() => {}} />);
    // "570.00", never 570 -- the scale is part of the value.
    expect(screen.getByTestId("header-input-order_total")).toHaveValue("570.00");
  });

  it("never uses a number input for any field", () => {
    // type="number" hands the value to the browser's float parser, which is
    // exactly what Section 7.1 forbids for money -- and a date input would
    // silently reformat what the document actually printed.
    const { container } = render(
      <HeaderFields header={header} edits={{}} disabled={false} onChange={() => {}} />,
    );
    const inputs = Array.from(container.querySelectorAll("input"));
    expect(inputs.length).toBeGreaterThan(0);
    for (const input of inputs) {
      expect(input.getAttribute("type")).toBe("text");
    }
  });

  it("marks a low-confidence field visibly", () => {
    render(<HeaderFields header={header} edits={{}} disabled={false} onChange={() => {}} />);
    const badges = screen.getAllByTestId("confidence-badge");
    expect(badges.some((b) => b.getAttribute("data-low") === "true")).toBe(true);
  });

  it("reports each keystroke to the caller so the diff is the caller's", () => {
    const onChange = vi.fn();
    render(<HeaderFields header={header} edits={{}} disabled={false} onChange={onChange} />);
    const input = screen.getByTestId("header-input-po_number");
    input.focus();
    return userEvent.type(input, "X").then(() => {
      expect(onChange).toHaveBeenCalledWith("po_number", expect.stringContaining("BCH-2291"));
    });
  });

  it("renders document text as text, never as markup", () => {
    // Section 7.12 / Section 10: nothing from a document is ever rendered as
    // HTML. A buyer name carrying a tag must appear as characters.
    const hostile = { ...header, buyer_name: "<img src=x onerror=alert(1)>" };
    const { container } = render(
      <HeaderFields header={hostile} edits={{}} disabled={false} onChange={() => {}} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByTestId("header-input-buyer_name")).toHaveValue(
      "<img src=x onerror=alert(1)>",
    );
  });

  it("warns when the currency was inferred rather than printed", () => {
    render(
      <HeaderFields
        header={{ ...header, currency_inferred: true }}
        edits={{}}
        disabled={false}
        onChange={() => {}}
      />,
    );
    expect(screen.getByTestId("currency-inferred")).toBeInTheDocument();
  });

  it("disables every field when the document is not editable", () => {
    render(<HeaderFields header={header} edits={{}} disabled onChange={() => {}} />);
    expect(screen.getByTestId("header-input-po_number")).toBeDisabled();
  });
});

describe("line items (Section 7.6)", () => {
  it("shows candidates as suggestions, never as applied matches", () => {
    const withCandidates: DocumentLine = {
      ...line,
      match_candidates: [
        { item_id: "22222222-2222-2222-2222-222222222222", sku: "CF-1002", description: "Colombian WB 5lb", score: "0.75" },
      ],
    };
    render(
      <LineTable
        lines={[withCandidates]}
        edits={{}}
        disabled={false}
        onChange={() => {}}
        onConfirmMapping={async () => {}}
      />,
    );
    const candidates = screen.getByTestId("candidates-1");
    expect(candidates).toHaveTextContent(/none applied/i);
    expect(within(candidates).getByText("CF-1002")).toBeInTheDocument();
  });

  it("surfaces a unit-of-measure disagreement instead of normalizing it", () => {
    render(
      <LineTable
        lines={[{ ...line, uom_mismatch: true, matched_uom: "EA" }]}
        edits={{}}
        disabled={false}
        onChange={() => {}}
        onConfirmMapping={async () => {}}
      />,
    );
    expect(screen.getByTestId("uom-mismatch-1")).toHaveTextContent(/ships the wrong quantity/i);
  });

  it("only creates a mapping from an explicit click", async () => {
    const onConfirmMapping = vi.fn().mockResolvedValue(undefined);
    render(
      <LineTable
        lines={[
          {
            ...line,
            match_candidates: [
              { item_id: "22222222-2222-2222-2222-222222222222", sku: "CF-1002", score: "0.75" },
            ],
          },
        ]}
        edits={{}}
        disabled={false}
        onChange={() => {}}
        onConfirmMapping={onConfirmMapping}
      />,
    );

    expect(onConfirmMapping).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /this one/i }));
    expect(onConfirmMapping).toHaveBeenCalledWith(line.id, "22222222-2222-2222-2222-222222222222");
  });

  it("keeps quantity as a string at its stored scale", () => {
    render(
      <LineTable
        lines={[line]}
        edits={{}}
        disabled={false}
        onChange={() => {}}
        onConfirmMapping={async () => {}}
      />,
    );
    expect(screen.getByTestId("line-1-quantity")).toHaveValue("12.0000");
  });
});

describe("the approval gate (Section 7.3)", () => {
  const warning: DocumentWarning = {
    id: "33333333-3333-3333-3333-333333333333",
    code: "VAL-002",
    severity: "high",
    field_name: "order_total",
    line_number: null,
    document_line_id: null,
    detail: { order_total: "100.00", sum_of_lines: "570.00", difference: "-470.00" },
    status: "open",
    acknowledged_at: null,
  };

  it("requires each warning to be ticked individually", async () => {
    const onToggle = vi.fn();
    render(
      <WarningsPanel
        warnings={[warning]}
        acknowledged={new Set()}
        disabled={false}
        onToggle={onToggle}
      />,
    );

    // No "acknowledge all" -- the gate exists so a person looked at each one.
    expect(screen.queryByRole("button", { name: /acknowledge all/i })).toBeNull();
    await userEvent.click(screen.getByTestId(`ack-${warning.id}`));
    expect(onToggle).toHaveBeenCalledWith(warning.id, true);
  });

  it("shows the two numbers that disagreed, as strings", () => {
    render(
      <WarningsPanel
        warnings={[warning]}
        acknowledged={new Set()}
        disabled={false}
        onToggle={() => {}}
      />,
    );
    const row = screen.getByTestId("warning-VAL-002");
    expect(row).toHaveTextContent("order_total: 100.00");
    expect(row).toHaveTextContent("sum_of_lines: 570.00");
  });

  it("says so plainly when nothing needs attention", () => {
    render(
      <WarningsPanel warnings={[]} acknowledged={new Set()} disabled={false} onToggle={() => {}} />,
    );
    expect(screen.getByTestId("no-warnings")).toBeInTheDocument();
  });
});

describe("the audit trail (Phase 3 exit criterion)", () => {
  const trail: TrailEntry[] = [
    {
      id: "44444444-4444-4444-4444-444444444444",
      action: "edited",
      user_id: "55555555-5555-5555-5555-555555555555",
      by_docflow_support: false,
      changes: [
        { field: "order_total", before: "570.00", after: "571.25" },
        { field: "quantity", before: "12.0000", after: "13", line_number: 1 },
      ],
      warning_acknowledgements: [],
      note: null,
      created_at: "2026-09-17T10:00:00Z",
    },
  ];

  it("shows every field before and after, not a count", () => {
    render(<TrailPanel trail={trail} />);
    const list = screen.getByTestId("trail");
    expect(list).toHaveTextContent("order_total");
    expect(list).toHaveTextContent("570.00");
    expect(list).toHaveTextContent("571.25");
    expect(list).toHaveTextContent("line 1");
    expect(list).not.toHaveTextContent(/2 fields changed/i);
  });

  it("labels an action the founder took inside the tenant", () => {
    render(<TrailPanel trail={[{ ...trail[0], by_docflow_support: true }]} />);
    expect(screen.getByTestId("docflow-support")).toHaveTextContent(/docflow support/i);
  });
});

describe("getting around the app (D-090)", () => {
  it("always offers a way to the queue and a way out", () => {
    // A reviewer signed in, landed on a page with their own email on it and
    // no link to their work, and no way to sign out. Every screen was
    // reachable only by typing a URL.
    render(<AppHeader email="reviewer@example.test" />);

    const links = screen.getAllByRole("link");
    expect(links.some((a) => a.getAttribute("href") === "/review")).toBe(true);
    expect(screen.getByTestId("sign-out")).toBeInTheDocument();
  });
});
