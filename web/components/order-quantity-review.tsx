"use client";

import { t, useLanguage } from "@/lib/language";

import { Fragment, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { QuantityReview, QuantityRow } from "@/lib/order-quantities";
import { TaskDialog } from "@/components/task-dialog";
import { StatusMessage } from "@/components/status-message";

const units = [["kg", "Kilograms"], ["g", "Grams"], ["pack", "Packs"], ["pc", "Pieces"], ["box", "Boxes"], ["bag", "Bags"], ["bunch", "Bunches"], ["tray", "Trays"]];
const resultText = (row: QuantityRow) => row.computed
  ? row.computed.pack_count === null ? `${row.computed.net_weight_kg} kg · Pack count pending` : `${row.computed.pack_count} packs · ${row.computed.net_weight_kg} kg`
  : row.can_keep_as_ordered ? "Keep the ordered weight. Packs will be recorded when purchased." : row.issue;

type Props = {
  rawFileId: string; issues: number; onIssues: (count: number) => void;
  initialData?: QuantityReview; onReview?: (review: QuantityReview | undefined) => void;
  highlighted?: boolean; disabled?: boolean;
};

export function OrderQuantityReview({ rawFileId, issues, onIssues, initialData, onReview, highlighted = false, disabled = false }: Props) {
  useLanguage();
  const [open, setOpen] = useState(false);
  return <section className="task-toolbar"><div><strong>{t("Weights and pack counts")}</strong>
    <p>{issues ? t(`${issues} issues need attention. Valid lines need no approval.`) : t("Quantities checked. Open to view or correct order lines.")}</p>
  </div><button type="button" className="invoice-secondary-button" disabled={disabled} data-next-action={highlighted ? "true" : undefined} onClick={() => setOpen(true)}>{t("Review weights and pack counts")}</button>
    {open && <QuantityDialog rawFileId={rawFileId} initialData={initialData} onReview={onReview} onClose={() => setOpen(false)} onIssues={onIssues} />}
  </section>;
}

function QuantityDialog({ rawFileId, initialData, onClose, onIssues, onReview }: Pick<Props, "rawFileId" | "initialData" | "onIssues" | "onReview"> & { onClose: () => void }) {
  useLanguage();
  const [startingReview] = useState(initialData);
  const [data, setData] = useState<QuantityReview | undefined>(initialData);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [issuesOnly, setIssuesOnly] = useState(true);
  const readKey = `${rawFileId}:${offset}:${revision}:${issuesOnly}`;
  const [resolvedRead, setResolvedRead] = useState(initialData ? readKey : "");
  const [edit, setEdit] = useState<QuantityRow>();
  const [note, setNote] = useState("");
  const [unit, setUnit] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [bulkUnit, setBulkUnit] = useState("");
  const [bulkSize, setBulkSize] = useState("");
  const [bulkNote, setBulkNote] = useState("");
  const [bulkEditing, setBulkEditing] = useState(false);
  const [preview, setPreview] = useState<QuantityRow[]>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const loading = resolvedRead !== readKey;
  const query = `issues_only=${issuesOnly}`;

  useEffect(() => {
    // Validation already calculated this review. Saves use its revision and
    // confirmation validates again; the snapshot stays within this workspace.
    if (startingReview && !offset && !revision && issuesOnly) return;
    const controller = new AbortController();
    api<QuantityReview>(`/orders/${rawFileId}/quantity-review?offset=${offset}&limit=50&issues_only=${issuesOnly}`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setData(value); onIssues(value.issue_count); }
    }).catch(reason => { if (!controller.signal.aborted) { setData(undefined); setError(reason instanceof Error ? reason.message : "Unable to load quantities"); } })
      .finally(() => { if (!controller.signal.aborted) setResolvedRead(readKey); });
    return () => controller.abort();
  }, [rawFileId, offset, revision, issuesOnly, onIssues, readKey, startingReview]);

  function accept(value: QuantityReview, message: string) {
    setData(value); onIssues(value.issue_count); onReview?.(issuesOnly ? value : undefined);
    setOffset(0); setResolvedRead(`${rawFileId}:0:${revision}:${issuesOnly}`);
    setEdit(undefined); setSelected([]); setPreview(undefined); setSuccess(message);
  }
  function changeSelection(ids: string[]) { setSelected(ids); setPreview(undefined); setEdit(undefined); setSuccess(""); }
  function changeView(value: boolean) { setIssuesOnly(value); setOffset(0); setSelected([]); setPreview(undefined); setEdit(undefined); setError(""); setSuccess(""); setRevision(value => value + 1); }

  async function chooseUnit() {
    if (!data || !unit || busy) return;
    setBusy(true); setError("");
    try {
      const value = await api<QuantityReview>(`/orders/${rawFileId}/quantity-unit?${query}`, { method: "POST", body: JSON.stringify({ expected_revision: data.revision, unit }) });
      accept(value, "Updated unspecified units. Printed units were preserved.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to choose quantity unit"); }
    finally { setBusy(false); }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!edit || !data || busy) return;
    setBusy(true); setError("");
    try {
      const value = await api<QuantityReview>(`/orders/${rawFileId}/quantity-review/${edit.row_id}?${query}`, { method: "PUT", body: JSON.stringify({ expected_revision: data.revision, quantity: edit.quantity, unit: edit.unit, pack_size: edit.pack_size, net_weight_kg: edit.net_weight_kg, note }) });
      const changed = (value.changed_rows || value.items).find(row => row.row_id === edit.row_id);
      accept(value, changed?.computed ? `Saved ${changed.product_name}: ${resultText(changed)}.` : "Quantity correction saved.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save the quantity correction"); }
    finally { setBusy(false); }
  }
  async function bulk(previewOnly: boolean) {
    if (!data || busy || !selected.length) return;
    setBusy(true); setError(""); setSuccess("");
    try {
      const value = await api<QuantityReview>(`/orders/${rawFileId}/quantity-review/bulk?${query}`, { method: "POST", body: JSON.stringify({ expected_revision: data.revision, row_ids: selected, unit: bulkUnit || null, pack_size: bulkSize.trim() || null, note: bulkNote.trim(), preview_only: previewOnly }) });
      if (previewOnly) setPreview(value.items);
      else accept(value, `Saved ${value.changed_count} corrections. Each subcustomer’s order amount was preserved.`);
    } catch (reason) { setPreview(undefined); setError(reason instanceof Error ? reason.message : "Unable to correct the selected lines"); }
    finally { setBusy(false); }
  }

  async function keepAsOrdered() {
    if (!data || busy || loading || !data.keepable_issue_count) return;
    setBusy(true); setError(""); setSuccess("");
    try {
      const value = await api<QuantityReview>(`/orders/${rawFileId}/quantity-review/keep?${query}`, { method: "POST", body: JSON.stringify({ expected_revision: data.revision, all_eligible: true }) });
      accept(value, `Order details kept unchanged. ${value.issue_count} ${value.issue_count === 1 ? "line still needs" : "lines still need"} an edit below.`);
      if (!value.issue_count) onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save the review"); }
    finally { setBusy(false); }
  }

  const rows = data?.items.filter(row => !issuesOnly || row.issue) || [];
  const selectable = rows.filter(row => row.issue).map(row => row.row_id);
  return <TaskDialog title={t("Review order quantities")} description={t("Keep every subcustomer’s quantities and packing exactly as ordered.")} size="lg" guidance={false} busy={busy} onClose={onClose}>
    <StatusMessage error={error} />
    {success && <p role="status" className="message success">{t(success)}</p>}
    {!data && loading ? <p role="status">{t("Loading quantities…")}</p> : !data ? <button type="button" onClick={() => { setError(""); setRevision(value => value + 1); }}>{t("Reload quantities")}</button> : <>
      <div className="quantity-review-heading"><p role="status">{data.total} {t("order lines.")}{Boolean(data.reviewed_count) && t(` ${data.reviewed_count} reviewed and kept as ordered.`)}</p></div>
      {Boolean(data.keepable_issue_count) && <div className="quantity-review-all task-toolbar-actions">
        <button type="button" className="primary-button" disabled={busy || loading} onClick={() => void keepAsOrdered()}>{busy ? t("Saving review…") : t("Keep all as ordered")}</button>
        <p>{t("Applies to the whole file.")}</p>
      </div>}
      {data.issue_count > 0 && !data.keepable_issue_count && <p role="status">{data.issue_count} {data.issue_count === 1 ? t("line needs") : t("lines need")} {t("an edit below.")}</p>}
      {!data.issue_count && <p role="status" className="quantity-clear">{t("Quantities reviewed. You can close this window and confirm the orders.")}</p>}
      {loading && <p role="status">{t("Updating quantities…")}</p>}
      {data.template_issues?.map(issue => <p className="message warning" key={issue}>{t(issue)} {t("Check Customer Excel format before correcting lines.")}</p>)}
      {data.unknown_unit_count > 0 && <fieldset><legend>{t("Choose the unit for ")}{data.unknown_unit_count} {t("lines without a declared unit")}</legend><p>{t("Check the original order. Printed row units remain in use.")}</p><label>{t("Quantity unit for unspecified lines")}<select value={unit} onChange={e => setUnit(e.target.value)}><option value="">{t("Choose quantity unit")}</option>{units.map(([key, name]) => <option key={key} value={key}>{t(name)}</option>)}</select></label><button type="button" disabled={!unit || busy || loading} onClick={chooseUnit}>{t("Apply unit to unspecified lines")}</button></fieldset>}

      <div className="quantity-review-lines">
        <div className="quantity-review-heading">
          <div className="queue-filters" role="group" aria-label={t("Quantity view")}><button type="button" aria-pressed={issuesOnly} disabled={busy} onClick={() => changeView(true)}>{t("Issues only")}</button><button type="button" aria-pressed={!issuesOnly} disabled={busy} onClick={() => changeView(false)}>{t("All lines")}</button></div>
        </div>
        <details className="quantity-bulk-options" onToggle={event => { setBulkEditing(event.currentTarget.open); changeSelection([]); }}>
          <summary>{t("Edit several lines")}</summary><p>{t("Select the lines below to correct them together.")}</p>
        </details>
      {bulkEditing && selected.length > 0 && <form className="quantity-bulk-editor" onSubmit={event => { event.preventDefault(); void bulk(!preview); }}>
        <h3>{t("Correct ")}{selected.length} {t("selected ")}{selected.length === 1 ? t("line") : t("lines")}</h3>
        <p>{t("Only the fields you enter will change. Each subcustomer keeps its order amount and declared net weight.")}</p>
        <div className="packing-setup-grid"><label>{t("New quantity unit")}<select value={bulkUnit} disabled={busy} onChange={event => { setBulkUnit(event.target.value); setPreview(undefined); }}><option value="">{t("Keep each line’s unit")}</option>{units.map(([key, name]) => <option key={key} value={key}>{t(name)}</option>)}</select></label>
          <label>{t("New physical pack size")}<input value={bulkSize} disabled={busy} placeholder={t("Keep existing, or enter 100g")} onChange={event => { setBulkSize(event.target.value); setPreview(undefined); }} /></label>
          <label>{t("Reason for these corrections")}<input required value={bulkNote} disabled={busy} onChange={event => { setBulkNote(event.target.value); setPreview(undefined); }} /></label></div>
        {preview && <div className="quantity-preview"><strong>{t("Preview — ")}{preview.length} {t("lines calculate correctly")}</strong><ul>{preview.map(row => <li key={row.row_id}>{row.client_code} · {row.product_name}: {row.quantity} {row.unit}, {row.pack_size} → {t(resultText(row))}</li>)}</ul></div>}
        <div className="task-toolbar-actions"><button type="submit" className="primary-button" disabled={busy || loading || (!bulkUnit && !bulkSize.trim()) || !bulkNote.trim()}>{preview ? t(`Save ${selected.length} corrections`) : t(`Preview ${selected.length} corrections`)}</button><button type="button" disabled={busy} onClick={() => changeSelection([])}>{t("Clear selection")}</button></div>
      </form>}

      {!rows.length && !loading ? <p className="quantity-clear" role="status">{issuesOnly ? t("No line issues to correct. Use All lines to check calculated quantities.") : t("No ordered lines to show.")}</p> : <div className="packing-setup-table quantity-review-table"><table><thead><tr>
        {bulkEditing && <th><input type="checkbox" aria-label={t("Select all shown issues")} checked={selectable.length > 0 && selectable.every(id => selected.includes(id))} disabled={busy || loading || !selectable.length} onChange={event => changeSelection(event.target.checked ? selectable : [])} /></th>}
        <th>{t("Subcustomer and product")}</th><th>{t("Order amount")}</th><th>{t("Packing")}</th><th>{t("Calculated result")}</th><th>{t("Review")}</th>
      </tr></thead><tbody>{rows.map(row => {
        return <Fragment key={row.row_id}><tr>
          {bulkEditing && <td>{row.issue && <input type="checkbox" aria-label={t(`Select quantity ${row.row_id}`)} checked={selected.includes(row.row_id)} disabled={busy || loading} onChange={event => changeSelection(event.target.checked ? [...selected, row.row_id] : selected.filter(id => id !== row.row_id))} />}</td>}
          <td>{row.client_code}<br /><strong>{row.product_name}</strong><small>{String(row.source.sheet || "")} {String(row.source.quantity_cell || row.source.row || "")}</small></td>
          <td>{row.quantity} {row.unit}</td><td>{row.pack_size || t("Unspecified")}</td><td>{t(resultText(row))}{row.reviewed_unchanged && <small className="quantity-reviewed">{t("Reviewed—kept as ordered")}</small>}</td>
          <td><button type="button" disabled={busy || loading} aria-label={t(`Correct quantity ${row.row_id}`)} onClick={() => { changeSelection([]); setEdit({ ...row }); setNote(""); }}>{t("Edit")}</button>
          </td>
        </tr>{edit?.row_id === row.row_id && <tr><td colSpan={bulkEditing ? 6 : 5}><form className="quantity-line-editor" onSubmit={save}><fieldset><legend>{t("Correct ")}{edit.product_name}</legend>
          <p>{t("Original: ")}{String(edit.source.raw_quantity ?? edit.quantity)} {String(edit.source.raw_unit ?? edit.unit)}{t(", packing ")}{String(edit.source.raw_pack_size ?? edit.pack_size ?? "unspecified")}{t(". Use zero with a reason to exclude a line.")}</p>
          <div className="packing-setup-grid"><label>{t("Order amount")}<input required type="number" step="any" min="0" value={edit.quantity} onChange={e => setEdit({ ...edit, quantity: Number(e.target.value) })} /></label><label>{t("Order amount unit")}<select required={edit.quantity > 0} value={units.some(([key]) => key === edit.unit) ? edit.unit : ""} onChange={e => setEdit({ ...edit, unit: e.target.value })}><option value="">{t("Choose unit")}</option>{units.map(([key, name]) => <option key={key} value={key}>{t(name)}</option>)}</select></label>
            <label>{t("Physical pack size")}<input required={edit.quantity > 0} placeholder={t("100g bag or 1pc")} value={edit.pack_size || ""} onChange={e => setEdit({ ...edit, pack_size: e.target.value })} /></label><label>{t("Declared line net kg, if supplied")}<input type="number" step="any" min="0.0001" value={edit.net_weight_kg ?? ""} onChange={e => setEdit({ ...edit, net_weight_kg: e.target.value ? Number(e.target.value) : null })} /></label></div>
          <label>{t("Reason for correction")}<input required value={note} onChange={e => setNote(e.target.value)} /></label><div className="task-toolbar-actions"><button type="submit" className="primary-button" disabled={busy}>{t("Save quantity correction")}</button><button type="button" disabled={busy} onClick={() => setEdit(undefined)}>{t("Cancel correction")}</button></div>
        </fieldset></form></td></tr>}</Fragment>;
      })}</tbody></table></div>}
      {(offset > 0 || data.has_more) && <div className="register-pagination"><button type="button" disabled={!offset || busy || loading} onClick={() => { setOffset(value => Math.max(0, value - 50)); changeSelection([]); }}>{t("Previous quantities")}</button><button type="button" disabled={!data.has_more || busy || loading} onClick={() => { setOffset(value => value + 50); changeSelection([]); }}>{t("Next quantities")}</button></div>}
      </div>
    </>}
  </TaskDialog>;
}
