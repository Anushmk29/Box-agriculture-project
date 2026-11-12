"use client";

import { t, useLanguage } from "@/lib/language";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { SlidersHorizontal } from "lucide-react";
import { api } from "@/lib/api";
import { useCompactLayout } from "@/lib/use-compact-layout";
import { TaskDialog } from "@/components/task-dialog";
import { safeDestination } from "@/lib/workflow";
import { TableLoadingSkeleton, TableSkeletonRows } from "@/components/loading-skeleton";

export type RegisterRow = Record<string, string | number | boolean | null | string[]>;
export type RecordPage<T> = { items: T[]; total: number; offset: number; limit: number; has_more: boolean; scope_label?: string | null };
export type RegisterColumn = { key: string; label: string; sort?: string; render?: (row: RegisterRow) => ReactNode };
type Props = {
  title: string; description?: string; endpoint: string; identity: string; columns: RegisterColumn[];
  open?: (row: RegisterRow) => string; statuses?: string[]; filters?: boolean; views?: boolean;
  toolbar?: ReactNode; selectable?: (row: RegisterRow) => boolean;
  selectionAction?: (rows: RegisterRow[]) => ReactNode; refreshKey?: number;
  searchLabel?: string; searchPlaceholder?: string;
  actionLabel?: (row: RegisterRow) => string;
  defaultSort?: string; defaultDirection?: "asc" | "desc";
  defaultView?: "pending" | "completed" | "all";
  mobileCards?: boolean;
  scopeLabel?: string;
  scopeDescription?: ReactNode;
  /** Shown when the default view is empty and no filters are set. */
  emptyMessage?: string;
};

export function RecordRegister(props: Props) {
  useLanguage();
  return <Suspense fallback={<TableLoadingSkeleton columns={props.columns.map(column => column.label)} />}><RegisterContent {...props} /></Suspense>;
}

