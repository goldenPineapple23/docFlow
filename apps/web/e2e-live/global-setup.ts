import { runSeedScript } from "./seed";

/**
 * The throwaway tenant, reviewer and documents this suite drives. Created
 * here rather than by hand so a run can never inherit another run's rows
 * (DECISIONS.md D-160).
 */
export default function globalSetup() {
  runSeedScript("setup");
}
