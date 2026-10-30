"use client";

import { TableLoadingSkeleton } from "@/components/loading-skeleton";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { CheckCircle2, History, LockKeyhole, PencilLine } from "lucide-react";
import { api } from "@/lib/api";
import type { RecordPage } from "@/components/record-register";
import { StatusMessage } from "@/components/status-message";

type ScanResult = {
  order_id: string;
  qr_code_uid: string;
  box_number: number;
  order_ref_id: string;
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
  items: Array<{
    product_code: string;
    produce_name: string;
    pack_size: string;
    packed_count: number;
    net_weight_kg: number;
  }>;
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
  const params = useParams<{ qrCode: string }>();
  const [box, setBox] = useState<ScanResult | null>(null);
  const [error, setError] = useState("");
  const [historyOffset, setHistoryOffset] = useState(0);
  const [history, setHistory] = useState<RecordPage<ScanResult["change_history"][number]>>();
  const [historyError, setHistoryError] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyRetry, setHistoryRetry] = useState(0);
  useEffect(() => {
    let active = true;
    if (!box) return;
    const timer = window.setTimeout(() => {
    setHistoryBusy(true); setHistoryError("");
    api<RecordPage<ScanResult["change_history"][number]>>(`/packing/scan/${encodeURIComponent(box.qr_code_uid)}/history?offset=${historyOffset}&limit=25`)
      .then(value => { if (active) setHistory(value); }).catch(reason => { if (active) setHistoryError(String(reason)); }).finally(() => { if (active) setHistoryBusy(false); });
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [box, historyOffset, historyRetry]);


  useEffect(() => {
    if (!params.qrCode) return;
    let qrCode = params.qrCode;
    try { qrCode = decodeURIComponent(qrCode); } catch { /* Keep malformed legacy codes literal. */ }
    api<ScanResult>(`/packing/scan/${encodeURIComponent(qrCode)}?include_history=false`)
      .then(setBox)
      .catch(reason => setError(reason instanceof Error ? reason.message : "Unable to read this box code"));
  }, [params.qrCode]);

  return (
    <div className="page" style={{ maxWidth: "900px" }}>
      <header className="page-header">
        <p className="eyebrow">{error ? "Box scan" : "Verified box scan"}</p>
        <h1>Box traceability</h1>
      </header>
      <StatusMessage error={error} />
      {error && <div className="scan-actions"><Link href="/scan" className="primary-button">Scan another box</Link><Link href="/packing/orders" className="invoice-secondary-button">Back to packing orders</Link></div>}
      {!box && !error && <TableLoadingSkeleton label="Loading box record" columns={["SKU", "Product", "Pack", "Count", "Net kg"]} />}
      {box && (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>{box.customer_code} · Box #{box.box_number}</h2>
              <p>{box.order_ref_id} · {box.route_code} · {box.shipment_date}</p>
            </div>
            {box.is_locked && <span style={{ color: "var(--leaf-ink)", display: "flex", gap: "6px" }}><CheckCircle2 /> Final pack locked</span>}
          </div>
          {box.packing_mode && <p>Packing rules: <strong>{box.packing_mode === "EL" ? "EL" : "Non-EL"}</strong></p>}
          {box.needs_replan && <p role="alert">Packing data changed. Open this box in packing to review a new plan.</p>}
          {Boolean(box.layers?.length) && <div><h3>Pack from bottom to top</h3><ol>{box.layers!.map(item => <li key={item.item_id}>{item.layer}: {item.packed_count} x {item.product_name} {item.pack_size}{["RESEARCH", "PROXY"].includes(item.volume_source || "") && <small> (reviewed volume estimate)</small>}</li>)}</ol></div>}
          <p>Net: <strong>{box.actual_net_weight_kg ?? "Not finalized"} kg</strong> · Gross: <strong>{box.actual_gross_weight_kg ?? "Not finalized"} kg</strong></p>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th align="left">SKU</th><th align="left">Product</th><th align="left">Pack</th><th align="right">Count</th><th align="right">Net kg</th></tr></thead>
            <tbody>{box.items.map((item, index) => (
              <tr key={`${item.product_code}-${item.pack_size}-${index}`} style={{ borderTop: "1px solid var(--line)" }}>
                <td>{item.product_code}</td><td>{item.produce_name}</td><td>{item.pack_size}</td><td align="right">{item.packed_count}</td><td align="right">{item.net_weight_kg.toFixed(2)}</td>
              </tr>
            ))}</tbody>
          </table>
          <div className="scan-actions">
            <Link href={`/packing?orderId=${encodeURIComponent(box.order_id)}&box=${encodeURIComponent(box.qr_code_uid)}`} className="primary-button">{box.can_edit ? <PencilLine size={17} /> : <LockKeyhole size={17} />}{box.can_edit ? "Open this box in packing" : "View this box in packing"}</Link>
            {!box.can_edit && <span className="scan-locked-note"><LockKeyhole size={17} /> Final orders are read-only</span>}
            <Link href="/packing/orders" className="invoice-secondary-button">Back to packing orders</Link>
          </div>
          <section className="scan-history" aria-labelledby="scan-history-title">
            <div className="section-heading"><div><h3 id="scan-history-title">Change history</h3><p>Who changed this order, when, and what changed.</p></div><History /></div>
            {!historyBusy && !historyError && history?.items.map((change, index) => <article className="scan-history-row" key={`${change.event_type}-${change.changed_at}-${index}`}>
              <div><strong>{change.event_type.replaceAll("_", " ").toLowerCase()}</strong><span>{change.actor_name} · {new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(change.changed_at))}</span></div>
              <ul>{changeSummary(change).map(detail => <li key={detail}>{detail}</li>)}</ul>
            </article>)}
            {!historyBusy && !historyError && history?.total === 0 && <p className="scan-history-empty">No recorded changes yet.</p>}
            {historyBusy && <p role="status">Loading change history…</p>}{historyError && <p role="alert">{historyError}<button type="button" onClick={() => setHistoryRetry(value => value + 1)}>Retry history</button></p>}
            <div className="register-pagination"><span>{history?.total ?? 0} changes</span><button type="button" disabled={historyBusy || !historyOffset} onClick={() => setHistoryOffset(value => Math.max(0, value - 25))}>Previous changes</button><button type="button" disabled={historyBusy || !history?.has_more} onClick={() => setHistoryOffset(value => value + 25)}>Next changes</button></div>
          </section>
        </section>
      )}
    </div>
  );
}
