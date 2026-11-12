"use client";

import { t, useLanguage } from "@/lib/language";

import { RecordRegister, recordLink } from "@/components/record-register";
import { rowAction, scopedPacking } from "@/lib/workflow";
import { purchaseColumns } from "@/lib/register-columns";

export default function BatchRegister() {
  useLanguage();
  return <RecordRegister mobileCards title={t("Purchase batches")} emptyMessage={t("No batches waiting for purchases. Batches appear here once order files are confirmed.")} description={t("Open a purchase to review quantities, record actual purchases, and send it to packing. Use the Master Purchase tab for one market-shopping PDF.")} endpoint="/procurement/batches" identity="batch_id"
    actionLabel={row => rowAction("procurement", row)} defaultSort="shipment_date" defaultDirection="asc"
    statuses={["PURCHASING_CONSOLIDATED", "PROCUREMENT_CONFIRMED", "HANDOFF_01_SENT"]}
    open={row => row.status === "HANDOFF_01_SENT" ? scopedPacking(String(row.batch_id)) : recordLink("/procurement", "batchId", row.batch_id)} columns={purchaseColumns} />;
}
