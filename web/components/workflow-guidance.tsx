"use client";

import { t, useLanguage } from "@/lib/language";

import Link from "next/link";
import type { ReactNode } from "react";
import { useSession } from "@/lib/session-context";
import { workAreas, type WorkStage } from "@/lib/workflow";
import { guideTo } from "@/lib/guide";
import { Check } from "lucide-react";

export type TaskAction = { label: string; disabled?: boolean } & ({ href: string; run?: never } | { run: () => void; href?: never });
export type TaskState = {
  title: string; detail: string; reference?: string; nextTeam?: string;
  progress?: string; blockers?: { text: string; href?: string }[];
  primary?: TaskAction; secondary?: ReactNode;
};

/** `done` marks the stages before `current` complete — pass it once a record is open. */
export function WorkflowProgress({ current, links = {}, done = false }: { current: WorkStage; links?: Partial<Record<WorkStage, string>>; done?: boolean }) {
  useLanguage();
  const session = useSession();
  const currentIndex = workAreas.findIndex(area => area.key === current);
  return <nav className="workflow-progress" aria-label={t("Shipment workflow")}><ol>
    {workAreas.map((area, index) => <li key={area.key} aria-current={current === area.key ? "step" : undefined} className={done && index < currentIndex ? "is-done" : undefined}>
      <span className="workflow-step-number" aria-hidden="true">{done && index < currentIndex ? <Check size={14} strokeWidth={3} /> : index + 1}</span>
      {area.key !== current && links[area.key] && session?.permissions.includes(area.permission)
        ? <Link href={links[area.key]!}>{t(area.label)}</Link>
        : <span>{t(area.label)}</span>}
      <small>{current === area.key ? t("Current workspace") : done && index < currentIndex ? t("Done") : t(area.team)}</small>
    </li>)}
  </ol></nav>;
}

export function TaskPanel({ title, detail, reference, nextTeam, progress, blockers = [], primary, secondary }: TaskState) {
  useLanguage();
  return <section className="task-guidance" aria-label={t("Current task")}>
    <div className="task-guidance-copy">
      {reference && <p className="task-reference">{reference}</p>}
      <h2>{t(title)}</h2><p className="task-instruction">{t(detail)}</p>
      {progress && <p className="task-progress-text">{t(progress)}</p>}
      {blockers.length > 0 && <ul className="task-blockers">{blockers.map(blocker => <li key={blocker.text}>{blocker.href ? <a href={blocker.href}>{t(blocker.text)}</a> : t(blocker.text)}</li>)}</ul>}
      {nextTeam && <p className="task-next-team">{t("Next team: ")}<strong>{t(nextTeam)}</strong></p>}
    </div>
    {(primary || secondary) && <div className="task-guidance-actions">
      {primary && (primary.href && !primary.disabled
        ? primary.href.startsWith("#")
          ? <a className="primary-button" data-guide={t(primary.label)} data-guide-detail={t("Review the task below, then continue with this highlighted action.")} href={primary.href} onClick={event => { if (guideTo(primary.href!)) event.preventDefault(); }}>{t(primary.label)}</a>
          : <Link className="primary-button" data-guide={t(primary.label)} data-guide-detail={t("Review the task below, then continue with this highlighted action.")} href={primary.href}>{t(primary.label)}</Link>
        : <button type="button" className="primary-button" data-guide={t(primary.label)} data-guide-detail={t("Review the task below, then continue with this highlighted action.")} disabled={primary.disabled} onClick={primary.run}>{t(primary.label)}</button>)}
      {secondary}
    </div>}
  </section>;
}
