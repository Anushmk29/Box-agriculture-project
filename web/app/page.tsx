"use client";

import { t, useLanguage, displayLocale } from "@/lib/language";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Boxes, CalendarDays, CheckCheck, ClipboardCheck, FileText, PackageOpen, Plus, ReceiptText, RefreshCw, Truck, type LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session-context";
import { rowAction, scopedPacking, type WorkStage } from "@/lib/workflow";
import { recordLink, statusLabel, type RecordPage, type RegisterRow } from "@/components/record-register";
import { StatusMessage } from "@/components/status-message";
import "./today.css";

type Summary = { awaiting_intake?: number; awaiting_procurement?: number; awaiting_packing?: number };
type Queue = { key: string; stage: WorkStage; title: string; endpoint: string; href: string; permission: string; icon: LucideIcon };
const queues: Queue[] = [
  { key: "orders", stage: "orders", title: "Review customer orders", endpoint: "/orders/files", href: "/orders/files", permission: "validate_orders", icon: PackageOpen },
  { key: "procurement", stage: "procurement", title: "Confirm market purchases", endpoint: "/procurement/batches", href: "/procurement/batches", permission: "confirm_procurement", icon: ClipboardCheck },
  { key: "packing", stage: "packing", title: "Pack boxes", endpoint: "/packing/orders", href: "/packing/orders", permission: "floor_packing", icon: Boxes },
  { key: "ready", stage: "billing", title: "Ready to invoice", endpoint: "/invoicing/orders", href: "/billing", permission: "manage_billing", icon: ReceiptText },
  { key: "drafts", stage: "billing", title: "Review invoice drafts", endpoint: "/invoicing/records?invoice_group=drafts&view=all", href: "/billing?tab=invoices&invoice_group=drafts&view=all", permission: "manage_billing", icon: FileText },
];

function identityField(queue: Queue, row: RegisterRow) {
  return queue.stage === "orders" ? "raw_file_id" : queue.stage === "procurement" ? "batch_id" : queue.stage === "billing" && row.invoice_id ? "invoice_id" : "order_id";
}

function destination(queue: Queue, row: RegisterRow) {
  if (queue.stage === "procurement" && row.status === "HANDOFF_01_SENT") return scopedPacking(String(row.batch_id));
  const id = identityField(queue, row);
  const param = id === "raw_file_id" ? "fileId" : id === "batch_id" ? "batchId" : id === "invoice_id" ? "invoiceId" : "orderId";
  return recordLink(`/${queue.stage}`, param, row[id]);
}

