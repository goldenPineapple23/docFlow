"use client";

import type { DealTerms, SetupFeePreset, Tier } from "@/lib/admin";

/**
 * The deal agreed in the sales conversation (D-117): plan, founding-customer
 * price, setup fee. One component for Create tenant and for the tenant
 * page's Deal terms, so the two can't drift apart. Every amount shown comes
 * from the tiers and setup_fee_presets tables (Section 10: no price typed
 * into code); the server checks a typed fee against its preset's range.
 */

/** "1500.00" -> "$1,500", "2350.50" -> "$2,350.50". Display only: no arithmetic on money. */
export function money(amount: string | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const [whole, cents = ""] = amount.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `$${grouped}${cents && !/^0+$/.test(cents) ? `.${cents}` : ""}`;
}

/** Same rounding as the server (onboarding.GoLivePlan.promo_months). */
export function promoMonths(days: number | null): number | null {
  return days ? Math.ceil(days / 30) : null;
}

export function presetLabel(p: SetupFeePreset): string {
  if (p.default_amount === null) return p.name;
  if (p.max_amount !== null && p.max_amount !== p.min_amount) {
    return `${p.name} — ${money(p.min_amount)}–${money(p.max_amount)}`;
  }
  return `${p.name} — ${money(p.default_amount)}`;
}

export function emptyDeal(): DealTerms {
  return {
    tier: "growth",
    setup_fee_preset: "standard",
    setup_fee_amount: null,
    setup_fee_billing: "stripe",
    setup_fee_note: null,
    founding_price: false,
  };
}

