import { execFileSync } from "node:child_process";
import path from "node:path";
import { PYTHON, SEED_FILE } from "../playwright.live.config";

/**
 * The throwaway tenant, reviewer and documents this suite drives. Created
 * here rather than by hand so a run can never inherit another run's rows
 * (DECISIONS.md D-160).
 */
export default function globalSetup() {
  const script = path.resolve(__dirname, "../../../scripts/seed_live_e2e.py");
  execFileSync(PYTHON, [script, "setup", "--out", SEED_FILE], { stdio: "inherit" });
}