function shipmentDate(value: RegisterRow[string]) {
  if (!value) return "No shipment date";
  const raw = String(value);
  const date = new Date(`${raw.slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? raw : new Intl.DateTimeFormat(displayLocale(), { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function StatCard({ icon: Icon, label, count, href, stage }: { icon: LucideIcon; label: string; count?: number; href: string; stage: WorkStage }) {
  useLanguage();
  return <div className="operations-stat" data-stage={stage}>
    <dt><span className="stat-stage-icon"><Icon size={19} aria-hidden /></span>{t(label)}</dt>
    <dd><Link href={href} aria-label={`${label}: ${count ?? "unavailable"}`}>{count ?? "-"}<ArrowRight size={14} aria-hidden /></Link></dd>
  </div>;
}

export default function OperationsPage() {
  useLanguage();
  const session = useSession();
  const permissionKey = [...(session?.permissions || [])].sort().join(",");
  const [summary, setSummary] = useState<Summary>();
  const [pages, setPages] = useState<Record<string, RecordPage<RegisterRow>>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [lastChecked, setLastChecked] = useState<string>();
  const [pendingOnly, setPendingOnly] = useState(true);
  const owner = session?.role === "OWNER_ADMIN";
  const accounting = !owner && session?.permissions.includes("manage_billing");
  const permitted = (permission: string) => session?.permissions.includes(permission);
  const visible = queues.filter(queue => permitted(queue.permission));
  const ordered = accounting ? [...visible.filter(queue => queue.stage === "billing"), ...visible.filter(queue => queue.stage !== "billing")] : visible;
  const shown = ordered.filter(queue => !pendingOnly || !pages[queue.key] || pages[queue.key].total > 0 || errors[queue.key]);
  const hasErrors = Object.keys(errors).length > 0;

  useEffect(() => {
    let active = true;
    let pending = false;
    const allowed = queues.filter(queue => permissionKey.split(",").includes(queue.permission));
    async function load() {
      if (pending) return;
      pending = true;
      setRefreshing(true);
      const results = await Promise.allSettled([
        api<Summary>("/operations/summary"),
        ...allowed.map(queue => api<RecordPage<RegisterRow>>(`${queue.endpoint}${queue.endpoint.includes("?") ? "&" : "?view=pending&"}limit=5&sort=shipment_date&direction=asc`)),
      ]);
      if (!active) return;
      const problems: Record<string, string> = {};
      const first = results[0];
      if (first.status === "fulfilled") setSummary(first.value as Summary);
      else problems.summary = "The overview could not be refreshed. Retry to see current totals.";
      const fresh: Record<string, RecordPage<RegisterRow>> = {};
      allowed.forEach((queue, index) => {
        const result = results[index + 1];
        if (result.status === "fulfilled" && "items" in result.value && Array.isArray(result.value.items)) fresh[queue.key] = result.value as RecordPage<RegisterRow>;
        else problems[queue.key] = "This work queue could not be loaded. Retry or open the full queue.";
      });
      setPages(current => ({ ...current, ...fresh }));
      setErrors(problems);
      if (!Object.keys(problems).length) setLastChecked(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
      setLoading(false);
      setRefreshing(false);
      pending = false;
    }
    void load();
    const timer = window.setInterval(load, 20000);
    return () => { active = false; window.clearInterval(timer); };
  }, [permissionKey, refresh]);

  return <div className="page today-page operations-dashboard">
    <header className="dashboard-heading">
      <div><h1>{t("Today")}</h1></div>
      <div className="dashboard-heading-actions">
        <span data-workspace-guide-host />
        <button type="button" className="invoice-secondary-button" data-guide={t("Refresh team work")} data-guide-priority="90" data-guide-detail={t("Check whether new tasks are ready for your team.")} disabled={refreshing} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={16} aria-hidden className={refreshing ? "spin" : undefined} />{t("Refresh work")}</button>
        {permitted("validate_orders") && <Link href="/orders" className="dashboard-primary" data-guide={t("Upload order")} data-guide-priority="60" data-guide-detail={t("Start a customer shipment by uploading its order file.")}><Plus size={18} aria-hidden />{t("Upload order")}</Link>}
      </div>
    </header>

    <StatusMessage error={errors.summary} />
    {owner && visible.some(queue => queue.stage !== "billing") && <section className="operations-overview" aria-label={t("Shipment overview")}>
      <h2>{t("Shipment overview")}</h2>
      <dl>
        {permitted("validate_orders") && <StatCard icon={PackageOpen} label={t("Orders awaiting review")} count={summary?.awaiting_intake} href="/orders/files" stage="orders" />}
        {permitted("confirm_procurement") && <StatCard icon={ClipboardCheck} label={t("Batches awaiting purchases")} count={summary?.awaiting_procurement} href="/procurement/batches" stage="procurement" />}
        {permitted("floor_packing") && <StatCard icon={Boxes} label={t("Orders awaiting packing")} count={summary?.awaiting_packing} href="/packing/orders" stage="packing" />}
      </dl>
    </section>}

    <section className="work-queues" aria-labelledby="team-work-heading">
      <div className="queue-toolbar">
        <h2 id="team-work-heading">{accounting ? t("Accounting work") : t("Team work")}</h2>
        <div className="queue-filters" role="group" aria-label={t("Filter work queues")}>
          <button type="button" aria-pressed={pendingOnly} onClick={() => setPendingOnly(true)}>{t("With pending work")}</button>
          <button type="button" aria-pressed={!pendingOnly} onClick={() => setPendingOnly(false)}>{t("All queues")}</button>
        </div>
      </div>
      <div className="dashboard-queues" role="region" aria-label={t("Team work queues")} tabIndex={0}>
        {shown.map(queue => {
          const page = pages[queue.key];
          const Icon = queue.icon;
          const rows = page?.items || [];
          return <section key={queue.key} className="today-queue" data-stage={queue.stage} aria-label={t(queue.title)}>
            <header className="queue-heading"><h3><span className="queue-stage-icon"><Icon size={18} aria-hidden /></span>{t(queue.title)}</h3>{page && <span className="queue-count" aria-label={t(`${page.total} pending`)}>{page.total}</span>}</header>
            {errors[queue.key] && <div className="queue-error" role="alert"><p>{t(errors[queue.key])} {page ? t("Previous results are shown and may be outdated.") : ""}</p><button type="button" className="invoice-secondary-button" onClick={() => setRefresh(value => value + 1)}>{t("Retry queue")}</button></div>}
            {loading ? <p className="queue-loading" role="status">{t("Loading work...")}</p>
              : rows.length ? <ul>{rows.slice(0, 3).map(row => {
                const title = String(row.file_name || (Array.isArray(row.files) ? row.files.join(", ") : "") || t("Source filename unavailable"));
                const customer = String(row.customer_name || row.customer || (Array.isArray(row.customers) ? row.customers.join(", ") : ""));
                const outstanding = queue.stage === "packing" ? Number(row.outstanding) : queue.stage === "orders" ? Number(row.unresolved_rows) : 0;
                const detail = outstanding > 0 ? queue.stage === "packing" ? `${outstanding} ${outstanding === 1 ? "box" : "boxes"} to finalize` : `${outstanding} ${outstanding === 1 ? "product needs" : "products need"} matching` : "";
                return <li key={`${queue.key}:${String(row[identityField(queue, row)])}`}>
                  <div className="queue-record">
                    <strong className="today-customer" title={customer || title}>{(customer || title).replaceAll("_", " ")}</strong>
                    {customer && <span className="today-reference" title={title}>{title}</span>}
                    <div className="queue-record-meta"><span><CalendarDays size={13} aria-hidden />{shipmentDate(row.shipment_date)}</span><span><Truck size={14} aria-hidden />{String(row.route || row.route_code || t("No route"))}</span></div>
                    <div className="queue-record-status"><span className="work-status">{t(statusLabel(String(row.status || "")))}</span>{detail && <span className="work-detail">{t(detail)}</span>}</div>
                  </div>
                  <Link className="invoice-secondary-button queue-record-action" data-guide={`${t(rowAction(queue.stage, row))}: ${title}`} data-guide-detail={`${customer ? `${customer}. ` : ""}${t(detail || "Open this pending task to continue the shipment.")}`} data-guide-priority="10" href={destination(queue, row)}>{t(rowAction(queue.stage, row))}<ArrowRight size={15} aria-hidden /></Link>
                </li>;
              })}</ul> : !errors[queue.key] && <p className="queue-empty"><CheckCheck size={16} aria-hidden />{t("No pending work")}</p>}
            <footer className="queue-footer"><span>{page && page.total > Math.min(rows.length, 3) ? t(`${Math.min(rows.length, 3)} of ${page.total} pending`) : ""}</span><Link href={queue.href}>{t("View all")}<ArrowRight size={14} aria-hidden /></Link></footer>
          </section>;
        })}
        {!loading && !shown.length && <div className="dashboard-all-clear"><CheckCheck size={24} aria-hidden /><h3>{t("You're all caught up")}</h3><p>{t("No pending work in your queues.")}</p><button type="button" className="invoice-secondary-button" onClick={() => setPendingOnly(false)}>{t("Show all queues")}</button></div>}
      </div>
      <div className={`dashboard-sync${hasErrors ? " has-error" : ""}`}><span className="sync-dot" aria-hidden /><span>{hasErrors ? t("Some queues need a refresh.") : t("Updated automatically.")}{lastChecked && <span className="last-refresh"> {t("Last successful refresh: ")}{lastChecked}.</span>}</span></div>
    </section>
  </div>;
}
