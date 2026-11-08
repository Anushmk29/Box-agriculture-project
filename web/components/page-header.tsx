"use client";

import { t, useLanguage } from "@/lib/language";
import type { ReactNode } from "react";

// One page shape: context and title on the left, the page's main action on the right.
export function PageHeader({ eyebrow, title, description, back, actions }: {
  eyebrow?: ReactNode; title: ReactNode; description?: ReactNode; back?: ReactNode; actions?: ReactNode;
}) {
  useLanguage();
  return <header className="page-header-bar">
    <div className="page-header-copy">
      {back && <div className="page-header-back">{back}</div>}
      {eyebrow && <p className="eyebrow">{t(eyebrow)}</p>}
      <h1>{t(title)}</h1>
      {description && <p className="page-header-description">{t(description)}</p>}
    </div>
    {actions && <div className="page-header-actions">{actions}</div>}
  </header>;
}
