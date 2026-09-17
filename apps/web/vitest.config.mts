import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Component tests for the review surface (CLAUDE.md Section 7.12).
 *
 * The end-to-end flow — a reviewer correcting and approving a document —
 * lives in Playwright (`e2e/`), against a real browser. These are the
 * faster-feedback tests for the pieces: confidence flagging, the edit diff,
 * the approval gate, and the rule that nothing from a document is ever
 * rendered as HTML.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // Playwright owns e2e/; running its specs under vitest would start a
    // browser runner inside a jsdom suite.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
