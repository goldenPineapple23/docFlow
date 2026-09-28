import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "./networkGuard";

/**
 * The guard in networkGuard.ts only covers a spec that imports `test` from
 * it. A spec that imports `test` straight from @playwright/test would run
 * unguarded, so this fails the suite the moment one does.
 */
test("every e2e spec runs under the network guard", () => {
  const dir = __dirname;
  const unguarded = readdirSync(dir)
    .filter((name) => name.endsWith(".spec.ts"))
    .filter((name) => {
      const source = readFileSync(path.join(dir, name), "utf-8");
      // The values (not types) this spec imports straight from @playwright/test.
      const direct = [...source.matchAll(/import\s*\{([^}]*)\}\s*from\s*"@playwright\/test"/g)]
        .flatMap((match) => match[1].split(","))
        .map((name) => name.trim())
        .filter((name) => name && !name.startsWith("type "));
      return direct.includes("test") || direct.includes("expect") || !source.includes('from "./networkGuard"');
    });
  expect(unguarded).toEqual([]);
});
