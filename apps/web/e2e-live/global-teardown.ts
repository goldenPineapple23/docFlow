import { execFileSync } from "node:child_process";
import path from "node:path";
import { PYTHON, SEED_FILE } from "../playwright.live.config";

/** Removes every row the run created, pass or fail. */
export default function globalTeardown() {
  const script = path.resolve(__dirname, "../../../scripts/seed_live_e2e.py");
  execFileSync(PYTHON, [script, "teardown", "--out", SEED_FILE], { stdio: "inherit" });
}
