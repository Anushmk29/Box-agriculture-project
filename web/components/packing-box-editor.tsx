"use client";

import { t, useLanguage } from "@/lib/language";

import { useState } from "react";
import { api } from "@/lib/api";
import { StatusMessage } from "@/components/status-message";
import { TaskDialog } from "@/components/task-dialog";
export type Box = {
 id: string; size_name: string; box_type: string; length_cm: number; width_cm: number; height_cm: number;
 dimension_source?: { file: string; sheet: string; row: number; basis: string } | null;
 max_safe_net_weight_kg: number; packing_revision: number; packing_reviewed: boolean; is_active: boolean; usable_volume_cm3: number | null;
};
const label = (value: string) => t(value.toLowerCase().replaceAll("_", " "));
export function BoxEditor({ initial, onClose, onSaved }: { initial: Box; onClose: () => void; onSaved: () => void }) {
  useLanguage();
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function field<K extends keyof Box>(key: K, value: Box[K]) { setDraft(current => ({ ...current, [key]: value })); }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setError("");
    const { id, packing_revision, size_name, box_type, length_cm, width_cm, height_cm, max_safe_net_weight_kg, packing_reviewed, is_active } = draft;
    try { await api(`/packing-setup/boxes/${id}`, { method: "PUT", body: JSON.stringify({ expected_revision: packing_revision, size_name, box_type, length_cm, width_cm, height_cm, max_safe_net_weight_kg, packing_reviewed, is_active }) }); onSaved(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save box type"); }
    finally { setBusy(false); }
  }
  return <TaskDialog title={t(`Edit box: ${draft.size_name}`)} description={t("External dimensions are fixed to the supplied box workbook and used for both packing estimates and shipping.")} size="lg" busy={busy} onClose={onClose}><form onSubmit={save}><StatusMessage error={error} /><div className="packing-setup-grid"><label>{t("Size name")}<input readOnly value={draft.size_name} /></label><label>{t("Material")}<input readOnly value={label(draft.box_type)} /></label></div>
    <fieldset><legend>{t("External box dimensions (cm)")}</legend><div className="packing-setup-grid">{(["length", "width", "height"] as const).map(axis => <label key={axis}>{t("External ")}{axis}<input readOnly aria-label={t(`External ${axis} (cm)`)} value={draft[`${axis}_cm`]} /></label>)}</div><p className="task-note">{t("Source: ")}{draft.dimension_source?.file || t("100726 Dimention (1).xlsx")}{draft.dimension_source ? t(`, ${draft.dimension_source.sheet}, row ${draft.dimension_source.row}`) : ""}{t(". These dimensions cannot be changed here.")}</p></fieldset>
    <label>{t("Safe net weight (kg)")}<input required type="number" min="0.01" step="0.01" value={draft.max_safe_net_weight_kg || ""} onChange={e => setDraft(current => ({ ...current, max_safe_net_weight_kg: Number(e.target.value), packing_reviewed: false }))} /></label><p>{t("Packing space estimate: ")}{(draft.length_cm * draft.width_cm * draft.height_cm * .92 / 1000).toFixed(2)} {t("L, based on external dimensions with the existing 8% space allowance.")}</p><label className="packing-setup-check"><input type="checkbox" checked={draft.packing_reviewed} onChange={e => field("packing_reviewed", e.target.checked)} />{t("I reviewed the safe weight capacity")}</label><label className="packing-setup-check"><input type="checkbox" checked={draft.is_active} onChange={e => field("is_active", e.target.checked)} />{t("Available for new packing")}</label><div className="packing-setup-actions"><button type="button" disabled={busy} onClick={onClose}>{t("Cancel")}</button><button className="primary-button" disabled={busy}>{t("Save box type")}</button></div></form></TaskDialog>;
}
