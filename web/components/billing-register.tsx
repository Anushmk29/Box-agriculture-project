"use client";

import { t, useLanguage } from "@/lib/language";

import { RecordRegister, recordLink, type RegisterRow } from "@/components/record-register";
import { orderColumns, orderStatuses } from "@/lib/register-columns";
import { rowAction } from "@/lib/workflow";
import { HintedButton } from "@/components/disabled-hint";

export function BillingRegister({ invoices, busy, refreshKey, create, approve }: {
  invoices: boolean; busy: boolean; refreshKey: number;
  create: (ids: string[], master: boolean) => void; approve: (rows: RegisterRow[]) => void;
}) {
  useLanguage();
  return <RecordRegister key={invoices ? "invoices" : "orders"} title={invoices ? t("Invoice register") : t("Orders to invoice")}
    description={invoices ? t("Review drafts and find final invoices. Your team manages everything after invoicing externally.") : t("Select completed orders to create separate drafts or one master invoice.")}
    endpoint={invoices ? "/invoicing/records" : "/invoicing/orders"} identity={invoices ? "invoice_id" : "order_id"} refreshKey={refreshKey}
    views={false} actionLabel={row => rowAction("billing", row)} defaultSort="shipment_date" defaultDirection="asc"
    emptyMessage={invoices ? undefined : t("Nothing to invoice yet — orders appear here once packing is sent to accounting.")}
    statuses={invoices ? ["INVOICE_DRAFT", "INVOICE_SENT", "CANCELLED"] : orderStatuses.slice(4)}
    columns={invoices ? [
      { key: "invoice_number", label: "Invoice", sort: "reference" }, { key: "customer", label: "Customer", sort: "customer" },
      { key: "file_name", label: "Order files" }, { key: "shipment_date", label: "Shipment", sort: "shipment_date" },
      { key: "route", label: "Route", sort: "route" }, { key: "status", label: "Status", sort: "status" },
      { key: "amount", label: "Total", sort: "amount", render: row => `${row.currency} ${Number(row.amount).toFixed(2)}` },
    ] : orderColumns}
    open={row => recordLink("/billing", invoices ? "invoiceId" : "orderId", row[invoices ? "invoice_id" : "order_id"])}
    selectable={row => !busy && (invoices ? row.status === "INVOICE_DRAFT" : row.can_invoice === true)}
    selectionAction={rows => invoices ? <HintedButton data-guide={t("Approve the selected reviewed drafts")} data-guide-detail={t("Only approve invoices after checking their quantities and selling prices.")} data-guide-priority="10" disabled={busy || !rows.length || rows.length > 500} reason={rows.length > 500 ? t("Select 500 or fewer drafts") : t("Tick the drafts you have reviewed")} onClick={() => approve(rows)}>{t("Approve ")}{rows.length} {t("reviewed drafts")}</HintedButton> : <>
      <HintedButton data-guide={t("Create drafts for the selected orders")} data-guide-priority="10" disabled={busy || !rows.length || rows.length > 500} reason={rows.length > 500 ? t("Select 500 or fewer orders") : t("Tick orders to invoice them")} onClick={() => create(rows.map(row => String(row.order_id)), false)}>{t("Create ")}{rows.length} {t("separate drafts")}</HintedButton>
      <HintedButton className="invoice-secondary-button" disabled={busy || rows.length < 2 || rows.length > 500} reason={t("Select 2 or more orders to combine into one invoice")} onClick={() => create(rows.map(row => String(row.order_id)), true)}>{t("Create master draft")}</HintedButton>
    </>} />;
}
