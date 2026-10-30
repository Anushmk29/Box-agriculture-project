"use client";
import { useEffect } from "react";
import { confirmAction } from "@/lib/confirm";

const leaveMessage = "You have unsaved changes. Leave this record and discard them?";

/** Protect workflow drafts when leaving through links, browser navigation or reload. */
export function useUnsavedChanges(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    // Reload/close and Back/Forward must be answered synchronously, so those keep the browser's own prompt.
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    let approvedLink: HTMLAnchorElement | null = null;
    const click = (event: MouseEvent) => {
      const link = (event.target as Element)?.closest("a[href]") as HTMLAnchorElement | null;
      if (!link || link.target === "_blank" || link.hasAttribute("download") || link.href === window.location.href) return;
      const target = new URL(link.href);
      if (target.origin === window.location.origin && target.pathname === window.location.pathname && target.search === window.location.search) return;
      if (approvedLink === link) { approvedLink = null; return; }
      // In-app links ask in the styled dialog, then replay the same click so Next handles the navigation.
      event.preventDefault(); event.stopImmediatePropagation();
      void confirmAction({ title: "Leave this record?", body: leaveMessage, confirmLabel: "Leave and discard", tone: "danger" }).then(leave => {
        if (!leave || !link.isConnected) return;
        approvedLink = link;
        link.click();
      });
    };
    // The Navigation API can cancel browser Back/Forward before Next unmounts the editor.
    const navigation = (window as unknown as { navigation?: EventTarget }).navigation;
    const navigate = (event: Event) => {
      if ((event as Event & { navigationType?: string }).navigationType === "traverse" && event.cancelable && !window.confirm(leaveMessage)) event.preventDefault();
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", click, true);
    navigation?.addEventListener("navigate", navigate);
    return () => { window.removeEventListener("beforeunload", unload); document.removeEventListener("click", click, true); navigation?.removeEventListener("navigate", navigate); };
  }, [dirty]);
}
