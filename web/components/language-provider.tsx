"use client";

import { useEffect, type ReactNode } from "react";
import { restoreLanguage } from "@/lib/language";

export function LanguageProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    restoreLanguage();
    const changed = (event: StorageEvent) => { if (event.key === "freshflow-language" || event.key === null) restoreLanguage(); };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, []);
  return children;
}
