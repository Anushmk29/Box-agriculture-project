"use client";

import { t, useLanguage, displayLocale } from "@/lib/language";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { RecordRegister, RegisterBackLink, recordLink, type RegisterColumn, type RegisterRow, type RecordPage } from "@/components/record-register";
import { TableLoadingSkeleton } from "@/components/loading-skeleton";
import { orderColumns, orderStatuses } from "@/lib/register-columns";
import { api } from "@/lib/api";
import { rowAction } from "@/lib/workflow";
import styles from "./orders.module.css";

const fileColumns: RegisterColumn[] = [
  { key: "file_name", label: "Excel / order file", sort: "name", render: row => <span className={styles.fileName}><strong>{row.file_name}</strong><small>{row.customer}</small></span> },
  { key: "order_count", label: "Subcustomers", sort: "orders", render: row => {
    const names = (row.subcustomers || []) as string[];
    const remaining = Number(row.order_count) - names.length;
    return <span className={styles.subcustomers}><strong>{row.order_count} {Number(row.order_count) === 1 ? t("suborder") : t("suborders")}</strong><small>{names.join(", ")}{remaining > 0 ? t(` +${remaining} more`) : ""}</small></span>;
  } },
  { key: "shipment_date", label: "Shipment", sort: "shipment_date" },
  { key: "route", label: "Route", sort: "route" },
  { key: "pending_count", label: "Packing progress", sort: "outstanding", render: row => <span className={styles.progress}><strong>{Number(row.pending_count) ? t(`${row.pending_count} pending`) : t("All complete")}</strong><small>{row.completed_count} {t("of ")}{row.order_count} {t("complete")}</small></span> },
  { key: "created_at", label: "Uploaded", sort: "created_at", render: row => row.created_at ? new Date(String(row.created_at)).toLocaleString(displayLocale(), { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—" },
];

export default function PackingRegister() {
  useLanguage();
  return <Suspense fallback={<TableLoadingSkeleton columns={fileColumns.map(column => column.label)} />}><PackingFiles /></Suspense>;
}

function PackingFiles() {
  useLanguage();
  const params = useSearchParams();
  const rawFileId = params.get("raw_file_id");
  const batchId = params.get("batch_id");
  const showOrders = Boolean(rawFileId) || params.get("display") === "orders";
  const [file, setFile] = useState<RegisterRow>();
  const [fileError, setFileError] = useState<{ id: string; message: string }>();
  const [fileRetry, setFileRetry] = useState(0);
  const currentFile = file?.raw_file_id === rawFileId ? file : undefined;

  useEffect(() => {
    if (!rawFileId) return;
    let active = true;
    const scope = new URLSearchParams({ raw_file_id: rawFileId, view: "all", limit: "1" });
    if (batchId) scope.set("batch_id", batchId);
    api<RecordPage<RegisterRow>>(`/packing/files?${scope}`).then(result => {
      if (active) { setFile(result.items[0]); setFileError(undefined); }
    }).catch(reason => { if (active) { setFile(undefined); setFileError({ id: rawFileId, message: reason instanceof Error ? reason.message : "Unable to load the order filename" }); } });
    return () => { active = false; };
  }, [rawFileId, batchId, fileRetry]);

  function fileLink(row: RegisterRow) {
    const scope = new URLSearchParams({ raw_file_id: String(row.raw_file_id), view: "all",
      returnTo: window.location.pathname + window.location.search });
    if (batchId) scope.set("batch_id", batchId);
    return `/packing/orders?${scope}`;
  }

  const alternate = new URLSearchParams(params.toString());
  for (const key of ["raw_file_id", "returnTo", "offset", "sort", "direction", "status"]) alternate.delete(key);
  if (showOrders) alternate.delete("display"); else alternate.set("display", "orders");

  return <div className={`page ${styles.workspace}`}>
    {rawFileId && <div><RegisterBackLink fallback="/packing/orders" label={t("Back to order files")} /></div>}
    {fileError?.id === rawFileId && <p role="alert">{t(fileError.message)} <button type="button" onClick={() => setFileRetry(value => value + 1)}>{t("Retry file details")}</button></p>}
    <RecordRegister key={showOrders ? "orders" : "files"} mobileCards title={rawFileId ? t("Suborders to pack") : t("Packing orders")}
      description={rawFileId ? t(`${currentFile ? t(`${currentFile.order_count} ${Number(currentFile.order_count) === 1 ? t("suborder") : t("suborders")} from this file. `) : ""}Choose a subcustomer to start or resume packing.`)
        : showOrders ? t("All suborders, with their original uploaded file.") : t("Choose an uploaded order file to see its subcustomers and packing work.")}
      emptyMessage={t("Nothing to pack right now. Order files appear here once purchases are sent to packing.")}
      endpoint={showOrders ? "/packing/orders" : "/packing/files"} identity={showOrders ? "order_id" : "raw_file_id"}
      scopeLabel={rawFileId ? String(currentFile?.file_name || "Selected order file") : undefined}
      statuses={orderStatuses} searchPlaceholder={showOrders ? t("Subcustomer or uploaded filename") : t("Excel filename or subcustomer")}
      toolbar={<div className={styles.actions}><Link className="invoice-secondary-button" href={`/packing/orders?${alternate}`}>{showOrders ? t("Group by order file") : t("View all suborders")}</Link><Link className="invoice-secondary-button" href="/packing/setup">{t("Manage products and boxes")}</Link></div>}
      actionLabel={row => showOrders ? rowAction("packing", row) : "View suborders"}
      defaultSort="shipment_date" defaultDirection="asc"
      columns={showOrders ? [...orderColumns.filter(column => !rawFileId || column.key !== "file_name"),
        { key: "boxes", label: "Boxes", sort: "boxes" }, { key: "outstanding", label: "Boxes to finalize", sort: "outstanding" }] : fileColumns}
      open={row => showOrders ? recordLink("/packing", "orderId", row.order_id) : fileLink(row)} />
  </div>;
}
