"use client";

import { t, useLanguage } from "@/lib/language";

import { RecordRegister } from "@/components/record-register";
import { MasterPurchase } from "@/components/master-purchase";
import { purchaseColumns } from "@/lib/register-columns";

export default function MasterPurchasePage() {
  useLanguage();
  return <RecordRegister mobileCards title={t("Master Purchase")}
    description={t("Select purchases by uploaded filename, preview each customer's section, then download one PDF to take to market. Original purchases stay separate.")}
    emptyMessage={t("No purchases available. Confirm an order file to create its purchase list.")}
    endpoint="/procurement/batches" identity="batch_id" columns={purchaseColumns}
    defaultView="all" defaultSort="shipment_date" defaultDirection="asc"
    statuses={["PURCHASING_CONSOLIDATED", "PROCUREMENT_CONFIRMED", "HANDOFF_01_SENT"]}
    selectable={row => row.status !== "CANCELLED"}
    selectionAction={rows => <MasterPurchase batchIds={rows.map(row => String(row.batch_id))} />} />;
}
