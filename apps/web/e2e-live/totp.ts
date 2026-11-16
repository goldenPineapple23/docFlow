import { createHmac } from "node:crypto";

/**
 * Authenticator codes as Supabase Auth checks them (RFC 6238: SHA-1, six
 * digits, 30-second steps), from Node's own crypto, so no TOTP package joins
 * the lockfile (D-241). scripts/seed_live_e2e.py's `totp_code` is the same in
 * Python; Supabase Auth accepting both is what proves them right.
 */

export const PERIOD_SECONDS = 30;

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Decode(secret: string): Buffer {
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const char of secret.toUpperCase().replace(/=+$/, "")) {
    const index = BASE32.indexOf(char);
    if (index < 0) throw new Error("the authenticator secret isn't base32");
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

export function currentStep(now: number = Date.now()): number {
  return Math.floor(now / 1000 / PERIOD_SECONDS);
}

export function totpCode(secret: string, step: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const digest = createHmac("sha1", base32Decode(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const number = digest.readUInt32BE(offset) & 0x7fffffff;
  return String(number % 1_000_000).padStart(6, "0");
}

/**
 * Wait until the step is later than `used`, so a code is never entered twice
 * whatever Supabase does with a repeat. Deterministic: no retry on refusal.
 */
export async function aStepAfter(used: number): Promise<number> {
  while (currentStep() <= used) {
    const next = (currentStep() + 1) * PERIOD_SECONDS * 1000;
    await new Promise((resolve) => setTimeout(resolve, Math.max(50, next - Date.now() + 50)));
  }
  return currentStep();
}
