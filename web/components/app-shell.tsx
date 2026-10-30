"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Boxes, CalendarDays, ChevronRight, ClipboardCheck, LayoutDashboard, LogOut, Menu, PackageOpen, PackageSearch, ReceiptText, ScanLine, Users } from "lucide-react";
import { Fragment, type MouseEvent, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useEffect, useState } from "react";
import { StatusMessage } from "@/components/status-message";
import { routePermission, safeDestination } from "@/lib/workflow";
import { SessionContext, type SessionUser } from "@/lib/session-context";
import { TaskDialog } from "@/components/task-dialog";
import { useCompactLayout } from "@/lib/use-compact-layout";
import { WorkspaceGuide } from "@/components/workspace-guide";

type OperationsBadgeSummary = {
  awaiting_intake?: number;
  awaiting_procurement?: number;
  awaiting_packing?: number;
  awaiting_billing?: number;
};

export function AppShell({ children }: { children: ReactNode }) {
  const compact = useCompactLayout();
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<SessionUser | null>(null);
  const [sessionError, setSessionError] = useState("");
  const [verifiedPath, setVerifiedPath] = useState<string | null>(null);
  const [badges, setBadges] = useState<OperationsBadgeSummary | null>(null);

  useEffect(() => {
    if (pathname === "/login") return;
    let cancelled = false;
    const refreshSession = () => api<SessionUser>("/auth/me", {}, { background: true })
      .then(user => {
        if (cancelled) return;
        setSession(user);
        setSessionError("");
        if (user.must_change_password && pathname !== "/change-password") {
          router.replace(`/change-password?next=${encodeURIComponent(safeDestination(window.location.pathname + window.location.search + window.location.hash))}`);
          return;
        }
        if (!user.must_change_password && pathname === "/change-password") {
          router.replace(safeDestination(new URLSearchParams(window.location.search).get("next")));
          return;
        }
        setVerifiedPath(pathname);
        if (!user.must_change_password) {
          api<OperationsBadgeSummary>("/operations/summary", {}, { background: true })
            .then(summary => { if (!cancelled) setBadges(summary); })
            .catch(() => {});
        }
      })
      .catch(reason => {
        if (cancelled) return;
        setSession(null);
        setVerifiedPath(null);
        setSessionError(reason instanceof Error ? reason.message : "Unable to verify your session");
      });
    refreshSession();
    const badgeTimer = window.setInterval(refreshSession, 20000);
    window.addEventListener("focus", refreshSession);
    window.addEventListener("account-access-updated", refreshSession);
    return () => {
      cancelled = true;
      window.clearInterval(badgeTimer);
      window.removeEventListener("focus", refreshSession);
      window.removeEventListener("account-access-updated", refreshSession);
    };
  }, [pathname, router]);

  if (pathname === "/login") return children;

  async function signOut() {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      setSessionError("Sign out on all devices could not be confirmed. Please try again.");
      return;
    }
    router.replace("/login");
    router.refresh();
  }

  function browseOrderFiles(event: MouseEvent<HTMLAnchorElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const returnTo = new URLSearchParams(window.location.search).get("returnTo");
    const destination = safeDestination(returnTo);
    if (pathname === "/orders" && returnTo && (destination === "/orders/files" || destination.startsWith("/orders/files?"))) {
      event.preventDefault();
      router.push(destination);
    }
    setMenuOpen(false);
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
  const hasPermission = (permission: string) => session?.permissions.includes(permission) ?? false;
  const permission = routePermission(pathname);
  // Once a session is known, render the next page immediately; the background
  // re-check still redirects if the account changed. The API enforces access.
  const sessionReady = Boolean(session && (verifiedPath === pathname || !session.must_change_password));
  const canRenderPage = sessionReady && session &&
    (session.must_change_password ? pathname === "/change-password" : pathname !== "/change-password") &&
    (!permission || hasPermission(permission));

  const isActive = (href: string) => {
    if (href === "/packing/setup") return pathname === "/packing/setup";
    if (href === "/packing/orders") return pathname === "/packing" || (pathname.startsWith("/packing/") && !pathname.startsWith("/packing/setup"));
    return pathname === href || (href !== "/" && pathname.startsWith(`/${href.split("/")[1]}`));
  };
  const navItems = [
    { href: "/", label: "Today", icon: LayoutDashboard },
    ...(hasPermission("validate_orders") || hasPermission("resolve_orders") ? [{ href: hasPermission("validate_orders") ? "/orders" : "/orders/attention", label: "Orders", icon: PackageOpen, count: hasPermission("validate_orders") ? badges?.awaiting_intake : undefined, hint: "files awaiting review" }] : []),
    ...(hasPermission("confirm_procurement") ? [{ href: "/procurement/batches", label: "Purchasing", icon: ClipboardCheck, count: badges?.awaiting_procurement, hint: "awaiting procurement" }] : []),
    ...(hasPermission("floor_packing") ? [
      { href: "/packing/orders", label: "Packing", icon: Boxes, count: badges?.awaiting_packing, hint: "orders awaiting packing" },
      { href: "/scan", label: "Scan box", icon: ScanLine },
      { href: "/packing/setup", label: "Products & boxes", icon: PackageSearch },
    ] : []),
    ...(hasPermission("manage_billing")
      ? [{ href: "/billing", label: "Billing", icon: ReceiptText, count: badges?.awaiting_billing, hint: "billing actions" }]
      : []),
    ...(hasPermission("manage_users")
      ? [{ href: "/users", label: "Team & access", icon: Users }]
      : []),
  ];
  const navGroups = [
    { label: "Overview", items: navItems.filter(item => item.href === "/") },
    { label: "Operations", items: navItems.filter(item => !["/", "/packing/setup", "/users"].includes(item.href)) },
    { label: "Catalogue", items: navItems.filter(item => item.href === "/packing/setup") },
    { label: "Administration", items: navItems.filter(item => item.href === "/users") },
  ].filter(group => group.items.length > 0);
  const workspaceName = navItems.find(item => item.href !== "/" && isActive(item.href))?.label || "Today";

  return (
    <div className="app-frame">
      <a className="workspace-skip-link" href="#workspace-content">Skip to main content</a>
      <header className="mobile-workspace-header">
        <button type="button" className="invoice-secondary-button" aria-haspopup="dialog" aria-expanded={menuOpen && compact} onClick={() => setMenuOpen(true)}><Menu size={18} aria-hidden />Menu</button>
        <strong>{workspaceName}</strong>
        {hasPermission("floor_packing") && <Link href="/scan" className="mobile-scan-shortcut"><ScanLine size={18} aria-hidden />Scan box</Link>}
      </header>
      {compact && menuOpen && <TaskDialog title="Workspaces" description="Choose your next task or return to Today." onClose={() => setMenuOpen(false)}>
        <nav className="mobile-workspace-menu" aria-label="Primary navigation">
          {navGroups.map(group => <div className="mobile-nav-group" role="group" aria-label={group.label} key={group.label}>
            <p className="mobile-nav-group-label">{group.label}</p>
            {group.items.map(({ href, label, icon: Icon, count, hint }) => <Fragment key={href}><Link href={href}
            aria-current={(label === "Orders" ? pathname === href : isActive(href)) ? "page" : undefined}
            onClick={event => { if (!event.defaultPrevented) setMenuOpen(false); }}>
            <Icon size={20} aria-hidden /><span>{label}</span>{Boolean(count) && <span aria-label={`${count} ${hint}`}>{count}</span>}
          </Link>{label === "Orders" && hasPermission("validate_orders") && <div className="mobile-nav-children"><Link href="/orders/files" aria-current={pathname === "/orders/files" ? "page" : undefined} onClick={browseOrderFiles}>Browse order files</Link></div>}</Fragment>)}
          </div>)}
        </nav>
        <div className="mobile-account"><p><strong>{session?.full_name}</strong><br />{session?.role.replaceAll("_", " ").toLowerCase()}</p><button type="button" className="invoice-secondary-button" onClick={signOut}><LogOut size={18} aria-hidden />Sign out everywhere</button></div>
      </TaskDialog>}
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

        <nav className="primary-navigation" aria-label="Primary navigation">
          {navGroups.map(group => <div className="sidebar-nav-group" role="group" aria-label={group.label} key={group.label}>
            <h2 className="sidebar-section-label">{group.label}</h2>
            {group.items.map(({ href, label, icon: Icon, count, hint }) => {
            const active = isActive(href);
            return (
            <Fragment key={href}><Link
              className={active ? "nav-link active" : "nav-link"}
              href={href}
              aria-current={(label === "Orders" ? pathname === href : active) ? "page" : undefined}
            >
              <span className="nav-link-left">
                <Icon size={18} aria-hidden />
                <span>{label}</span>
              </span>
              {count ? (
                <span key={count} className="nav-badge" title={`${count} ${hint}`}>
                  {count}
                </span>
              ) : null}
            </Link>
            {label === "Orders" && hasPermission("validate_orders") && <div className="sidebar-nav-children"><Link href="/orders/files" aria-current={pathname === "/orders/files" ? "page" : undefined} onClick={browseOrderFiles}>Browse order files</Link></div>}
            </Fragment>
            );
          })}
          </div>)}
        </nav>

        <div className="sidebar-foot">
          <span className="sidebar-date" suppressHydrationWarning>{today}</span>
          {session && (
            <div className="user-card-sidebar">
              <div className="user-avatar-badge" aria-hidden>{userInitials}</div>
              <div className="user-info-text">
                <strong>{session.full_name}</strong>
                <small>{session.role.replaceAll("_", " ").toLowerCase()}</small>
              </div>
              <button className="signout-icon-btn" type="button" onClick={signOut} title="Sign out everywhere" aria-label="Sign out everywhere">
                <LogOut size={16} />
              </button>
            </div>
          )}
        </div>
      </aside>

      <main className="main-content" id="workspace-content" tabIndex={-1}>
        <div className="workspace-topbar">
          <div className="workspace-breadcrumb"><span>Operations</span><ChevronRight size={14} aria-hidden /><span>{workspaceName}</span></div>
          <span className="workspace-date" suppressHydrationWarning><CalendarDays size={15} aria-hidden />{today}</span>
        </div>
        <StatusMessage error={sessionError} />
        {canRenderPage ? <SessionContext.Provider value={session}><WorkspaceGuide />{children}</SessionContext.Provider>
          : sessionReady && session && !session.must_change_password && permission && !hasPermission(permission)
            ? <section className="page"><h1>Access unavailable</h1><p>Your access level does not include this workspace. Ask the owner if you need access for your work.</p><Link className="primary-button" href="/">Return to Today</Link></section>
            : !sessionError && <div className="page" role="status" aria-label="Checking account access"><span className="skeleton skeleton-title" /><span className="skeleton skeleton-line" /><span className="skeleton skeleton-block" /></div>}
      </main>
    </div>
  );
}
