"use client";

import { useState, type InputHTMLAttributes } from "react";

/**
 * A password field with a Show / Hide switch (DECISIONS.md D-107).
 *
 * Asked for by the founder after mistyping the confirmation on the
 * set-password page: with the text hidden there is no way to see which of
 * two fields is wrong. Hidden by default; the switch is a real button with a
 * label a screen reader announces, and it never submits the form.
 */
export function PasswordInput(props: Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  const [visible, setVisible] = useState(false);
  const { className, ...rest } = props;

  return (
    <div className="relative mt-1">
      <input
        {...rest}
        type={visible ? "text" : "password"}
        className={`w-full rounded border py-2 pl-3 pr-16 ${className ?? ""}`}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-pressed={visible}
        aria-label={visible ? "Hide password" : "Show password"}
        className="absolute inset-y-0 right-0 px-3 text-xs font-medium text-slate-600 hover:text-slate-900"
      >
        {visible ? "Hide" : "Show"}
      </button>
    </div>
  );
}
