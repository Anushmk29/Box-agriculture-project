"use client";
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
  const session = useSession();
  const currentIndex = workAreas.findIndex(area => area.key === current);
  return <nav className="workflow-progress" aria-label="Shipment workflow"><ol>
    {workAreas.map((area, index) => <li key={area.key} aria-current={current === area.key ? "step" : undefined} className={done && index < currentIndex ? "is-done" : undefined}>
      <span className="workflow-step-number" aria-hidden="true">{done && index < currentIndex ? <Check size={14} strokeWidth={3} /> : index + 1}</span>
      {area.key !== current && links[area.key] && session?.permissions.includes(area.permission)
        ? <Link href={links[area.key]!}>{area.label}</Link>
        : <span>{area.label}</span>}
      <small>{current === area.key ? "Current workspace" : done && index < currentIndex ? "Done" : area.team}</small>
    </li>)}
  </ol></nav>;
}

export function TaskPanel({ title, detail, reference, nextTeam, progress, blockers = [], primary, secondary }: TaskState) {
  return <section className="task-guidance" aria-label="Current task">
    <div className="task-guidance-copy">
      {reference && <p className="task-reference">{reference}</p>}
      <h2>{title}</h2><p className="task-instruction">{detail}</p>
      {progress && <p className="task-progress-text">{progress}</p>}
      {blockers.length > 0 && <ul className="task-blockers">{blockers.map(blocker => <li key={blocker.text}>{blocker.href ? <a href={blocker.href}>{blocker.text}</a> : blocker.text}</li>)}</ul>}
      {nextTeam && <p className="task-next-team">Next team: <strong>{nextTeam}</strong></p>}
    </div>
    {(primary || secondary) && <div className="task-guidance-actions">
      {primary && (primary.href && !primary.disabled
        ? primary.href.startsWith("#")
          ? <a className="primary-button" data-guide={primary.label} data-guide-detail="Review the task below, then continue with this highlighted action." href={primary.href} onClick={event => { if (guideTo(primary.href!)) event.preventDefault(); }}>{primary.label}</a>
          : <Link className="primary-button" data-guide={primary.label} data-guide-detail="Review the task below, then continue with this highlighted action." href={primary.href}>{primary.label}</Link>
        : <button type="button" className="primary-button" data-guide={primary.label} data-guide-detail="Review the task below, then continue with this highlighted action." disabled={primary.disabled} onClick={primary.run}>{primary.label}</button>)}
      {secondary}
    </div>}
  </section>;
}
