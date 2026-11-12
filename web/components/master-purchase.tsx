"use client";

import { t, useLanguage } from "@/lib/language";

import { useState } from "react";
import { Download } from "lucide-react";
import { api } from "@/lib/api";
import { HintedButton } from "@/components/disabled-hint";
import { StatusMessage } from "@/components/status-message";
import { TaskDialog } from "@/components/task-dialog";
import styles from "./master-purchase.module.css";

type PurchaseLine = {
  batch_reference: string; status: string; product_code: string; product_name: string; pack_size: string;
  source_files: string[]; shipment_dates: string[]; ordered_packs: number; pack_count_pending: boolean;
  ordered_kg: number; buffer_kg: number; target_packs: number; target_kg: number;
  actual_packs: number | null; actual_kg: number | null; notes: string;
};
type MarketList = {
  generated_at: string; batch_ids: string[]; total_target_kg: number;
  sections: { customer_id: string | null; customer_code: string; customer_name: string; total_target_kg: number; lines: PurchaseLine[] }[];
};

export function MasterPurchase({ batchIds }: { batchIds: string[] }) {
  useLanguage();
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [report, setReport] = useState<MarketList | null>(null);
  async function preview() {
    setBusy(true); setError("");
    try {
      setReport(await api<MarketList>("/procurement/master-purchase/preview", {
        method: "POST", body: JSON.stringify({ batch_ids: batchIds }),
      }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to prepare the market list. Try again."); }
    finally { setBusy(false); }
  }
  async function download() {
    if (!report) return;
    setBusy(true); setError("");
    try {
      const blob = await api<Blob>("/procurement/master-purchase/pdf", {
        method: "POST", body: JSON.stringify({ batch_ids: report.batch_ids }),
      }, { blob: true });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = "master-purchase-list.pdf";
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to download the PDF. Try again."); }
    finally { setBusy(false); }
  }
  return <div className={styles.action}>
    <HintedButton type="button" className="invoice-secondary-button" data-guide={batchIds.length ? t("Preview the Master Purchase List") : undefined} data-guide-priority="5" data-guide-detail={t("Review the separate customer sections, then download one market-shopping PDF.")} disabled={busy || !batchIds.length || batchIds.length > 50}
      reason={batchIds.length > 50 ? t("Select at most 50 purchases") : t("Select the purchases to take to market")} onClick={preview}>
      <Download size={16} aria-hidden />{busy && !report ? t("Preparing list…") : t("Create Master Purchase List")}
    </HintedButton>
    {!report && <StatusMessage error={error} />}
    {report && <TaskDialog className={styles.dialog} title={t("Master Purchase List")} description={t("One market list, with a separate section for each customer.")} size="lg" guidance={false} busy={busy}
      onClose={() => { setReport(null); setError(""); }} footer={<button type="button" className="primary-button" disabled={busy} onClick={download}><Download size={16} aria-hidden />{busy ? t("Preparing PDF…") : t("Download Master Purchase PDF")}</button>}>
      <StatusMessage error={error} />
      <p>{report.batch_ids.length} {report.batch_ids.length === 1 ? t("purchase") : t("purchases")} {t("· Total to buy: ")}<strong>{report.total_target_kg} {t("kg")}</strong></p>
      <p>{t("Review each customer’s quantities before going to market. Downloading uses the latest saved quantities and leaves purchase statuses unchanged. Unconfirmed purchases are draft.")}</p>
      {report.sections.map(section => <section className={styles.section} key={section.customer_id || "shared"} aria-label={section.customer_code}>
        <h2>{section.customer_name}{section.customer_name !== section.customer_code && ` · ${section.customer_code}`}</h2>
        <div className={styles.scroll} tabIndex={0} role="region" aria-label={t(`${section.customer_code} purchase products`)}>
          <table className="batch-table"><thead><tr><th scope="col">{t("Product / source")}</th><th scope="col">{t("Ordered")}</th><th scope="col">{t("Buffer")}</th><th scope="col">{t("To buy")}</th><th scope="col">{t("Actual / notes")}</th></tr></thead>
            <tbody>{section.lines.map((line, index) => <tr key={`${line.batch_reference}-${index}`}>
              <td><strong>{line.product_name}</strong><small>{line.product_code} · {line.pack_size}</small><small>{line.source_files.join(", ")}</small><small>{line.shipment_dates.join(", ")}</small></td>
              <td>{line.ordered_kg} {t("kg")}<small>{line.pack_count_pending ? t("Packs pending") : t(`${line.ordered_packs} packs`)}</small></td>
              <td>{line.buffer_kg} {t("kg")}</td><td><strong>{line.target_kg} {t("kg")}</strong><small>{line.pack_count_pending ? t("Packs pending") : t(`${line.target_packs} packs`)}</small></td>
              <td>{line.actual_kg === null ? t("Not confirmed") : t(`${line.actual_packs} packs / ${line.actual_kg} kg`)}<small>{line.notes}</small></td>
            </tr>)}</tbody>
          </table>
        </div><p className={styles.subtotal}>{t("Section to buy: ")}{section.total_target_kg} {t("kg")}</p>
      </section>)}
    </TaskDialog>}
  </div>;
}
