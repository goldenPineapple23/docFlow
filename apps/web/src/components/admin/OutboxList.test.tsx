import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OutboxList } from "./OutboxList";
import type { OutboxEmail } from "@/lib/admin";

const email = (over: Partial<OutboxEmail>): OutboxEmail => ({
  id: "e1",
  tenant_id: "t1",
  tenant_name: "Acme Test Distributor",
  to_address: "owner@example.test",
  reply_to: null,
  template: "go_live",
  subject: "DocFlow is live for Acme Test Distributor",
  body_text: "Hello",
  status: "held",
  error: null,
  created_at: "2026-09-29T12:00:00Z",
  sent_at: null,
  ...over,
});

describe("the Console Outbox (0032)", () => {
  it("shows the Reply-To an email carries, so it can be sent by hand with it", () => {
    render(<OutboxList emails={[email({ reply_to: "support@example.test" })]} />);
    expect(screen.getByTestId("outbox-reply-to").textContent).toBe("Reply-To: support@example.test");
  });

  it("shows no Reply-To line for an email without one", () => {
    render(<OutboxList emails={[email({ template: "intake_suspended" })]} />);
    expect(screen.queryByTestId("outbox-reply-to")).toBeNull();
  });
});
