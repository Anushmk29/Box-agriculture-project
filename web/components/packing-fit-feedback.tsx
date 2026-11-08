"use client";

import { t, useLanguage } from "@/lib/language";

import { useState } from "react";
import { api } from "@/lib/api";
import { TaskDialog } from "@/components/task-dialog";
import { StatusMessage } from "@/components/status-message";

export function PackingFitFeedback({ boxId, boxNumber, revision }: { boxId: string; boxNumber: number; revision: number }) {
  useLanguage();
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState("FIT"), [note, setNote] = useState("");
  const [error, setError] = useState(""), [saved, setSaved] = useState(false);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api(`/packing-history/boxes/${boxId}/feedback`, { method: "POST", body: JSON.stringify({ expected_revision: revision, outcome, note }) });
      setOpen(false); setSaved(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save packing feedback"); }
    finally { setBusy(false); }
  }
  return <>
    <button type="button" className="invoice-secondary-button btn-sm" onClick={() => { setError(""); setOpen(true); }}>{saved ? t("Fit feedback saved") : t("Record packing fit")}</button>
    {open && <TaskDialog title={t(`Packing fit · box ${boxNumber}`)} description={t("Record what happened during packing. The original suggestion and current contents are saved with your observation.")} size="sm" busy={busy} onClose={() => setOpen(false)}>
      <form onSubmit={save}><StatusMessage error={error} />
        <label>{t("Result")}<select disabled={busy} value={outcome} onChange={event => setOutcome(event.target.value)}>
          <option value="FIT">{t("Contents fit")}</option><option value="LARGER_BOX">{t("Needed a larger box")}</option><option value="EXCESS_SPACE">{t("Too much empty space")}</option><option value="REPACKED">{t("Needed repacking")}</option><option value="DAMAGE">{t("Damage or crushing")}</option>
        </select></label>
        <label>{t("Details")}<textarea disabled={busy} required={outcome !== "FIT"} minLength={outcome !== "FIT" ? 5 : undefined} maxLength={2048} rows={3} value={note} onChange={event => setNote(event.target.value)} placeholder={t("Include padding, cooling materials or protection issues where relevant.")} /></label>
        <div className="task-dialog-actions"><button type="button" disabled={busy} onClick={() => setOpen(false)}>{t("Cancel")}</button><button className="primary-button" disabled={busy || (outcome !== "FIT" && note.trim().length < 5)}>{busy ? t("Saving…") : t("Save observation")}</button></div>
      </form>
    </TaskDialog>}
  </>;
}
