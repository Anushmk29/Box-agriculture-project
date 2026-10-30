"use client";

import { useSyncExternalStore } from "react";
import { TaskDialog } from "@/components/task-dialog";
import { getConfirm, getServerConfirm, settleConfirm, subscribeConfirm } from "@/lib/confirm";

export function ConfirmHost() {
  const request = useSyncExternalStore(subscribeConfirm, getConfirm, getServerConfirm);
  if (!request) return null;
  return <TaskDialog title={request.title} description={request.body} size="sm" onClose={() => settleConfirm(false)}>
    <div className="task-dialog-actions">
      <button type="button" className="invoice-secondary-button" onClick={() => settleConfirm(false)}>Cancel</button>
      <button type="button" className={request.tone === "danger" ? "danger-button" : "primary-button"} autoFocus onClick={() => settleConfirm(true)}>{request.confirmLabel}</button>
    </div>
  </TaskDialog>;
}
