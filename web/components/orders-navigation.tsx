"use client";

import { t, useLanguage } from "@/lib/language";

import Link from "next/link";
import { useSession } from "@/lib/session-context";

export function OrdersNavigation({ current }: { current: "files" | "attention" }) {
  useLanguage();
  const session = useSession();
  return <nav className="orders-section-tabs" aria-label={t("Order sections")}>
    {session?.permissions.includes("validate_orders") && <Link href="/orders/files" aria-current={current === "files" ? "page" : undefined}>{t("Order files")}</Link>}
    {session?.permissions.includes("resolve_orders") && <Link href="/orders/attention" aria-current={current === "attention" ? "page" : undefined}>{t("Needs attention")}</Link>}
  </nav>;
}
