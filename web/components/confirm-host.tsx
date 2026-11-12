"use client";

import { t, useLanguage } from "@/lib/language";

import { useSyncExternalStore } from "react";
import { TaskDialog } from "@/components/task-dialog";
import { getConfirm, getServerConfirm, settleConfirm, subscribeConfirm } from "@/lib/confirm";

export function ConfirmHost() {
  useLanguage();
  const request = useSyncExternalStore(subscribeConfirm, getConfirm, getServerConfirm);
  if (!request) return null;
  return <TaskDialog title={t(request.title)} description={request.body} size="sm" onClose={() => settleConfirm(false)}>
    <div className="task-dialog-actions">
      <button type="button" className="invoice-secondary-button" onClick={() => settleConfirm(false)}>{t("Cancel")}</button>
      <button type="button" className={request.tone === "danger" ? "danger-button" : "primary-button"} autoFocus onClick={() => settleConfirm(true)}>{t(request.confirmLabel)}</button>
    </div>
  </TaskDialog>;
}
