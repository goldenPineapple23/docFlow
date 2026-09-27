import { execFileSync } from "node:child_process";
import path from "node:path";
import { PYTHON, SEED_FILE } from "../playwright.live.config";

/**
 * Running `scripts/seed_live_e2e.py`, with whatever it says kept.
 *
 * `stdio: "inherit"` would be the obvious way to run this, and it is the
 * wrong one: the script's own explanation goes to the CI log, while the
 * failure Playwright reports is only "Command failed". Whoever is
 * debugging may not be able to open that log at all -- downloading it
 * needs admin rights on the repository -- so the error carries the
 * script's words instead of pointing at them.
 */
export function runSeedScript(command: "setup" | "teardown"): void {
  const script = path.resolve(__dirname, "../../../scripts/seed_live_e2e.py");
  try {
    const said = execFileSync(PYTHON, [script, command, "--out", SEED_FILE], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    if (said.trim()) console.log(said.trim());
  } catch (failure) {
    const asRun = failure as { stdout?: string; stderr?: string; status?: number };
    throw new Error(
      [
        `seed_live_e2e.py ${command} failed (exit ${asRun.status ?? "?"}).`,
        (asRun.stdout ?? "").trim(),
        (asRun.stderr ?? "").trim(),
      ]
        .filter(Boolean)
        .join("\n"),
    );
  }
}
