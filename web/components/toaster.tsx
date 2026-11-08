"use client";

import { t, useLanguage } from "@/lib/language";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { dismissToast, getServerToasts, getToasts, subscribeToasts, type Toast } from "@/lib/toast";

const icons = { success: CheckCircle2, error: TriangleAlert, info: Info };

function ToastItem({ item }: { item: Toast }) {
  useLanguage();
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const timer = window.setTimeout(() => dismissToast(item.id), item.tone === "error" ? 8000 : 4500);
    return () => window.clearTimeout(timer);
  }, [item.id, item.tone, paused]);
  const Icon = icons[item.tone];
  return <div className={`toast toast-${item.tone}`} role={item.tone === "error" ? "alert" : "status"}
    onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}>
    <Icon size={18} aria-hidden />
    <p>{t(item.message)}</p>
    {item.next && <Link href={item.next.href} className="toast-next" onClick={() => dismissToast(item.id)}>{t(item.next.label)}</Link>}
    <button type="button" className="toast-close" aria-label={t("Dismiss")} onClick={() => dismissToast(item.id)}><X size={16} /></button>
  </div>;
}

export function Toaster() {
  useLanguage();
  const toasts = useSyncExternalStore(subscribeToasts, getToasts, getServerToasts);
  return <div className="toaster" aria-live="polite">{toasts.map(item => <ToastItem key={item.id} item={item} />)}</div>;
}
