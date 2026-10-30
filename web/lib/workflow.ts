import type { RegisterRow } from "@/components/record-register";

export const workAreas = [
  { key: "orders", label: "Orders", team: "Order intake", href: "/orders/files", permission: "validate_orders" },
  { key: "procurement", label: "Purchasing", team: "Buyers", href: "/procurement/batches", permission: "confirm_procurement" },
  { key: "packing", label: "Packing", team: "Packing team", href: "/packing/orders", permission: "floor_packing" },
  { key: "billing", label: "Billing", team: "Accounting", href: "/billing", permission: "manage_billing" },
] as const;
export type WorkStage = typeof workAreas[number]["key"];

export function routePermission(pathname: string) {
  if (pathname === "/orders/attention" || pathname.startsWith("/orders/attention/")) return "resolve_orders";
  if (pathname === "/exceptions" || pathname.startsWith("/exceptions/")) return "resolve_orders";
  if (pathname === "/users" || pathname.startsWith("/users/")) return "manage_users";
  if (pathname === "/scan" || pathname.startsWith("/scan/")) return "floor_packing";
  return workAreas.find(area => pathname === `/${area.key}` || pathname.startsWith(`/${area.key}/`))?.permission;
}

/** Only known app destinations; reject external URLs, controls and auth loops. */
export function safeDestination(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u0020]/.test(value)) return "/";
  try {
    const url = new URL(value, "https://app.invalid");
    const path = url.pathname;
    if (url.origin !== "https://app.invalid" || (path !== "/" && !routePermission(path))) return "/";
    return path + url.search + url.hash;
  } catch { return "/"; }
}

export function rowAction(stage: WorkStage, row: RegisterRow): string {
  const status = String(row.status || "");
  if (stage === "orders") {
    if (["UPLOADED", "PARSED"].includes(status)) return Number(row.unresolved_rows) > 0 ? `Resolve ${row.unresolved_rows} ${Number(row.unresolved_rows) === 1 ? "product" : "products"}` : "Review upload";
    return "View record";
  }
  if (stage === "procurement") return status === "HANDOFF_01_SENT" ? "View packing orders" : ["PURCHASING_CONSOLIDATED", "PROCUREMENT_CONFIRMED"].includes(status) ? "Confirm purchases" : "View record";
  if (stage === "packing") {
    if (status === "FINAL_PACK_LOCKED") return "Send to accounting";
    if (!["HANDOFF_01_SENT", "PACKING_DESIGNED", "FLOOR_PACKING"].includes(status)) return "View completed packing";
    if (!Number(row.boxes)) return "Start packing";
    return "Resume packing";
  }
  if (row.invoice_id) return status === "INVOICE_DRAFT" ? "Review invoice" : "View invoice";
  return row.can_invoice ? "Create invoice draft" : "View record";
}

export function scopedPacking(batchId: string) {
  return `/packing/orders?batch_id=${encodeURIComponent(batchId)}&view=all&sort=shipment_date&direction=asc`;
}
