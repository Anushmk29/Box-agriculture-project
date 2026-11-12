"use client";

import { t, useLanguage } from "@/lib/language";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { WorkspaceGuide } from "@/components/workspace-guide";

// Focused operations inherit the existing leaf-green visual system.
// A named task opens above the workspace; its result returns to the same page.
export function TaskDialog({ title, description, onClose, busy = false, size = "md", className = "", guidance = true, footer, children }: {
  title: string; description: string; onClose: () => void; busy?: boolean; size?: "sm" | "md" | "lg" | "sheet";
  className?: string; guidance?: boolean; footer?: ReactNode; children: ReactNode;
}) {
  useLanguage();
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  const [closing, setClosing] = useState(false);
  useEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    return () => { dialog?.close(); if (opener?.isConnected) opener.focus(); };
  }, []);

  // The close button and Escape play a short exit before the parent unmounts the dialog.
  function requestClose() {
    if (busy || closing) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { onClose(); return; }
    setClosing(true);
    window.setTimeout(onClose, 160);
  }

  return <dialog ref={ref} className={`task-dialog task-dialog-${size}${closing ? " is-closing" : ""} ${className}`} aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}
    onCancel={event => { event.preventDefault(); requestClose(); }}>
    <header className="task-dialog-heading">
      <div><h2 id={`${id}-title`}>{t(title)}</h2><p id={`${id}-description`}>{t(description)}</p></div>
      <button type="button" className="task-close" aria-label={t(`Close ${t(title)}`)} disabled={busy} onClick={requestClose}><X size={20} /></button>
    </header>
    <div className="task-dialog-body" aria-busy={busy}>{guidance && <WorkspaceGuide inDialog scopeRef={ref} />}{children}</div>
    {footer && <footer className="task-dialog-footer">{footer}</footer>}
  </dialog>;
}
