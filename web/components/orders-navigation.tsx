"use client";

import Link from "next/link";
import { useSession } from "@/lib/session-context";

export function OrdersNavigation({ current }: { current: "files" | "attention" }) {
  const session = useSession();
  return <nav className="orders-section-tabs" aria-label="Order sections">
    {session?.permissions.includes("validate_orders") && <Link href="/orders/files" aria-current={current === "files" ? "page" : undefined}>Order files</Link>}
    {session?.permissions.includes("resolve_orders") && <Link href="/orders/attention" aria-current={current === "attention" ? "page" : undefined}>Needs attention</Link>}
  </nav>;
}
