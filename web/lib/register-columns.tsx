import type { RegisterColumn, RegisterRow } from "@/components/record-register";

// One customer cell: readable name first, code underneath only when it adds information.
const squash = (value: string) => value.replace(/[\s_-]/g, "").toLowerCase();
const customerCell = (row: RegisterRow) => {
  const code = String(row.customer_code ?? "");
  const name = String(row.customer_name || code || "—").replace(/^Sub-Customer\s+/, "").replaceAll("_", " ");
  return <span className="register-customer"><strong>{name}</strong>{code && squash(code) !== squash(name) ? <small>{code}</small> : null}</span>;
};

export const orderStatuses = ["HANDOFF_01_SENT", "PACKING_DESIGNED", "FLOOR_PACKING", "FINAL_PACK_LOCKED", "HANDOFF_02_RETURNED", "INVOICES_DRAFTED", "INVOICES_SENT", "CONFIRMED_PAID", "RECEIPTS_ISSUED", "ORDER_CLOSED"];
export const fileColumns: RegisterColumn[] = [
  { key: "file_name", label: "Order file", sort: "name" }, { key: "customer", label: "Client", sort: "customer" },
  { key: "shipment_date", label: "Shipment", sort: "shipment_date" }, { key: "route", label: "Route", sort: "route" },
  { key: "status", label: "Status", sort: "status" }, { key: "customer_count", label: "Customers" },
  { key: "unresolved_rows", label: "Unresolved rows" },
];
export const orderColumns: RegisterColumn[] = [
  { key: "customer_code", label: "Customer", sort: "customer", render: customerCell },
  { key: "order_ref_id", label: "Order reference", sort: "reference", render: row => <code className="register-ref">{String(row.order_ref_id ?? "—")}</code> }, { key: "shipment_date", label: "Shipment", sort: "shipment_date" },
  { key: "route", label: "Route", sort: "route" }, { key: "status", label: "Status", sort: "status" },
  { key: "created_at", label: "Created", sort: "created_at", render: row => {
    if (!row.created_at) return "—";
    const created = new Date(String(row.created_at));
    if (!Number.isFinite(created.getTime())) return "—";
    const today = new Date();
    const days = Math.floor((Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) - Date.UTC(created.getFullYear(), created.getMonth(), created.getDate())) / 86400000);
    return days <= 0 ? "Today" : days === 1 ? "Yesterday" : `${days} days ago`;
  } },
];
