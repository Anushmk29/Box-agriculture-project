"use client";

import { t, useLanguage, displayLocale } from "@/lib/language";

import { TableLoadingSkeleton } from "@/components/loading-skeleton";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ArrowRight, CheckCircle2, ChevronDown, History, Layers3, LockKeyhole, PencilLine, ScanLine } from "lucide-react";
import { api } from "@/lib/api";
import { statusLabel, type RecordPage } from "@/components/record-register";
import { StatusMessage } from "@/components/status-message";
import { PackingItemEditor, type PackingItem } from "@/components/packing-item-editor";
import { useSession } from "@/lib/session-context";
import { confirmAction } from "@/lib/confirm";
import styles from "./scan-record.module.css";

type ScanResult = {
  box_id: string;
  packing_revision: number;
  order_id: string;
  qr_code_uid: string;
  box_number: number;
  order_ref_id: string;
  file_name: string;
  order_status: string;
  can_edit: boolean;
  customer_code: string;
  route_code: string;
  shipment_date: string;
  is_locked: boolean;
  packing_mode?: string | null;
  volume_cm3?: number;
  usable_volume_cm3?: number;
  needs_replan?: boolean;
  layers?: Array<{ item_id: string; layer: string; packed_count: number; product_name: string; pack_size: string; volume_source?: string }>;
  actual_net_weight_kg: number | null;
  actual_gross_weight_kg: number | null;
  items: PackingItem[];
  change_history: Array<{
    event_type: string;
    actor_name: string;
    actor_role: string;
    changed_at: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
  }>;
};

function changeSummary(change: ScanResult["change_history"][number]) {
  const fields = new Set([...Object.keys(change.before || {}), ...Object.keys(change.after || {})]);
  return [...fields].map(field => {
    const label = field.replaceAll("_", " ");
    const before = change.before?.[field] ?? "—";
    const after = change.after?.[field] ?? "—";
    return `${label}: ${String(before)} → ${String(after)}`;
  });
}

