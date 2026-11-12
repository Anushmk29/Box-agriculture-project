"use client";

import { t, useLanguage } from "@/lib/language";

import { useId, useState, type ReactNode } from "react";
import { ArrowUpRight, ChevronDown } from "lucide-react";
import { TaskDialog } from "@/components/task-dialog";

export function OrderDisclosure({ title, description, icon, children, open = false, className = "", modal = false, busy = false, feedback, attention }: {
  title: string; description: string; icon: ReactNode; children: ReactNode; open?: boolean; className?: string;
  modal?: boolean; busy?: boolean; feedback?: ReactNode; attention?: string;
}) {
  useLanguage();
  const [dialogOpen, setDialogOpen] = useState(false);
  const descriptionId = useId();

  return <><details className={`order-disclosure ${className}`} open={open || Boolean(attention)}>
    <summary>
      <span className="order-disclosure-copy"><strong>{t(title)}</strong></span>
      {attention && <span className="order-disclosure-attention" role="img" aria-label={t("Needs attention")} title={attention} />}
      <span className="order-disclosure-state" aria-hidden="true"><ChevronDown size={18} /></span>
    </summary>
    <div className="order-disclosure-body">
      <p id={descriptionId} className="order-disclosure-description">{attention || t(description)}</p>
      {modal ? <button type="button" className="order-action-trigger" aria-label={t(title)} aria-describedby={descriptionId} aria-haspopup="dialog" disabled={busy} onClick={() => setDialogOpen(true)}>
        <span aria-hidden="true">{icon}</span>{t(title)}<ArrowUpRight size={17} aria-hidden="true" />
      </button> : children}
    </div>
  </details>
    {modal && dialogOpen && <TaskDialog title={t(title)} description={t(description)} size="lg" guidance={false} busy={busy} onClose={() => setDialogOpen(false)}>
      {feedback}
      <div className="order-action-content">{children}</div>
    </TaskDialog>}
  </>;
}
