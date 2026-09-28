"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Boxes, ClipboardCheck, LayoutDashboard, LogOut, PackageOpen, ReceiptText, Users } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { useEffect, useState } from "react";
import { StatusMessage } from "@/components/status-message";

type SessionUser = { full_name: string; role: string; must_change_password: boolean };

type OperationsBadgeSummary = {
  active_orders?: number;
  awaiting_procurement?: number;
  boxes_missing_weights?: number;
  awaiting_billing?: number;
};

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<SessionUser | null>(null);
  const [sessionError, setSessionError] = useState("");
  const [badges, setBadges] = useState<OperationsBadgeSummary | null>(null);

  useEffect(() => {
    if (pathname === "/login") return;
    api<SessionUser>("/auth/me")
      .then(user => {
        setSession(user);
        setSessionError("");
        if (user.must_change_password && pathname !== "/change-password") {
          router.replace("/change-password");
          return;
        }
        if (!user.must_change_password && pathname === "/change-password") {
          router.replace("/");
          return;
        }
        if (pathname === "/billing" && user.role === "GENERAL") {
          router.replace("/");
          return;
        }
        if (pathname === "/users" && user.role !== "OWNER_ADMIN") {
          router.replace("/");
          return;
        }
        api<OperationsBadgeSummary>("/operations/summary")
          .then(setBadges)
          .catch(() => {
            // Badges are optional; the nav still works without them.
          });
      })
      .catch(reason => {
        setSession(null);
        setSessionError(reason instanceof Error ? reason.message : "Unable to verify your session");
      });
  }, [pathname, router]);

  if (pathname === "/login") return children;

  async function signOut() {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  const userInitials = session?.full_name
    ? session.full_name
        .split(" ")
        .map(part => part[0])
        .filter(Boolean)
        .slice(0, 2)
        .join("")
        .toUpperCase()
    : "";

  const today = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long" }).format(new Date());

  const navItems = [
    { href: "/", label: "Operations", icon: LayoutDashboard },
    { href: "/orders", label: "Order intake", icon: PackageOpen, count: badges?.active_orders, hint: "active orders" },
    { href: "/procurement", label: "Procurement", icon: ClipboardCheck, count: badges?.awaiting_procurement, hint: "awaiting procurement" },
    { href: "/packing", label: "Packing floor", icon: Boxes, count: badges?.boxes_missing_weights, hint: "boxes to weigh", attention: true },
    ...(session && session.role !== "GENERAL"
      ? [{ href: "/billing", label: "Billing", icon: ReceiptText, count: badges?.awaiting_billing, hint: "awaiting billing" }]
      : []),
    ...(session?.role === "OWNER_ADMIN"
      ? [{ href: "/users", label: "Users", icon: Users }]
      : []),
  ];

  return (
    <div className="app-frame">
      <aside className="sidebar" aria-label="Primary sidebar">
        <Link className="brand" href="/">
          <span className="brand-mark">
            <img src="/logo.png" alt="" />
          </span>
          <span>
            Agriculture is life
            <small>Packing &amp; export operations</small>
          </span>
        </Link>

        <nav aria-label="Primary navigation">
          {navItems.map(({ href, label, icon: Icon, count, hint, attention }) => (
            <Link
              className={pathname === href ? "nav-link active" : "nav-link"}
              href={href}
              key={href}
              aria-current={pathname === href ? "page" : undefined}
            >
              <span className="nav-link-left">
                <Icon size={18} aria-hidden />
                <span>{label}</span>
              </span>
              {count ? (
                <span className={attention ? "nav-badge attention" : "nav-badge"} title={`${count} ${hint}`}>
                  {count}
                </span>
              ) : null}
            </Link>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span className="sidebar-date">{today}</span>
          {session && (
            <div className="user-card-sidebar">
              <div className="user-avatar-badge" aria-hidden>{userInitials}</div>
              <div className="user-info-text">
                <strong>{session.full_name}</strong>
                <small>{session.role.replaceAll("_", " ").toLowerCase()}</small>
              </div>
              <button className="signout-icon-btn" type="button" onClick={signOut} title="Sign out" aria-label="Sign out">
                <LogOut size={16} />
              </button>
            </div>
          )}
        </div>
      </aside>

      <div className="main-content">
        <StatusMessage error={sessionError} />
        {children}
      </div>
    </div>
  );
}
