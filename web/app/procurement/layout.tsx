"use client";

import { t, useLanguage } from "@/lib/language";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

export default function PurchasingLayout({ children }: { children: ReactNode }) {
  useLanguage();
  const master = usePathname() === "/procurement/master-purchase";
  return <div className="page">
    <nav className="orders-section-tabs" aria-label={t("Purchasing sections")}>
      <Link href="/procurement/batches" aria-current={!master ? "page" : undefined}>{t("Purchases")}</Link>
      <Link href="/procurement/master-purchase" aria-current={master ? "page" : undefined}>{t("Master Purchase")}</Link>
    </nav>
    {children}
  </div>;
}
