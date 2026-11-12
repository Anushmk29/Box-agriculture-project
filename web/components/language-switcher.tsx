"use client";

import { setLanguage, useLanguage } from "@/lib/language";

export function LanguageSwitcher() {
  const language = useLanguage();
  return <div className="language-switcher" role="group" aria-label="Interface language / ภาษาที่แสดง">
    <button type="button" lang="th" aria-label="ภาษาไทย" aria-pressed={language === "th"} onClick={() => setLanguage("th")}>ไทย</button>
    <button type="button" lang="en" aria-label="English" aria-pressed={language === "en"} onClick={() => setLanguage("en")}>EN</button>
  </div>;
}
