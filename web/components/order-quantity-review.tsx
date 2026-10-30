"use client";

import { Fragment, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { QuantityReview, QuantityRow } from "@/lib/order-quantities";
import { TableLoadingSkeleton } from "@/components/loading-skeleton";
import { TaskDialog } from "@/components/task-dialog";
import { StatusMessage } from "@/components/status-message";

const units = [["kg", "Kilograms"], ["g", "Grams"], ["pack", "Packs"], ["pc", "Pieces"], ["box", "Boxes"], ["bag", "Bags"], ["bunch", "Bunches"], ["tray", "Trays"]];
const resultText = (row: QuantityRow) => row.computed ? `${row.computed.pack_count} packs · ${row.computed.net_weight_kg} kg` : row.issue;
const similar = (a: QuantityRow, b: QuantityRow) => (a.product_code || a.product_name) === (b.product_code || b.product_name)
  && a.pack_size === b.pack_size && a.unit === b.unit && a.issue === b.issue;

type Props = {
  rawFileId: string; issues: number; onIssues: (count: number) => void;
  initialData?: QuantityReview; onReview?: (review: QuantityReview | undefined) => void;
  highlighted?: boolean; disabled?: boolean;
};

export function OrderQuantityReview({ rawFileId, issues, onIssues, initialData, onReview, highlighted = false, disabled = false }: Props) {
  const [open, setOpen] = useState(false);
  return <section className="task-toolbar"><div><strong>Weights and pack counts</strong>
    <p>{issues ? `${issues} issues need attention. Valid lines need no approval.` : "Quantities checked. Open to view or correct order lines."}</p>
  </div><button type="button" className="invoice-secondary-button" disabled={disabled} data-next-action={highlighted ? "true" : undefined} onClick={() => setOpen(true)}>Review weights and pack counts</button>
    {open && <QuantityDialog rawFileId={rawFileId} initialData={initialData} onReview={onReview} onClose={() => setOpen(false)} onIssues={onIssues} />}
  </section>;
}

function QuantityDialog({ rawFileId, initialData, onClose, onIssues, onReview }: Pick<Props, "rawFileId" | "initialData" | "onIssues" | "onReview"> & { onClose: () => void }) {
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
      else accept(value, `Saved ${value.changed_count} corrections. Each shop’s order amount was preserved.`);
    } catch (reason) { setPreview(undefined); setError(reason instanceof Error ? reason.message : "Unable to correct the selected lines"); }
    finally { setBusy(false); }
  }

  const rows = data?.items.filter(row => !issuesOnly || row.issue) || [];
  const selectable = rows.filter(row => row.issue).map(row => row.row_id);
  return <TaskDialog title="Review order quantities" description="Review issues only, or correct repeated problems together. 1 kg in 100 g packs means 10 packs. Original values stay in the correction history." size="lg" busy={busy} onClose={onClose}>
    <StatusMessage error={error} />
    {success && <p role="status" className="message success">{success}</p>}
    {!data && loading ? <TableLoadingSkeleton label="Loading quantities…" columns={["Select", "Shop and product", "Order amount", "Packing", "Calculated result", "Review"]} /> : !data ? <button type="button" onClick={() => { setError(""); setRevision(value => value + 1); }}>Reload quantities</button> : <>
      <div className="quantity-review-heading"><p role="status">{data.issue_count} issues across {data.total} ordered lines. Valid lines need no approval.</p>
        <div className="queue-filters" role="group" aria-label="Quantity view"><button type="button" aria-pressed={issuesOnly} disabled={busy} onClick={() => changeView(true)}>Issues only</button><button type="button" aria-pressed={!issuesOnly} disabled={busy} onClick={() => changeView(false)}>All lines</button></div>
      </div>
      {loading && <p role="status">Updating quantities…</p>}
      {data.template_issues?.map(issue => <p className="message warning" key={issue}>{issue} Check Customer Excel format before correcting lines.</p>)}
      {data.unknown_unit_count > 0 && <fieldset><legend>Choose the unit for {data.unknown_unit_count} lines without a declared unit</legend><p>Check the original order. Printed row units remain in use.</p><label>Quantity unit for unspecified lines<select value={unit} onChange={e => setUnit(e.target.value)}><option value="">Choose quantity unit</option>{units.map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label><button type="button" disabled={!unit || busy || loading} onClick={chooseUnit}>Apply unit to unspecified lines</button></fieldset>}

      {selected.length > 0 && <form className="quantity-bulk-editor" onSubmit={event => { event.preventDefault(); void bulk(!preview); }}>
        <h3>Correct {selected.length} selected {selected.length === 1 ? "line" : "lines"} together</h3>
        <p>Only the fields you enter will change. Each shop keeps its order amount and declared net weight.</p>
        <div className="packing-setup-grid"><label>New quantity unit<select value={bulkUnit} disabled={busy} onChange={event => { setBulkUnit(event.target.value); setPreview(undefined); }}><option value="">Keep each line’s unit</option>{units.map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
          <label>New physical pack size<input value={bulkSize} disabled={busy} placeholder="Keep existing, or enter 100g" onChange={event => { setBulkSize(event.target.value); setPreview(undefined); }} /></label>
          <label>Reason for these corrections<input required value={bulkNote} disabled={busy} onChange={event => { setBulkNote(event.target.value); setPreview(undefined); }} /></label></div>
        {preview && <div className="quantity-preview"><strong>Preview — {preview.length} lines calculate correctly</strong><ul>{preview.map(row => <li key={row.row_id}>{row.client_code} · {row.product_name}: {row.quantity} {row.unit}, {row.pack_size} → {resultText(row)}</li>)}</ul></div>}
        <div className="task-toolbar-actions"><button type="submit" className="primary-button" disabled={busy || loading || (!bulkUnit && !bulkSize.trim()) || !bulkNote.trim()}>{preview ? `Save ${selected.length} corrections` : `Preview ${selected.length} corrections`}</button><button type="button" disabled={busy} onClick={() => changeSelection([])}>Clear selection</button></div>
      </form>}

      {!rows.length && !loading ? <p className="quantity-clear" role="status">{issuesOnly ? "No line issues to correct. Use All lines to check calculated quantities." : "No ordered lines to show."}</p> : <div className="packing-setup-table quantity-review-table"><table><thead><tr>
        <th><input type="checkbox" aria-label="Select all shown issues" checked={selectable.length > 0 && selectable.every(id => selected.includes(id))} disabled={busy || loading || !selectable.length} onChange={event => changeSelection(event.target.checked ? selectable : [])} /></th>
        <th>Shop and product</th><th>Order amount</th><th>Packing</th><th>Calculated result</th><th>Review</th>
      </tr></thead><tbody>{rows.map(row => {
        const related = rows.filter(other => other.issue && similar(row, other));
        return <Fragment key={row.row_id}><tr>
          <td>{row.issue && <input type="checkbox" aria-label={`Select quantity ${row.row_id}`} checked={selected.includes(row.row_id)} disabled={busy || loading} onChange={event => changeSelection(event.target.checked ? [...selected, row.row_id] : selected.filter(id => id !== row.row_id))} />}</td>
          <td>{row.client_code}<br /><strong>{row.product_name}</strong><small>{String(row.source.sheet || "")} {String(row.source.quantity_cell || row.source.row || "")}</small></td>
          <td>{row.quantity} {row.unit}</td><td>{row.pack_size || "Unspecified"}</td><td>{resultText(row)}</td>
          <td><button type="button" disabled={busy || loading} aria-label={`Correct quantity ${row.row_id}`} onClick={() => { changeSelection([]); setEdit({ ...row }); setNote(""); }}>Edit</button>
            {related.length > 1 && <button type="button" className="quantity-similar" disabled={busy || loading} aria-label={`Select ${related.length} similar issues for ${row.row_id}`} onClick={() => changeSelection(related.map(item => item.row_id))}>Select {related.length} similar</button>}</td>
        </tr>{edit?.row_id === row.row_id && <tr><td colSpan={6}><form className="quantity-line-editor" onSubmit={save}><fieldset><legend>Correct {edit.product_name}</legend>
          <p>Original: {String(edit.source.raw_quantity ?? edit.quantity)} {String(edit.source.raw_unit ?? edit.unit)}, packing {String(edit.source.raw_pack_size ?? edit.pack_size ?? "unspecified")}. Use zero with a reason to exclude a line.</p>
          <div className="packing-setup-grid"><label>Order amount<input required type="number" step="any" min="0" value={edit.quantity} onChange={e => setEdit({ ...edit, quantity: Number(e.target.value) })} /></label><label>Order amount unit<select required={edit.quantity > 0} value={units.some(([key]) => key === edit.unit) ? edit.unit : ""} onChange={e => setEdit({ ...edit, unit: e.target.value })}><option value="">Choose unit</option>{units.map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
            <label>Physical pack size<input required={edit.quantity > 0} placeholder="100g bag or 1pc" value={edit.pack_size || ""} onChange={e => setEdit({ ...edit, pack_size: e.target.value })} /></label><label>Declared line net kg, if supplied<input type="number" step="any" min="0.0001" value={edit.net_weight_kg ?? ""} onChange={e => setEdit({ ...edit, net_weight_kg: e.target.value ? Number(e.target.value) : null })} /></label></div>
          <label>Reason for correction<input required value={note} onChange={e => setNote(e.target.value)} /></label><div className="task-toolbar-actions"><button type="submit" className="primary-button" disabled={busy}>Save quantity correction</button><button type="button" disabled={busy} onClick={() => setEdit(undefined)}>Cancel correction</button></div>
        </fieldset></form></td></tr>}</Fragment>;
      })}</tbody></table></div>}
      <div className="register-pagination"><span>{issuesOnly ? "Showing issues only" : "Showing all lines"}</span><button type="button" disabled={!offset || busy || loading} onClick={() => { setOffset(value => Math.max(0, value - 50)); changeSelection([]); }}>Previous quantities</button><button type="button" disabled={!data.has_more || busy || loading} onClick={() => { setOffset(value => value + 50); changeSelection([]); }}>Next quantities</button><button type="button" disabled={busy || loading} onClick={() => { setEdit(undefined); changeSelection([]); setError(""); setRevision(value => value + 1); }}>Reload quantities</button></div>
    </>}
  </TaskDialog>;
}
