"use client";
import Link from "next/link";
import { RecordRegister, recordLink } from "@/components/record-register";
import { orderColumns, orderStatuses } from "@/lib/register-columns";
import { rowAction } from "@/lib/workflow";

export default function PackingRegister() {
  return <div className="page"><RecordRegister mobileCards title="Packing orders" emptyMessage="Nothing to pack right now. Orders appear here once purchases are sent to packing." description="Find customer orders and review packing progress." endpoint="/packing/orders" identity="order_id" statuses={orderStatuses}
    toolbar={<Link className="invoice-secondary-button" href="/packing/setup">Manage products and boxes</Link>}
    actionLabel={row => rowAction("packing", row)} defaultSort="shipment_date" defaultDirection="asc"
    columns={[...orderColumns, { key: "boxes", label: "Boxes", sort: "boxes" }, { key: "outstanding", label: "Boxes to finalize", sort: "outstanding" }]}
    open={row => recordLink("/packing", "orderId", row.order_id)} /></div>;
}
