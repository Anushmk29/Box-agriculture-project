"use client";

import { useSyncExternalStore } from "react";

const key = "order-guidance-hints"; // Keep the existing Orders preference.
const event = "workspace-guidance-preference";
let fallback = true;

function snapshot() {
  try { return window.sessionStorage.getItem(key) !== "off"; }
  catch { return fallback; }
}
function subscribe(notify: () => void) {
  window.addEventListener(event, notify);
  window.addEventListener("storage", notify);
  return () => { window.removeEventListener(event, notify); window.removeEventListener("storage", notify); };
}
export function useGuidanceHints() {
  const enabled = useSyncExternalStore(subscribe, snapshot, () => true);
  function toggle() {
    fallback = !snapshot();
    try { window.sessionStorage.setItem(key, fallback ? "on" : "off"); } catch { /* In-memory preference still works. */ }
    window.dispatchEvent(new Event(event));
  }
  return { enabled, toggle };
}
