import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";

export function OrderDisclosure({ title, description, icon, children, open = false, className = "" }: {
  title: string; description: string; icon: ReactNode; children: ReactNode; open?: boolean; className?: string;
}) {
  return <details className={`order-disclosure ${className}`} open={open}>
    <summary>
      <span className="order-disclosure-icon" aria-hidden="true">{icon}</span>
      <span className="order-disclosure-copy"><strong>{title}</strong><span>{description}</span></span>
      <span className="order-disclosure-state" aria-hidden="true"><span className="when-closed">Show</span><span className="when-open">Hide</span><ChevronDown size={20} /></span>
    </summary>
    <div className="order-disclosure-body">{children}</div>
  </details>;
}
