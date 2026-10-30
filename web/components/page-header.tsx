import type { ReactNode } from "react";

// One page shape: context and title on the left, the page's main action on the right.
export function PageHeader({ eyebrow, title, description, back, actions }: {
  eyebrow?: ReactNode; title: ReactNode; description?: ReactNode; back?: ReactNode; actions?: ReactNode;
}) {
  return <header className="page-header-bar">
    <div className="page-header-copy">
      {back && <div className="page-header-back">{back}</div>}
      {eyebrow && <p className="eyebrow">{eyebrow}</p>}
      <h1>{title}</h1>
      {description && <p className="page-header-description">{description}</p>}
    </div>
    {actions && <div className="page-header-actions">{actions}</div>}
  </header>;
}
