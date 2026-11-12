"use client";

import { t, useLanguage } from "@/lib/language";

import { useState, type InputHTMLAttributes } from "react";
import { Eye, EyeOff } from "lucide-react";

// The toggle is labelled "characters", not "password", so it never competes with the field's own label.
export function PasswordInput(props: Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  useLanguage();
  const [visible, setVisible] = useState(false);
  return <span className="password-input">
    <input {...props} type={visible ? "text" : "password"} />
    <button type="button" aria-label={visible ? t("Hide characters") : t("Show characters")} aria-pressed={visible} onClick={() => setVisible(value => !value)}>
      {visible ? <EyeOff size={18} /> : <Eye size={18} />}
    </button>
  </span>;
}
