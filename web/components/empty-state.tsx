"use client";

import { t, useLanguage } from "@/lib/language";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({ icon: Icon, title, children, actions }: {
  icon: LucideIcon; title: string; children?: ReactNode; actions?: ReactNode;
}) {
  useLanguage();
  return <div className="panel empty-state">
    <span className="empty-state-icon" aria-hidden><Icon size={28} /></span>
    <h2>{t(title)}</h2>
    {children && <p>{children}</p>}
    {actions && <div className="empty-state-actions">{actions}</div>}
  </div>;
}
