"use client";

import { t, useLanguage } from "@/lib/language";

import { useSyncExternalStore, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { getLoadingSnapshot, getServerLoadingSnapshot, subscribeLoading } from "@/lib/loading";
import { LoadingDialog } from "./loading-dialog";

const titles: Record<string, string> = {
  "/": "Loading operations",
  "/orders": "Loading order intake",
  "/procurement": "Loading procurement batch",
  "/packing": "Loading packing floor",
  "/billing": "Loading billing",
  "/users": "Loading users",
  "/login": "Signing in",
  "/change-password": "Updating your account",
};

export function LoadingProvider({ children }: { children: ReactNode }) {
  useLanguage();
  const pathname = usePathname();
  const loading = useSyncExternalStore(subscribeLoading, getLoadingSnapshot, getServerLoadingSnapshot);
  const title = pathname.startsWith("/scan/") ? "Loading box record" : titles[pathname] || "Loading";

  return <>
    <div style={{ display: "contents", visibility: loading ? "hidden" : undefined }} aria-busy={loading} inert={loading}>
      {children}
    </div>
    {loading && <LoadingDialog title={t(title)} />}
  </>;
}

