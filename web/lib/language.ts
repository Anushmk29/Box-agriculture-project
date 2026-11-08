"use client";

import { useSyncExternalStore } from "react";
import { translateText, type Language } from "./translations";

const storageKey = "freshflow-language";
const listeners = new Set<() => void>();
let language: Language = "en";

function publish(next: Language) {
  language = next;
  document.documentElement.lang = next;
  listeners.forEach(listener => listener());
}

export function restoreLanguage() {
  const preferred: Language = navigator.language.toLowerCase().startsWith("th") ? "th" : "en";
  try {
    const saved = localStorage.getItem(storageKey);
    publish(saved === "th" || saved === "en" ? saved : preferred);
  } catch { publish(preferred); }
}

export function setLanguage(next: Language) {
  try { localStorage.setItem(storageKey, next); } catch { /* The switch still works when browser storage is disabled. */ }
  publish(next);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useLanguage(): Language {
  return useSyncExternalStore(subscribe, () => language, () => "en");
}

export function t(source: string): string;
export function t<T>(source: T): T;
export function t(source: unknown): unknown {
  return typeof source === "string" ? translateText(source, language) : source;
}

export function displayLocale() {
  return language === "th" ? "th-TH-u-ca-gregory" : "en-GB";
}