export function DealTermsFields({
  value,
  onChange,
  tiers,
  presets,
}: {
  value: DealTerms;
  onChange: (next: DealTerms) => void;
  tiers: Tier[];
  presets: SetupFeePreset[];
}) {
  const tier = tiers.find((t) => t.code === value.tier);
  const preset = presets.find((p) => p.code === value.setup_fee_preset);
  const founding = presets.find((p) => p.code === "founding");
  const ranged = preset !== undefined && (preset.max_amount === null || preset.max_amount !== preset.min_amount);
  const set = (patch: Partial<DealTerms>) => onChange({ ...value, ...patch });

  function choosePreset(code: SetupFeePreset["code"]) {
    const next = presets.find((p) => p.code === code);
    set({
      setup_fee_preset: code,
      // The preset's own amount; Custom starts empty so it has to be typed.
      setup_fee_amount: next?.default_amount ?? "",
    });
  }

  return (
    <div className="grid gap-4 text-sm sm:grid-cols-2" data-testid="deal-terms">
      <label className="block">
        <span className="font-medium">Plan</span>
        <select
          value={value.tier}
          onChange={(e) => {
            const code = e.target.value as Tier["code"];
            const next = tiers.find((t) => t.code === code);
            set({ tier: code, founding_price: value.founding_price && !!next?.promo_monthly_price });
          }}
          data-testid="tenant-tier"
          className="mt-1 w-full rounded border px-3 py-2"
        >
          {tiers.map((t) => (
            <option key={t.code} value={t.code}>
              {t.name} — {money(t.monthly_price)}/month, {t.document_allowance.toLocaleString()} documents
            </option>
          ))}
        </select>
      </label>

      <div className="flex items-end">
        {tier?.promo_monthly_price ? (
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={value.founding_price}
              data-testid="deal-founding"
              onChange={(e) => {
                const on = e.target.checked;
                if (on) {
                  set({ founding_price: true, setup_fee_preset: "founding", setup_fee_amount: founding?.default_amount ?? null });
                } else {
                  const back = value.setup_fee_preset === "founding" ? presets.find((p) => p.code === "standard") : undefined;
                  set({
                    founding_price: false,
                    ...(back ? { setup_fee_preset: back.code, setup_fee_amount: back.default_amount } : {}),
                  });
                }
              }}
              className="mt-1"
            />
            <span>
              <span className="font-medium">Founding customer</span>
              <span className="block text-gray-600">
                {money(tier.promo_monthly_price)}/month for the first {promoMonths(tier.promo_days)} months, then{" "}
                {money(tier.monthly_price)}
                {founding ? `; ${money(founding.default_amount)} setup fee` : ""}
              </span>
            </span>
          </label>
        ) : null}
      </div>

      <fieldset className="block sm:col-span-2">
        <legend className="font-medium">Setup fee</legend>
        <div className="mt-1 grid gap-1 sm:grid-cols-2">
          {presets.map((p) => (
            <label key={p.code} className="flex items-start gap-2" title={p.description}>
              <input
                type="radio"
                name="setup-fee-preset"
                checked={value.setup_fee_preset === p.code}
                data-testid={`deal-preset-${p.code}`}
                onChange={() => choosePreset(p.code)}
                className="mt-1"
              />
              <span>
                {presetLabel(p)}
                <span className="block text-xs text-gray-500">{p.description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {ranged && preset ? (
        <label className="block">
          <span className="font-medium">Amount (USD)</span>
          <input
            value={value.setup_fee_amount ?? ""}
            onChange={(e) => set({ setup_fee_amount: e.target.value })}
            inputMode="decimal"
            data-testid="deal-amount"
            placeholder={preset.default_amount ?? "e.g. 1200"}
            className="mt-1 w-full rounded border px-3 py-2"
          />
          <span className="mt-0.5 block text-xs text-gray-500">
            {preset.max_amount === null
              ? "Any amount"
              : `Between ${money(preset.min_amount)} and ${money(preset.max_amount)}`}
          </span>
        </label>
      ) : null}

      {value.setup_fee_preset !== "waived" ? (
        <fieldset className="block">
          <legend className="font-medium">How the setup fee is billed</legend>
          <label className="mt-1 flex items-center gap-2">
            <input
              type="radio"
              name="setup-fee-billing"
              checked={value.setup_fee_billing === "stripe"}
              onChange={() => set({ setup_fee_billing: "stripe" })}
            />
            On the first Stripe invoice
          </label>
          <label className="mt-1 flex items-center gap-2">
            <input
              type="radio"
              name="setup-fee-billing"
              checked={value.setup_fee_billing === "invoiced_manually"}
              data-testid="deal-manual"
              onChange={() => set({ setup_fee_billing: "invoiced_manually" })}
            />
            I&apos;ll invoice it myself
          </label>
        </fieldset>
      ) : null}

      {preset?.note_required || value.setup_fee_billing === "invoiced_manually" ? (
        <label className="block sm:col-span-2">
          <span className="font-medium">{preset?.note_required ? "Why (required)" : "Note (optional)"}</span>
          <input
            value={value.setup_fee_note ?? ""}
            onChange={(e) => set({ setup_fee_note: e.target.value })}
            maxLength={500}
            required={preset?.note_required}
            data-testid="deal-note"
            placeholder={preset?.note_required ? "e.g. waived for the pilot" : "e.g. invoice #1001"}
            className="mt-1 w-full rounded border px-3 py-2"
          />
        </label>
      ) : null}
    </div>
  );
}

/** One line: what go-live will bill. Used on the tenant page and the go-live panel. */
export function dealSummary(d: {
  tierName: string | null;
  monthlyPrice: string | null;
  promoMonthlyPrice: string | null;
  promoDays: number | null;
  founding: boolean;
  feeAmount: string | null;
  feeBilling: string | null;
  presetName: string | null;
  note: string | null;
}): string {
  const plan =
    d.founding && d.promoMonthlyPrice
      ? `${d.tierName} at ${money(d.promoMonthlyPrice)}/month for ${promoMonths(d.promoDays)} months (founding), then ${money(d.monthlyPrice)}`
      : `${d.tierName} at ${money(d.monthlyPrice)}/month`;
  let fee: string;
  if (d.feeAmount === null) fee = "no setup fee recorded";
  else if (/^0+(\.0+)?$/.test(d.feeAmount)) fee = `setup fee waived${d.note ? ` (${d.note})` : ""}`;
  else
    fee = `${money(d.feeAmount)} ${d.presetName ? `${d.presetName.toLowerCase()} ` : ""}setup fee ${
      d.feeBilling === "invoiced_manually" ? "invoiced by you" : "on the first invoice"
    }`;
  return `${plan} · ${fee}`;
}
