"use client";
import Link from "next/link";
import { RecordRegister, recordLink } from "@/components/record-register";
import { rowAction, scopedPacking } from "@/lib/workflow";

export default function BatchRegister() {
  return <div className="page"><RecordRegister mobileCards title="Purchase batches" emptyMessage="No batches waiting for purchases. Batches appear here once order files are confirmed." description="Find a batch, review its progress and open the purchasing workbench." endpoint="/procurement/batches" identity="batch_id"
    toolbar={<Link className="invoice-secondary-button" href="/procurement/combine">Combine order files</Link>}
    actionLabel={row => rowAction("procurement", row)} defaultSort="shipment_date" defaultDirection="asc"
    statuses={["PURCHASING_CONSOLIDATED", "PROCUREMENT_CONFIRMED", "HANDOFF_01_SENT"]}
    open={row => row.status === "HANDOFF_01_SENT" ? scopedPacking(String(row.batch_id)) : recordLink("/procurement", "batchId", row.batch_id)} columns={[
      { key: "display_name", label: "Batch name", sort: "name" }, { key: "batch_reference", label: "Reference", sort: "reference" },
      { key: "customers", label: "Clients", sort: "customer", render: row => { const clients = row.customers as string[]; return clients.slice(0, 2).join(", ") + (clients.length > 2 ? ` +${clients.length - 2}` : ""); } },
      { key: "shipment_date", label: "Shipment", sort: "shipment_date" }, { key: "route_code", label: "Route", sort: "route" },
      { key: "status", label: "Status", sort: "status" }, { key: "total_skus", label: "Products", sort: "products" },
      { key: "outstanding_items", label: "Awaiting confirmation", sort: "outstanding" },
    ]} /></div>;
}
