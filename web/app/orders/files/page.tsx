"use client";

import { t, useLanguage } from "@/lib/language";

import Link from "next/link";
import { RecordRegister, recordLink } from "@/components/record-register";
import { fileColumns, orderStatuses } from "@/lib/register-columns";
import { rowAction } from "@/lib/workflow";
import { OrdersNavigation } from "@/components/orders-navigation";

export default function FileRegister() {
  useLanguage();
  return <div className="page"><OrdersNavigation current="files" /><RecordRegister mobileCards title={t("Order files")} emptyMessage={t("Nothing waiting for review. New order files appear here after upload.")} description={t("Review pending uploads or find a previous shipment.")} endpoint="/orders/files" identity="raw_file_id" columns={fileColumns}
    statuses={["UPLOADED", "PARSED", "SPLIT_AND_VALIDATED", "PURCHASING_CONSOLIDATED", ...orderStatuses]}
    toolbar={<Link className="primary-button" href="/orders">{t("Upload order")}</Link>}
    actionLabel={row => rowAction("orders", row)} defaultSort="shipment_date" defaultDirection="asc"
    open={row => recordLink("/orders", "fileId", row.raw_file_id)} /></div>;
}
