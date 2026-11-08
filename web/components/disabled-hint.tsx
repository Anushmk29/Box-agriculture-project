"use client";

import { useId, type ButtonHTMLAttributes } from "react";
import { t, useLanguage } from "@/lib/language";

// A button that says why it is disabled, so a greyed-out action is never a mystery.
export function HintedButton({ reason, disabled, className = "primary-button", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { reason?: string }) {
  useLanguage();
  const id = useId();
  const showReason = Boolean(disabled && reason);
  return <span className="hinted-button">
    <button type="button" {...props} className={className} disabled={disabled} aria-describedby={showReason ? id : props["aria-describedby"]} />
    {showReason && <small id={id}>{t(reason)}</small>}
  </span>;
}
