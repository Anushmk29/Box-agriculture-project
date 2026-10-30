import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({ icon: Icon, title, children, actions }: {
  icon: LucideIcon; title: string; children?: ReactNode; actions?: ReactNode;
}) {
  return <div className="panel empty-state">
    <span className="empty-state-icon" aria-hidden><Icon size={28} /></span>
    <h2>{title}</h2>
    {children && <p>{children}</p>}
    {actions && <div className="empty-state-actions">{actions}</div>}
  </div>;
}
