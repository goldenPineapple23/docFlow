import { runSeedScript } from "./seed";

/** Removes every row the run created, pass or fail. */
export default function globalTeardown() {
  runSeedScript("teardown");
}
