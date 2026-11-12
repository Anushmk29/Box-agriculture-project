"use client";

import { t, useLanguage } from "@/lib/language";
type SkeletonColumn = { label: string; className?: string };

/** Render inside tbody so placeholders follow the table's real columns and mobile layout. */
export function TableSkeletonRows({ columns, rows = 5, label }: {
  columns: readonly SkeletonColumn[]; rows?: number; label?: string;
}) {
  useLanguage();
  return Array.from({ length: rows }, (_, row) => <tr className="table-skeleton-row" key={row}>
    {columns.map((column, index) => <td key={index} className={column.className} data-label={column.label || undefined}>
      <div className="table-skeleton-cell" role={row === 0 && index === 0 && label ? "status" : undefined}
        aria-label={row === 0 && index === 0 ? t(label) : undefined}>
        {row === 0 && index === 0 && label && <span className="sr-only">{t(label)}</span>}
        <span aria-hidden="true" className={`skeleton table-skeleton-line table-skeleton-width-${(row + index) % 3}`} />
        {index === 0 && <span aria-hidden="true" className="skeleton table-skeleton-detail" />}
      </div>
    </td>)}
  </tr>);
}

export function TableLoadingSkeleton({ label = "Loading records…", columns, rows = 5 }: {
  label?: string; columns: readonly string[]; rows?: number;
}) {
  useLanguage();
  return <section className="table-loading-skeleton" role="status" aria-label={t(label)} aria-busy="true">
    <span className="sr-only">{t(label)}</span>
    <div className="table-skeleton-scroll" aria-hidden="true">
      <table className="register-table"><thead><tr>{columns.map((column, index) => <th scope="col" key={index}>{t(column)}</th>)}</tr></thead>
        <tbody><TableSkeletonRows columns={columns.map(column => ({ label: column }))} rows={rows} /></tbody>
      </table>
    </div>
  </section>;
}

export function LoadingSkeleton({ label = "Loading workspace…", description, compact = false }: {
  label?: string; description?: string; compact?: boolean;
}) {
  useLanguage();
  return <section className={`loading-skeleton${compact ? " loading-skeleton-compact" : " panel"}`} role="status" aria-label={t(label)} aria-busy="true">
    <div className="loading-skeleton-caption"><strong>{t(label)}</strong>{description && <p>{t(description)}</p>}</div>
    <div className="loading-skeleton-shapes" aria-hidden="true">
      {!compact && <div className="loading-skeleton-heading"><span className="skeleton loading-skeleton-icon" /><div><span className="skeleton skeleton-title" /><span className="skeleton skeleton-line" /></div></div>}
      <div className="loading-skeleton-rows">{[0, 1, 2].map(row => <div className="loading-skeleton-row" key={row}><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div>)}</div>
    </div>
  </section>;
}

export function PageLoadingSkeleton({ label = "Loading workspace…" }: { label?: string }) {
  useLanguage();
  return <div className="page workspace-loading"><LoadingSkeleton label={t(label)} description={t("Preparing this page. You can still use the menu.")} /></div>;
}
