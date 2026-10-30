"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { usePathname } from "next/navigation";
import { ArrowRight, Pause, Play } from "lucide-react";
import { nextGuidance } from "@/lib/workspace-guidance";
import { useGuidanceHints } from "@/lib/guidance-hints";
import { guideElement } from "@/lib/guide";

export function WorkspaceGuide({ scopeRef, inDialog = false }: { scopeRef?: RefObject<HTMLElement | null>; inDialog?: boolean }) {
  const pathname = usePathname();
  const { enabled, toggle } = useGuidanceHints();
  const [step, setStep] = useState<{ title: string; detail: string }>();
  const [host, setHost] = useState<HTMLElement | null>(null);
  const target = useRef<HTMLElement | null>(null);
  const dialog = inDialog;

  useEffect(() => {
    const scope = scopeRef?.current || document.getElementById("workspace-content");
    if (!scope) return;
    let frame = 0;
    let intersection: IntersectionObserver | undefined;
    function clear() {
      intersection?.disconnect();
      target.current?.removeAttribute("data-guidance-current");
      target.current?.removeAttribute("data-guidance-visible");
      target.current?.removeAttribute("data-guidance-pulse");
      target.current = null;
    }
    function scan() {
      frame = 0;
      setHost(dialog ? null : scope!.querySelector<HTMLElement>("[data-workspace-guide-host]"));
      const openDialogs = document.querySelectorAll("dialog[open]");
      const topDialog = openDialogs.item(openDialogs.length - 1);
      const orders = dialog ? null : scope!.querySelector(".orders-workspace");
      const existing = orders?.querySelector<HTMLElement>("[data-next-action='true']");
      const blocked = dialog ? Boolean(topDialog && topDialog !== scope) : Boolean(topDialog);
      const next = blocked ? undefined : existing ? { element: existing, title: "", detail: "" } : orders ? undefined : nextGuidance(scope!, dialog);
      if (target.current !== next?.element) {
        clear();
        if (next) {
          target.current = next.element;
          next.element.setAttribute("data-guidance-current", "true");
          intersection = new IntersectionObserver(entries => {
            for (const entry of entries) entry.target.setAttribute("data-guidance-visible", String(entry.isIntersecting));
          });
          intersection.observe(next.element);
        }
      }
      target.current?.setAttribute("data-guidance-pulse", String(enabled && !document.hidden));
      const visibleStep = orders ? undefined : next;
      setStep(previous => previous?.title === visibleStep?.title && previous?.detail === visibleStep?.detail ? previous : visibleStep ? { title: visibleStep.title, detail: visibleStep.detail } : undefined);
    }
    function schedule() { if (!frame) frame = requestAnimationFrame(scan); }
    const observer = new MutationObserver(schedule);
    observer.observe(scope, { subtree: true, childList: true, attributes: true,
      attributeFilter: ["data-guide", "data-guide-detail", "data-guide-priority", "data-next-action", "disabled", "aria-disabled", "aria-busy", "hidden", "open", "value", "required", "readonly"] });
    scope.addEventListener("input", schedule); scope.addEventListener("change", schedule);
    document.addEventListener("toggle", schedule, true);
    document.addEventListener("visibilitychange", schedule);
    schedule();
    return () => {
      observer.disconnect(); cancelAnimationFrame(frame); clear();
      scope.removeEventListener("input", schedule); scope.removeEventListener("change", schedule);
      document.removeEventListener("toggle", schedule, true);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, [pathname, scopeRef, dialog, enabled]);

  if (!step) return null;
  const guide = <section className={`workspace-guide${dialog ? " workspace-guide-dialog" : ""}${host ? " workspace-guide-inline" : ""}`} aria-label="Next step guidance" data-guide-ui>
    <div className={host ? "sr-only" : "workspace-guide-copy"}><span>Next step</span><p aria-live="polite" aria-atomic="true"><strong>{step.title}</strong><small>{step.detail}</small></p></div>
    <div className="workspace-guide-actions"><button type="button" className="invoice-secondary-button" aria-label="Show next action" title={host ? step.title : undefined} onClick={() => { if (target.current) guideElement(target.current); }}>{!host && "Show next action"}<ArrowRight size={16} aria-hidden /></button>
      <button type="button" className="guidance-pause" aria-label={enabled ? "Pause hints" : "Resume hints"} title={enabled ? "Pause hints" : "Resume hints"} aria-pressed={enabled} onClick={toggle}>{enabled ? <Pause size={14} aria-hidden /> : <Play size={14} aria-hidden />}{!host && (enabled ? "Pause hints" : "Resume hints")}</button></div>
  </section>;
  return host ? createPortal(guide, host) : guide;
}