export default function ScanBoxPage() {
  useLanguage();
  const params = useParams<{ qrCode: string }>();
  const [box, setBox] = useState<ScanResult | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [editor, setEditor] = useState<{ boxId: string; revision: number; item?: PackingItem } | null>(null);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const permissions = useSession()?.permissions ?? [];
  const canEdit = Boolean(box?.can_edit && !box.is_locked && !box.needs_replan && !needsRefresh && !refreshBusy
    && box.box_id && Number.isInteger(box.packing_revision) && permissions.includes("floor_packing"));
  const [historyOffset, setHistoryOffset] = useState(0);
  const [history, setHistory] = useState<RecordPage<ScanResult["change_history"][number]>>();
  const [historyError, setHistoryError] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyRetry, setHistoryRetry] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => {
    let active = true;
    if (!box || !historyOpen) return;
    const timer = window.setTimeout(() => {
    setHistoryBusy(true); setHistoryError("");
    api<RecordPage<ScanResult["change_history"][number]>>(`/packing/scan/${encodeURIComponent(box.qr_code_uid)}/history?offset=${historyOffset}&limit=25`)
      .then(value => { if (active) setHistory(value); }).catch(reason => { if (active) setHistoryError(reason instanceof Error ? reason.message : "Unable to load change history"); }).finally(() => { if (active) setHistoryBusy(false); });
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [box, historyOpen, historyOffset, historyRetry]);


  const loadBox = useCallback(async (signal?: AbortSignal) => {
    if (!params.qrCode) return;
    let qrCode = params.qrCode;
    try { qrCode = decodeURIComponent(qrCode); } catch { /* Keep malformed legacy codes literal. */ }
    setRefreshBusy(true); setError("");
    try {
      const value = await api<ScanResult>(`/packing/scan/${encodeURIComponent(qrCode)}?include_history=false`, { signal });
      if (!signal?.aborted) { setBox(value); setNeedsRefresh(false); }
    } catch (reason) {
      if (!signal?.aborted) { setNeedsRefresh(true); setError(reason instanceof Error ? reason.message : "Unable to read this box code"); }
    } finally { if (!signal?.aborted) setRefreshBusy(false); }
  }, [params.qrCode]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setBox(null); setEditor(null); setMessage(""); setHistory(undefined); setHistoryOffset(0);
      void loadBox(controller.signal);
    }, 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [loadBox]);

  async function reloadBox() {
    if (editor && !await confirmAction({ title: "Discard unsaved packing changes?", body: "Your changes have not been saved. Load the latest packing record to review what changed.", confirmLabel: "Discard and reload", tone: "danger" })) return;
    setEditor(null); await loadBox();
  }

  return (
    <div className={`page ${styles.page}`}>
      <header className={styles.pageHeader}>
        <div><Link href="/packing/orders" className={styles.backLink}><ArrowLeft size={16} aria-hidden="true" /> {t("Back to packing orders")}</Link>
          <h1>{t("Box traceability")}</h1>
          <p>{t("Check the contents and packing details of this box.")}</p>
        </div>
        <div className={styles.headerTools}><Link href="/scan" className={`invoice-secondary-button ${styles.scanLink}`}><ScanLine size={18} aria-hidden="true" /> {t("Scan another box")}</Link><div data-workspace-guide-host /></div>
      </header>
      <StatusMessage error={error} success={message} />
      {needsRefresh && <button type="button" className="invoice-secondary-button" disabled={refreshBusy} onClick={() => void reloadBox()}>{t("Reload latest packing for review")}</button>}
      {!box && !error && <TableLoadingSkeleton label={t("Loading box record")} columns={["Product", "Pack size", "Quantity", "Net weight"]} />}
      {box && (
        <>
        <section className={styles.record} aria-label={t(`Box ${box.box_number} details`)}>
          <div className={styles.boxHeader}>
            <div className={styles.identity}>
              <div className={styles.boxTitle}><span>{t("Box #")}{box.box_number}</span><span className={box.is_locked ? styles.lockedStatus : styles.editableStatus}>{box.is_locked ? <CheckCircle2 size={16} aria-hidden="true" /> : <PencilLine size={16} aria-hidden="true" />}{box.is_locked ? t("Final pack locked") : box.can_edit ? t("Open for packing") : t("Read-only")}</span></div>
              <h2>{box.customer_code.replace(/_+/g, " ")}</h2>
              <dl className={styles.metadata}>
                <div><dt>{t("Shipment")}</dt><dd>{box.shipment_date ? new Intl.DateTimeFormat(displayLocale(), { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${box.shipment_date}T00:00:00Z`)) : t("Not scheduled")}</dd></div>
                <div><dt>{t("Route")}</dt><dd>{box.route_code}</dd></div>
                {box.packing_mode && <div><dt>{t("Packing rules")}</dt><dd>{box.packing_mode === "EL" ? t("EL") : t("Non-EL")}</dd></div>}
              </dl>
            </div>
            <div className={styles.actionArea}>
              <div className={styles.actionRow}><Link href={`/packing?orderId=${encodeURIComponent(box.order_id)}&box=${encodeURIComponent(box.qr_code_uid)}`} className="primary-button" data-guide={box.can_edit ? t("Open this box in packing") : t("View this box in packing")} data-guide-detail={box.can_edit ? t("Open packing to review or update this box.") : t("This box is locked. You can view its packing record.")}>{box.can_edit ? <PencilLine size={17} aria-hidden="true" /> : <LockKeyhole size={17} aria-hidden="true" />}{box.can_edit ? t("Open this box in packing") : t("View this box in packing")}<ArrowRight size={16} aria-hidden="true" /></Link></div>
              {!box.can_edit && <span className={styles.lockedNote}>{t("Final orders are read-only")}</span>}
            </div>
          </div>
          {box.needs_replan && <p className={styles.warning} role="alert">{t("Packing data changed. Open this box in packing to review a new plan.")}</p>}
          <dl className={styles.weights} aria-label={t("Recorded box weights")}>
            <div><dt>{t("Net weight ")}<span>{t("Products only")}</span></dt><dd>{box.actual_net_weight_kg == null ? t("Not finalized") : t(`${box.actual_net_weight_kg} kg`)}</dd></div>
            <div><dt>{t("Gross weight ")}<span>{t("Including packaging")}</span></dt><dd>{box.actual_gross_weight_kg == null ? t("Not finalized") : t(`${box.actual_gross_weight_kg} kg`)}</dd></div>
          </dl>
          <div className={styles.contentsLayout}>
          <section className={styles.contents} aria-labelledby="scan-contents-title">
            <div className={styles.sectionTitle}><h3 id="scan-contents-title">{t("Box contents")}</h3><span>{box.items.length} {box.items.length === 1 ? t("product") : t("products")} · {box.items.reduce((total, item) => total + item.packed_count, 0)} {t("packs")}</span></div>
            {canEdit && <div className={styles.editTools}><p>{t("Update this box to match what was actually packed. Remove unavailable items or choose a replacement.")}</p><button type="button" className="primary-button" onClick={() => setEditor({ boxId: box.box_id, revision: box.packing_revision })}>{t("Add packing item")}</button></div>}
            {box.items.length ? <table className={styles.table}>
            <caption className="sr-only">{t("Products packed in box ")}{box.box_number}</caption>
            <thead><tr><th scope="col">{t("Product")}</th><th scope="col">{t("Pack size")}</th><th scope="col">{t("Quantity")}</th><th scope="col">{t("Net weight")}</th></tr></thead>
            <tbody>{box.items.map((item, index) => (
              <tr key={`${item.product_code}-${item.pack_size}-${index}`}>
                <td><strong>{item.produce_name}</strong><small>{t("SKU ")}{item.product_code}</small>{canEdit && item.item_id && item.product_id && <button type="button" className={`invoice-secondary-button ${styles.editItem}`} aria-label={t(`Edit contents for ${item.produce_name}`)} onClick={() => setEditor({ boxId: box.box_id, revision: box.packing_revision, item })}>{t("Edit / remove")}</button>}</td><td>{item.pack_size}</td><td>{item.packed_count}</td><td>{item.net_weight_kg.toFixed(2)} {t("kg")}</td>
              </tr>
            ))}</tbody>
            </table> : <p className={styles.empty}>{t("No products recorded in this box yet.")}</p>}
          </section>
          {Boolean(box.layers?.length) && <aside className={styles.packingOrder} aria-labelledby="scan-packing-title"><h3 id="scan-packing-title"><Layers3 size={18} aria-hidden="true" /> {t("Packing order")}</h3><p>{t("Pack from bottom to top")}</p><ol>{box.layers!.map((item, index) => <li key={item.item_id}><span className={styles.layerNumber}>{index + 1}</span><div><strong>{item.packed_count} × {item.product_name}</strong><span>{item.pack_size} · {item.layer}</span>{["RESEARCH", "PROXY"].includes(item.volume_source || "") && <small>{t("Reviewed volume estimate")}</small>}</div></li>)}</ol></aside>}
          </div>
        </section>
          <div className={styles.supporting}>
          <details className={styles.disclosure}>
            <summary><div><h3>{t("Order file & box details")}</h3><p>{t("Uploaded filename, sticker code, and order status")}</p></div><ChevronDown size={18} aria-hidden="true" /></summary>
            <dl className={styles.references}><div><dt>{t("Order file")}</dt><dd>{box.file_name || t("Source filename unavailable")}</dd></div><div><dt>{t("Sticker code")}</dt><dd>{box.qr_code_uid}</dd></div><div><dt>{t("Order status")}</dt><dd>{box.order_status && t(statusLabel(box.order_status))}</dd></div></dl>
          </details>
          <details className={styles.disclosure} onToggle={event => setHistoryOpen(event.currentTarget.open)}>
            <summary><div><h3 id="scan-history-title"><History size={18} aria-hidden="true" /> {t("Change history")}</h3><p>{t("Who changed this order, when, and what changed")}</p></div><ChevronDown size={18} aria-hidden="true" /></summary>
            <section className={styles.historyBody} aria-labelledby="scan-history-title" aria-busy={historyBusy}>
            {!historyBusy && !historyError && history?.items.map((change, index) => <article className="scan-history-row" key={`${change.event_type}-${change.changed_at}-${index}`}>
              <div><strong>{t(change.event_type.replaceAll("_", " ").toLowerCase())}</strong><span>{change.actor_name} · {new Intl.DateTimeFormat(displayLocale(), { dateStyle: "medium", timeStyle: "short" }).format(new Date(change.changed_at))}</span></div>
              <ul>{changeSummary(change).map(detail => <li key={detail}>{t(detail)}</li>)}</ul>
            </article>)}
            {!historyBusy && !historyError && history?.total === 0 && <p className="scan-history-empty">{t("No recorded changes yet.")}</p>}
            {historyBusy && <p role="status">{t("Loading change history…")}</p>}{historyError && <p role="alert">{historyError}<button type="button" className="invoice-secondary-button" onClick={() => setHistoryRetry(value => value + 1)}>{t("Retry history")}</button></p>}
            {history && <div className="register-pagination"><span>{history.total} {t("changes")}</span><button type="button" disabled={historyBusy || !historyOffset} onClick={() => setHistoryOffset(value => Math.max(0, value - 25))}>{t("Previous changes")}</button><button type="button" disabled={historyBusy || !history.has_more} onClick={() => setHistoryOffset(value => value + 25)}>{t("Next changes")}</button></div>}
            </section>
          </details>
          </div>
        </>
      )}
      {editor && <PackingItemEditor {...editor} onClose={() => setEditor(null)} onConflict={() => setNeedsRefresh(true)} onReload={reloadBox} onSaved={async () => {
        setEditor(null); setNeedsRefresh(true);
        setMessage("Packing item saved. Review the contents before finalizing packing.");
        await loadBox();
      }} />}
    </div>
  );
}