function RegisterContent({ title, description, endpoint, identity, columns, open, statuses = [], filters = true,
  views = true, toolbar, selectable, selectionAction, refreshKey = 0, searchLabel = "Search records", searchPlaceholder = "Customer or uploaded filename",
  actionLabel, defaultSort = "created_at", defaultDirection = "desc", defaultView = "pending", mobileCards = false, emptyMessage, scopeLabel, scopeDescription }: Props) {
  useLanguage();
  const pathname = usePathname();
  const router = useRouter();
  const compact = useCompactLayout();
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);
  const [viewCounts, setViewCounts] = useState<Record<string, number>>({});
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const params = useMemo(() => new URLSearchParams(query), [query]);
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const limit = [25, 50, 100].includes(Number(params.get("limit"))) ? Number(params.get("limit")) : 25;
  const view = params.get("view") || defaultView;
  const [page, setPage] = useState<RecordPage<RegisterRow>>();
  const [busy, setBusy] = useState(true);
  const [resolvedQuery, setResolvedQuery] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<Map<string, RegisterRow>>(new Map());
  // Page movement preserves selection; any filter/sort/tab change clears it.
  const selectionScope = useMemo(() => {
    const scope = new URLSearchParams(query); scope.delete("offset"); scope.delete("limit");
    return `${endpoint}?${scope}&refresh=${refreshKey}`;
  }, [query, endpoint, refreshKey]);
  const [selectedScope, setSelectedScope] = useState(selectionScope);
  const effectiveSelected = selectedScope === selectionScope ? selected : new Map<string, RegisterRow>();
  if (selectedScope !== selectionScope) {
    // Forget the old selection permanently, including when filters later return to their old values.
    setSelectedScope(selectionScope);
    setSelected(new Map());
  }

  function update(changes: Record<string, string>, reset = true) {
    const next = new URLSearchParams(query);
    for (const [key, value] of Object.entries(changes)) { if (value) next.set(key, value); else next.delete(key); }
    if (reset) next.delete("offset");
    window.history.replaceState(null, "", `${pathname}?${next}`);
  }

  useEffect(() => {
    let active = true;
    const request = new URLSearchParams(query);
    // Only register parameters go to the API; detail IDs and return paths stay local.
    for (const key of Array.from(request.keys())) if (!["q", "view", "customer", "route", "date_from", "date_to", "status", "sort", "direction", "offset", "limit", "batch_id", "raw_file_id", "invoice_group"].includes(key)) request.delete(key);
    if (!request.has("view")) request.set("view", defaultView);
    if (!request.has("sort")) request.set("sort", defaultSort);
    if (!request.has("direction")) request.set("direction", defaultDirection);
    request.set("limit", String(limit)); request.set("offset", String(offset));
    const timer = window.setTimeout(() => {
      setBusy(true); setError("");
      api<RecordPage<RegisterRow>>(`${endpoint}?${request}`, {}, { background: true }).then(result => {
        if (!active) return;
        if (offset > 0 && offset >= result.total) {
          const next = new URLSearchParams(query); next.set("offset", String(Math.max(0, Math.ceil(result.total / limit) - 1) * limit));
          window.history.replaceState(null, "", `${pathname}?${next}`); return;
        }
        setPage(result); setResolvedQuery(query);
      }).catch(reason => { if (active) { setPage(undefined); setError(reason instanceof Error ? reason.message : "Unable to load records"); } })
        .finally(() => { if (active) setBusy(false); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [query, endpoint, limit, offset, pathname, retry, refreshKey, defaultSort, defaultDirection, defaultView]);

  const countScope = useMemo(() => {
    const scope = new URLSearchParams(query);
    for (const key of Array.from(scope.keys())) if (!["q", "customer", "route", "date_from", "date_to", "status", "batch_id", "raw_file_id", "invoice_group"].includes(key)) scope.delete(key);
    return scope.toString();
  }, [query]);
  useEffect(() => {
    if (!views) return;
    let active = true;
    // Runs after the page read so a single failing request still surfaces in the register.
    const timer = window.setTimeout(() => {
      Promise.all(["pending", "completed", "all"].map(value => {
        const request = new URLSearchParams(countScope); request.set("view", value); request.set("limit", "1"); request.set("offset", "0");
        return api<RecordPage<RegisterRow>>(`${endpoint}?${request}`, {}, { background: true }).then(result => [value, result.total] as const);
      })).then(entries => { if (active) setViewCounts(Object.fromEntries(entries)); }).catch(() => { if (active) setViewCounts({}); });
    }, 700);
    return () => { active = false; window.clearTimeout(timer); };
  }, [views, countScope, endpoint, refreshKey, retry]);

  function select(row: RegisterRow, checked: boolean) {
    const next = new Map(effectiveSelected); const id = String(row[identity]);
    if (checked) next.set(id, row); else next.delete(id);
    setSelectedScope(selectionScope); setSelected(next);
  }
  const pageIsCurrent = page && page.offset === offset && page.limit === limit;
  // Hide old rows as soon as URL filters change, including the debounce interval.

  const showing = pageIsCurrent && !busy && resolvedQuery === query;
  const eligible = showing ? page.items.filter(row => selectable?.(row)) : [];
  const allPageSelected = eligible.length > 0 && eligible.every(row => effectiveSelected.has(String(row[identity])));
  const sort = params.get("sort") || defaultSort;
  const direction = params.get("direction") || defaultDirection;
  const scopeId = params.get("raw_file_id") || params.get("batch_id");
  const moreFilterKeys = ["customer", "route", "date_from", "date_to"];
  const activeMoreFilters = moreFilterKeys.filter(key => params.get(key)).length;
  const [moreFiltersOpen, setMoreFiltersOpen] = useState(activeMoreFilters > 0);
  const anyFilter = activeMoreFilters > 0 || Boolean(params.get("q") || params.get("status"));
  const emptyText = emptyMessage && view === defaultView && !anyFilter ? emptyMessage : "No matching records. Adjust the filters or choose another view.";
  useEffect(() => {
    if (!showing) return;
    try {
      const key = `register-scroll:${pathname}?${query}`;
      const saved = sessionStorage.getItem(key);
      if (saved !== null) { window.scrollTo(0, Number(saved)); sessionStorage.removeItem(key); }
    } catch { /* Storage can be disabled on shared workstations. */ }
  }, [showing, pathname, query]);

  function rememberScroll() {
    try { sessionStorage.setItem(`register-scroll:${pathname}?${query}`, String(window.scrollY)); } catch { /* Optional convenience. */ }
  }

  return <section className={`record-register${mobileCards ? " register-compact" : ""}`} aria-label={t(title)}>
    <header className="register-heading"><div><h1>{t(title)}</h1>{description && <p>{t(description)}</p>}</div>{toolbar}</header>
    {scopeId && <div className="register-scope"><p>{scopeDescription || <>{t("Showing records linked to ")}{params.has("raw_file_id") ? t("order file") : t("order files")} <strong>{(showing ? page?.scope_label : undefined) || scopeLabel || t("Selected order file")}</strong>.</>}</p><button type="button" onClick={() => update({ batch_id: "", raw_file_id: "" })}>{t("Show all records")}</button></div>}
    {views && <nav className="register-views" aria-label={t("Record views")}>{[["pending", "Pending work"], ["completed", "Completed"], ["all", "All"]].map(([value, label]) => <button key={value} type="button" aria-pressed={view === value} onClick={() => update({ view: value })}>{t(label)}{viewCounts[value] !== undefined && <span className="register-view-count" aria-hidden="true">{viewCounts[value]}</span>}</button>)}</nav>}
    {(() => {
      const statusField = statuses.length > 0 && <label>{t("Status")}<select value={params.get("status") || ""} onChange={event => update({ status: event.target.value })}><option value="">{t("All statuses")}</option>{statuses.map(status => <option key={status} value={status}>{t(statusLabel(status))}</option>)}</select></label>;
      const moreFields = filters && <><label>{t("Customer")}<input value={params.get("customer") || ""} onChange={event => update({ customer: event.target.value })} placeholder={t("Customer name or code")} /></label>
        <label>{t("Route")}<input value={params.get("route") || ""} onChange={event => update({ route: event.target.value })} placeholder={t("All routes")} /></label>
        <label>{t("Shipment from")}<input type="date" value={params.get("date_from") || ""} onChange={event => update({ date_from: event.target.value })} /></label>
        <label>{t("Shipment to")}<input type="date" value={params.get("date_to") || ""} onChange={event => update({ date_to: event.target.value })} /></label></>;
      const clearButton = <button type="button" onClick={() => update({ q: "", customer: "", route: "", date_from: "", date_to: "", status: "" })}>{t("Clear filters")}</button>;
      const activeCount = activeMoreFilters + (params.get("status") ? 1 : 0);
      const useSheet = compact && (filters || statuses.length > 0);
      return <>
        <div className="register-filters">
          <label className="register-search">{t(searchLabel)}<input type="search" data-guide={t(`Find ${title.toLowerCase()}`)} data-guide-priority="95" data-guide-detail={t(emptyMessage) || t("Search by customer or uploaded filename, or change the filters to find the work you need.")} value={params.get("q") || ""} onChange={event => update({ q: event.target.value })} placeholder={t(searchPlaceholder)} /></label>
          {useSheet
            ? <button type="button" className="register-more-filters" aria-haspopup="dialog" onClick={() => setFilterSheetOpen(true)}><SlidersHorizontal size={16} aria-hidden />{t("Filters")}{activeCount > 0 && <span className="register-filter-count">{activeCount}</span>}</button>
            : <>
              {filters && <button type="button" className="register-more-filters" aria-expanded={moreFiltersOpen} onClick={() => setMoreFiltersOpen(value => !value)}><SlidersHorizontal size={16} aria-hidden />{t("More filters")}{activeMoreFilters > 0 && <span className="register-filter-count">{activeMoreFilters}</span>}</button>}
              {filters && (moreFiltersOpen || activeMoreFilters > 0) && moreFields}
              {statusField}
              {clearButton}
            </>}
        </div>
        {useSheet && filterSheetOpen && <TaskDialog size="sheet" title={t("Filter records")} description={t("Results update as you choose.")} onClose={() => setFilterSheetOpen(false)}>
          <div className="register-filters register-filters-sheet">{statusField}{moreFields}
            <div className="register-sheet-actions">{clearButton}<button type="button" className="primary-button" onClick={() => setFilterSheetOpen(false)}>{showing ? t(`Show ${page.total} ${page.total === 1 ? "result" : "results"}`) : t("Show results")}</button></div>
          </div>
        </TaskDialog>}
      </>;
    })()}
    {selectable && <div className="register-selection"><strong>{effectiveSelected.size} {t("selected")}</strong><button type="button" onClick={() => { setSelected(new Map()); setSelectedScope(selectionScope); }}>{t("Clear selection")}</button>{selectionAction?.(Array.from(effectiveSelected.values()))}</div>}
    {mobileCards && <div className="mobile-register-sort"><label>{t("Sort records")}<select value={sort} onChange={event => update({ sort: event.target.value })}>{columns.filter(column => column.sort).map(column => <option key={column.key} value={column.sort}>{t(column.label)}</option>)}</select></label><button type="button" onClick={() => update({ direction: direction === "asc" ? "desc" : "asc" })}>{direction === "asc" ? t("Ascending") : t("Descending")}</button></div>}
    {error && <div role="alert" className="register-error">{t(error)} <button type="button" data-guide={t("Retry loading these records")} data-guide-priority="1" data-guide-detail={t("The last request failed. Retry before choosing a record.")} onClick={() => setRetry(value => value + 1)}>{t("Retry")}</button></div>}
    <div className="register-table-scroll" tabIndex={0} role="region" aria-label={t(`${title} table`)} aria-busy={!showing && !error}>
      <table className="register-table"><thead><tr>
        {selectable && <th><label><input type="checkbox" data-guide={!effectiveSelected.size ? t("Select the records to work on") : undefined} data-guide-priority="45" data-guide-detail={t("Check the listed records before selecting them. Only eligible records can be selected.")} aria-label={t("Select this page")} checked={allPageSelected} disabled={!eligible.length} onChange={event => {
          const next = new Map(effectiveSelected); for (const row of eligible) { const id = String(row[identity]); if (event.target.checked) next.set(id, row); else next.delete(id); }
          setSelectedScope(selectionScope); setSelected(next);
        }} /> {t("This page")}</label></th>}
        {columns.map((column, index) => <th key={column.key} className={index === 0 ? "register-identity" : ""} aria-sort={column.sort === sort ? direction === "asc" ? "ascending" : "descending" : undefined}>{column.sort ? <button type="button" onClick={() => update({ sort: column.sort!, direction: column.sort === sort && direction === "asc" ? "desc" : "asc" })}>{t(column.label)}{column.sort === sort ? direction === "asc" ? " ↑" : " ↓" : ""}</button> : t(column.label)}</th>)}
        {open && <th className="register-action">{t("Action")}</th>}
      </tr></thead><tbody>
        {!showing && !error && <TableSkeletonRows label={t("Loading records…")} columns={[
          ...(selectable ? [{ label: "This page" }] : []),
          ...columns.map((column, index) => ({ label: column.label, className: index === 0 ? "register-identity" : undefined })),
          ...(open ? [{ label: "Action", className: "register-action" }] : []),
        ]} />}
        {showing && page.items.map((row, rowIndex) => <tr key={String(row[identity])} style={{ "--row-index": Math.min(rowIndex, 12) } as CSSProperties}
          className={open ? "register-row-link" : undefined}
          onClick={open ? event => {
            // The whole row opens the record; controls inside the row keep their own behaviour.
            if ((event.target as HTMLElement).closest("a, button, input, select, label, textarea") || window.getSelection()?.toString()) return;
            rememberScroll(); router.push(open(row));
          } : undefined}>
          {selectable && <td><input type="checkbox" aria-label={t(`Select ${String(row[columns[0].key])}`)} checked={effectiveSelected.has(String(row[identity]))} disabled={!selectable(row)} onChange={event => select(row, event.target.checked)} /></td>}
          {columns.map((column, index) => <td key={column.key} data-label={t(column.label)} className={index === 0 ? "register-identity" : ""}>{column.render ? column.render(row) : column.key === "status" ? <StatusChip status={String(row[column.key])} /> : Array.isArray(row[column.key]) ? (row[column.key] as string[]).join(", ") : String(row[column.key] ?? "—")}</td>)}
          {open && <td className="register-action"><Link className="invoice-secondary-button btn-sm" data-guide={`${t(actionLabel?.(row) || "Open")}: ${String(row[columns[0].key])}`} data-guide-detail={t(description) || t("Open a record to continue its current work.")} data-guide-priority="35" href={open(row)} prefetch={false} onClick={rememberScroll}>{t(actionLabel?.(row) || "Open")}<span className="sr-only"> {String(row[columns[0].key])}</span></Link></td>}
        </tr>)}
        {showing && !page.items.length && <tr><td className="register-empty" colSpan={columns.length + (open ? 1 : 0) + (selectable ? 1 : 0)}>{t(emptyText)}</td></tr>}
      </tbody></table>
    </div>
    <footer className="register-pagination"><span>{showing && page.total ? t(`${offset + 1}–${offset + page.items.length} of ${page.total} records`) : showing ? t("0 records") : ""}</span>
      <label>{t("Rows per page")}<select value={limit} onChange={event => update({ limit: event.target.value })}>{[25, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label>
      <button type="button" disabled={!showing || offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - limit)) }, false)}>{t("Previous")}</button>
      <span>{t("Page ")}{Math.floor(offset / limit) + 1}{showing ? t(` of ${Math.max(1, Math.ceil(page.total / limit))}`) : ""}</span>
      <button type="button" disabled={!showing || !page.has_more} onClick={() => update({ offset: String(offset + limit) }, false)}>{t("Next")}</button>
      <form className="register-page-jump" onSubmit={event => {
        event.preventDefault();
        const number = Number(new FormData(event.currentTarget).get("page"));
        if (showing && Number.isInteger(number) && number >= 1 && number <= Math.max(1, Math.ceil(page.total / limit))) update({ offset: String((number - 1) * limit) }, false);
      }}><label>{t("Go to page")}<input aria-label={t("Page number")} name="page" type="number" min="1" max={showing ? Math.max(1, Math.ceil(page.total / limit)) : 1} key={offset} defaultValue={Math.floor(offset / limit) + 1} disabled={!showing} /></label><button type="submit" disabled={!showing}>{t("Go")}</button></form>
      <button type="button" onClick={() => setRetry(value => value + 1)}>{t("Refresh")}</button>
    </footer>
  </section>;
}

export type StatusTone = "neutral" | "info" | "progress" | "success" | "danger";

/** Waiting for someone = progress, moving through the floor = info, finished = success. */
export function statusTone(status: string): StatusTone {
  if (["CANCELLED"].includes(status)) return "danger";
  if (["CONFIRMED_PAID", "RECEIPTS_ISSUED", "ORDER_CLOSED"].includes(status)) return "success";
  if (["UPLOADED", "PARSED", "PURCHASING_CONSOLIDATED", "HANDOFF_02_RETURNED", "INVOICE_DRAFT", "INVOICE_SENT", "PARTIALLY_PAID"].includes(status)) return "progress";
  if (["SPLIT_AND_VALIDATED", "PROCUREMENT_CONFIRMED", "HANDOFF_01_SENT", "PACKING_DESIGNED", "FLOOR_PACKING", "FINAL_PACK_LOCKED", "INVOICES_DRAFTED", "INVOICES_SENT"].includes(status)) return "info";
  return "neutral";
}

export function StatusChip({ status }: { status: string }) {
  useLanguage();
  return <span className={`chip chip-${statusTone(status)}`}>{t(statusLabel(status))}</span>;
}

export function statusLabel(status: string) {
  const labels: Record<string, string> = {
    UPLOADED: "Awaiting review", PARSED: "Awaiting validation", SPLIT_AND_VALIDATED: "Orders split",
    PURCHASING_CONSOLIDATED: "Awaiting purchases", PROCUREMENT_CONFIRMED: "Ready for handoff",
    HANDOFF_01_SENT: "Sent to packing", PACKING_DESIGNED: "Boxes prepared", FLOOR_PACKING: "Packing in progress",
    FINAL_PACK_LOCKED: "Packing locked", HANDOFF_02_RETURNED: "Ready to invoice",
    INVOICES_DRAFTED: "Invoices drafted", INVOICES_SENT: "Invoices issued", INVOICE_DRAFT: "Draft",
    INVOICE_SENT: "Issued", PARTIALLY_PAID: "Partially paid", CONFIRMED_PAID: "Paid",
    RECEIPTS_ISSUED: "Receipts issued", ORDER_CLOSED: "Closed", CANCELLED: "Cancelled", UNAVAILABLE: "Closed — unavailable",
  };
  return labels[status] || status.toLowerCase().replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
}

export function recordLink(path: string, key: string, id: string | number | boolean | null | string[]) {
  const params = new URLSearchParams({ [key]: String(id), returnTo: window.location.pathname + window.location.search });
  return `${path}?${params}`;
}

export function RegisterBackLink(props: { fallback: string; label?: string }) {
  useLanguage();
  return <Suspense fallback={<Link href={props.fallback}>{t(props.label || "Back to register")}</Link>}><BackLink {...props} /></Suspense>;
}

function BackLink({ fallback, label = "Back to register" }: { fallback: string; label?: string }) {
  useLanguage();
  const params = useSearchParams(); const value = params.get("returnTo");
  const safe = safeDestination(value);
  const href = value && (safe === "/" || safe === fallback || safe.startsWith(`${fallback}?`)) ? safe : fallback;
  return <Link className="invoice-secondary-button" href={href}>{href === "/" ? t("Back to Today") : t(label)}</Link>;
}
