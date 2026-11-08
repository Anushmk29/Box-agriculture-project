"use client";

import { t, useLanguage } from "@/lib/language";

import { useEffect, useId, useRef } from "react";
import { ShoppingBag } from "lucide-react";

export function LoadingDialog({ title = "Loading", description = "Preparing your items. Please wait…" }: {
  title?: string; description?: string;
}) {
  useLanguage();
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();

  useEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return (
    <dialog ref={ref} className="loading-dialog" aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-description`} onCancel={event => event.preventDefault()}>
      <div className="loading-dialog-icon" aria-hidden="true"><ShoppingBag size={28} /></div>
      <h2 id={`${id}-title`}>{t(title)}</h2>
      <p id={`${id}-description`} role="status">{t(description)}</p>
      <div className="loading-dialog-progress" role="progressbar" aria-label={t(title)}>
        <span />
      </div>
    </dialog>
  );
}
