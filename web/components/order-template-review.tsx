"use client";

import { t, useLanguage } from "@/lib/language";

import { useState } from "react";
import { api } from "@/lib/api";
import { TaskDialog } from "@/components/task-dialog";
import { StatusMessage } from "@/components/status-message";

type SheetMapping = { reference_sheet: string; anchors: Record<string, string>; fields: Record<string, number | number[]>; customers: Record<string, string> | null };
type Definition = { key: string; sample: string; main_customer_code: string; client_code: string | null; default_unit: string | null; numeric_pack_unit: string | null; sheets: SheetMapping[] };
type Format = { id: string; version: number; confirmed: boolean; automatic?: boolean; definition: Definition };
type Review = { revision: string; formats: Format[]; detected: { key?: string; status: string; confirmed?: boolean; issues: string[]; shipment_references?: string[] }[]; issues: string[] };
const units = ["kg", "g", "pack", "pc", "box", "bag", "bunch", "tray"];
const fieldNames: Record<string, string> = { code: "Product code", supplier_code: "Supplier code", customer_product_code: "Customer product code", name: "Product name", pack: "Packing", quantity: "Order amount", unit: "Order unit", adjacent: "Adjacent pack weight (kg)", description_pack: "Description containing pack size" };

export function OrderTemplateReview({ rawFileId, onReviewed, highlighted = false, disabled = false, actionLabel = "Review customer Excel format" }: { rawFileId: string; onReviewed: () => Promise<void>; highlighted?: boolean; disabled?: boolean; actionLabel?: string }) {
  useLanguage();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Review>();
  const [selected, setSelected] = useState<Format>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    setOpen(true); setBusy(true); setError(""); setData(undefined); setSelected(undefined);
    try {
      const result = await api<Review>(`/order-templates/${rawFileId}`);
      setData(result);
      const key = result.detected[0]?.key;
      setSelected(result.formats.find(f => f.definition.key === key));
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to load customer formats"); }
    finally { setBusy(false); }
  }
  function changeSheet(index: number, update: Partial<SheetMapping>) {
    if (!selected) return;
    setSelected({ ...selected, definition: { ...selected.definition, sheets: selected.definition.sheets.map((s, i) => i === index ? { ...s, ...update } : s) } });
  }
  function moveColumn(index: number, kind: "anchors" | "customers", oldColumn: string, newColumn: string) {
    if (!selected) return;
    const entries = { ...selected.definition.sheets[index][kind] };
    if (newColumn !== oldColumn && newColumn in entries) { setError("That column is already mapped. Remove its old mapping first."); return; }
    const value = entries[oldColumn]; delete entries[oldColumn];
    if (newColumn) entries[newColumn] = value;
    changeSheet(index, { [kind]: entries });
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!selected || !data || busy) return;
    setBusy(true); setError("");
    try {
      await api(`/order-templates/${rawFileId}/confirm`, { method: "POST", body: JSON.stringify({ template_id: selected.id, expected_version: selected.version, expected_revision: data.revision, default_unit: selected.definition.default_unit || null, definition: selected.definition }) });
      await onReviewed(); setOpen(false);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to confirm the format"); }
    finally { setBusy(false); }
  }
  return <section className="task-toolbar"><div><strong>{t("Customer Excel format")}</strong><p>{t("Known formats are detected automatically. Adjust columns only if the order was read incorrectly.")}</p></div><button type="button" disabled={disabled || busy} data-next-action={highlighted ? "true" : undefined} onClick={load}>{t(actionLabel)}</button>{open && <TaskDialog title={t("Customer Excel format")} description={t("Known formats are applied automatically. Use this editor only to correct a reading problem. Saving re-reads the original file and replaces line corrections on this pending order.")} size="lg" busy={busy} onClose={() => setOpen(false)}><StatusMessage error={error} />{busy && !data ? <p role="status">{t("Loading customer formats…")}</p> : <form onSubmit={save}>
    {data?.issues.map(issue => <p className="message warning" key={issue}>{t(issue)}</p>)}
    {data?.detected[0]?.shipment_references?.map(reference => <p key={reference}>{t("Source shipment reference: ")}{reference}</p>)}
    <label>{t("Subcustomer or group format")}<select required value={selected?.id || ""} onChange={e => setSelected(data?.formats.find(f => f.id === e.target.value))}><option value="">{t("Choose the correct customer format")}</option>{data?.formats.map(f => <option key={f.id} value={f.id}>{f.definition.key} {t("· version ")}{f.version} · {f.confirmed ? t("Saved") : f.automatic ? t("Automatic") : t("Needs review")}</option>)}</select></label>
    {selected && <><p>{t("Sample file: ")}{selected.definition.sample}{t(". Main customer: ")}{selected.definition.main_customer_code}.</p>
      <label>{t("Quantity unit when the order does not print one")}<select value={selected.definition.default_unit || ""} onChange={e => setSelected({ ...selected, definition: { ...selected.definition, default_unit: e.target.value || null } })}><option value="">{t("Every order amount has an explicit unit")}</option>{units.map(u => <option key={u} value={u}>{u}</option>)}</select></label>
      <label>{t("Unit for bare numbers in Packing")}<select value={selected.definition.numeric_pack_unit || ""} onChange={e => setSelected({ ...selected, definition: { ...selected.definition, numeric_pack_unit: e.target.value || null } })}><option value="">{t("Require review")}</option><option value="g">{t("Grams per pack")}</option><option value="kg">{t("Kilograms per pack")}</option></select></label>
      {selected.definition.sheets.map((sheet, index) => <fieldset key={index}><legend>{sheet.reference_sheet}</legend><p>{t("Columns use numbers: A = 1, B = 2, C = 3. Header text identifies the table even when blank rows or dates change. Leave optional fields blank.")}</p><div className="packing-setup-grid">{Object.entries(fieldNames).map(([field, label]) => <label key={field}>{t(label)} {t("column")}<input type="number" min="1" max="512" required={field === "name" || (field === "quantity" && !sheet.customers)} value={String(sheet.fields[field] ?? "")} onChange={e => { const fields = { ...sheet.fields }; if (e.target.value) fields[field] = Number(e.target.value); else delete fields[field]; changeSheet(index, { fields }); }} /></label>)}</div>
        <details><summary>{t("Header checks and subcustomer columns")}</summary>{Object.entries(sheet.anchors).map(([column, value]) => <div className="packing-setup-grid" key={column}><label>{t("Header column")}<input type="number" min="1" max="512" required value={column} onChange={e => moveColumn(index, "anchors", column, e.target.value)} /></label><label>{t("Expected heading in column ")}{column}<input required value={value} onChange={e => changeSheet(index, { anchors: { ...sheet.anchors, [column]: e.target.value } })} /></label><button type="button" onClick={() => moveColumn(index, "anchors", column, "")}>{t("Remove heading ")}{column}</button></div>)}<button type="button" onClick={() => { const column = String(Array.from({ length: 512 }, (_, i) => i + 1).find(i => !(String(i) in sheet.anchors))); changeSheet(index, { anchors: { ...sheet.anchors, [column]: "" } }); }}>{t("Add header check")}</button>
        {sheet.customers && Object.entries(sheet.customers).map(([column, value]) => <div className="packing-setup-grid" key={column}><label>{t("Order column")}<input type="number" min="1" max="512" required value={column} onChange={e => moveColumn(index, "customers", column, e.target.value)} /></label><label>{t("Subcustomer in order column ")}{column}<input required value={value} onChange={e => changeSheet(index, { customers: { ...sheet.customers, [column]: e.target.value } })} /></label><button type="button" onClick={() => moveColumn(index, "customers", column, "")}>{t("Remove order column ")}{column}</button></div>)}{sheet.customers && <button type="button" onClick={() => { const column = String(Array.from({ length: 512 }, (_, i) => i + 1).find(i => !(String(i) in sheet.customers!))); changeSheet(index, { customers: { ...sheet.customers, [column]: "" } }); }}>{t("Add subcustomer column")}</button>}</details>
      </fieldset>)}
      <button type="submit" disabled={busy}>{t("Confirm format and re-read order")}</button></>}
    </form>}</TaskDialog>}</section>;
}
