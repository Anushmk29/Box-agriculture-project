"use client";

import { t, useLanguage } from "@/lib/language";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Save, Snowflake, ThermometerSun } from "lucide-react";
import { api } from "@/lib/api";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { StatusMessage } from "@/components/status-message";
import { TableSkeletonRows } from "@/components/loading-skeleton";
import { BoxEditor, type Box } from "@/components/packing-box-editor";
import { PackingHistoryPanel } from "@/components/packing-history-panel";
import historyStyles from "@/components/packing-history.module.css";

type Limit = { box_catalogue_id: string; max_net_weight_kg: number };
type Product = {
  id: string; t_code: string; name_en: string; name_th: string; name_vi: string; packing_revision: number;
  planning_basis: "VOLUME" | "HISTORY";
  box_material: string | null; temperature: string | null; control_status: string | null;
  produce_type: string; weight_class: string | null; firmness: string | null;
  volume_l_per_kg: number | null; foam_below_kg?: number | null; volume_estimated: boolean; ready: boolean; problems: string[];
  limits: Limit[]; source_choices: { row: number; name: string }[]; grouping_row: number | null;
  identity_choices: { row: number; name_en: string; name_th: string }[];
  limit_issues: { note: string; max_net_weight_kg: number | null }[];
  temperature_source?: "WORKBOOK" | "STAFF" | "SAVED" | "UNRESOLVED";
};
type Changes = Partial<Pick<Product, "box_material" | "temperature" | "control_status" | "produce_type" | "weight_class" | "firmness" | "planning_basis" | "volume_l_per_kg" | "foam_below_kg" | "grouping_row">> & { confirm?: boolean; identity_row?: number; limits?: Limit[]; limits_guidance_only?: boolean; limit_resolutions?: Record<string, string> };
type Draft = { row: Product; changes: Changes; error?: string; latest?: Product };
type Page = { items: Product[]; total: number; has_more: boolean; summary: { total: number; ready: number; strict: boolean; cold?: number; ambient?: number; temperature_unknown?: number; grouping_matched?: number } };
const categories = ["LEAFY", "FRUIT", "HERB", "ROOT", "VEGETABLE", "SPICE", "PACKAGED_FOOD", "PERSONAL_CARE", "OTHER"];
const label = (v: string) => t(v.toLowerCase().replaceAll("_", " "));
const empty = (): Product => ({ id: "new", t_code: "", name_en: "", name_th: "", name_vi: "", planning_basis: "VOLUME", packing_revision: 1, box_material: null, temperature: null, control_status: null, produce_type: "OTHER", weight_class: null, firmness: null, volume_l_per_kg: null, volume_estimated: false, ready: false, problems: [], limits: [], source_choices: [], grouping_row: null, identity_choices: [], limit_issues: [] });
const columns = ["Product", "Box / temperature", "EL", "Handling", "Packing basis", "Box limits"].map(label => ({ label }));

export default function PackingSetup() {
  useLanguage();
  const [tab, setTab] = useState("products");
  const [q, setQ] = useState(""), [status, setStatus] = useState("all"), [el, setEl] = useState("all");
  const [temperature, setTemperature] = useState("all");
  const [offset, setOffset] = useState(0), [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<Page | null>(null), [boxes, setBoxes] = useState<Box[]>([]);
  const [box, setBox] = useState<Box | null>(null), [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [showChanges, setShowChanges] = useState(false);
  const [activeProduct, setActiveProduct] = useState("");
  const [adding, setAdding] = useState<Product | null>(null), [newChanges, setNewChanges] = useState<Changes>({});
  const [busy, setBusy] = useState(false), [loaded, setLoaded] = useState("");
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  const key = JSON.stringify([q, status, el, temperature, offset, refresh]), dirty = Object.keys(drafts).length;
  useUnsavedChanges(dirty > 0 || adding !== null);
  useEffect(() => {
    let active = true;
    const timer = setTimeout(async () => {
      setError("");
      try {
        const [page, catalogue] = await Promise.all([api<Page>(`/packing-setup/table?q=${encodeURIComponent(q)}&status=${status}&el=${el}&temperature=${temperature}&offset=${offset}&limit=25`), api<Box[]>("/packing-setup/boxes")]);
        if (active) { setData(page); setBoxes(catalogue); }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "Unable to load products"); }
      finally { if (active) setLoaded(key); }
    }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [q, status, el, temperature, offset, refresh, key]);
  function edit(row: Product, patch: Changes) {
    if (row.id === "new") { setNewChanges(c => ({ ...c, ...patch })); return; }
    setDrafts(current => ({ ...current, [row.id]: { row: current[row.id]?.row || row, changes: { ...current[row.id]?.changes, confirm: false, ...patch } } }));
    setMessage("");
  }
  async function save() {
    setBusy(true); setError(""); setMessage("");
    try {
      const edits = Object.entries(drafts).map(([id, d]) => ({ id, expected_revision: d.row.packing_revision, changes: d.changes }));
      let count = 0, failed = false;
      for (let index = 0; index < edits.length; index += 100) {
        const result = await api<{ results: { id: string; product?: Product; error?: string; current?: Product }[] }>("/packing-setup/table/bulk", { method: "POST", body: JSON.stringify({ edits: edits.slice(index, index + 100) }) }, { background: true });
        setDrafts(current => {
          const next = { ...current };
          for (const r of result.results) {
            if (r.product) delete next[r.id];
            else if (next[r.id]) next[r.id] = { ...next[r.id], error: r.error, latest: r.current };
          }
          return next;
        });
        count += result.results.filter(r => r.product).length;
        failed ||= result.results.some(r => r.error);
      }
      setMessage(`${count} product${count === 1 ? "" : "s"} saved.${failed ? " Check the highlighted rows." : ""}`);
      setShowChanges(failed);
      setRefresh(v => v + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to save changes"); }
    finally { setBusy(false); setRefresh(v => v + 1); }
  }
  async function create() {
    if (!adding) return;
    setBusy(true); setError("");
    try {
      await api("/packing-setup/table", { method: "POST", body: JSON.stringify({ t_code: adding.t_code, name_en: adding.name_en, name_th: adding.name_th, name_vi: adding.name_vi, changes: newChanges }) }, { background: true });
      setAdding(null); setNewChanges({}); setMessage("Product added as a draft. Confirm its settings after review."); setOffset(0); setQ(adding.t_code); setStatus("all"); setEl("all"); setTemperature("all"); setRefresh(v => v + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to add product"); }
    finally { setBusy(false); }
  }
  function renderRow(original: Product) {
    const draft = drafts[original.id], changes = original.id === "new" ? newChanges : draft?.changes || {};
    const row = { ...(draft?.row || original), ...changes }, isNew = row.id === "new";
    const prefix = isNew ? "New product" : row.t_code;
    const guiding = activeProduct === row.id || isNew;
    const select = (title: string, field: "produce_type" | "weight_class" | "firmness", values: string[]) => <select data-guide={guiding && !row[field] ? t(`Choose ${t(title.toLowerCase())} for ${prefix}`) : undefined} data-guide-priority="20" aria-label={`${prefix} ${t(title)}`} disabled={busy} value={row[field] || ""} onChange={e => edit(original, { [field]: e.target.value || null })}><option value="">{t("Choose ")}{t(title.toLowerCase())}</option>{values.map(v => <option key={v} value={v}>{label(v)}</option>)}</select>;
    return <tr key={row.id} onFocusCapture={() => setActiveProduct(row.id)} className={draft?.error ? "product-row-error" : draft ? "product-row-dirty" : ""}>
      <td>{isNew ? <div className="product-identity-inputs">{(["t_code", "name_th", "name_vi", "name_en"] as const).map((k, i) => <input key={k} aria-label={["New SKU", "New Thai name", "New Vietnamese name", "New English name"][i]} placeholder={["SKU", "Thai name", "Vietnamese name", "English name"][i]} disabled={busy} value={row[k]} onChange={e => setAdding(c => c ? { ...c, [k]: e.target.value } : null)} />)}</div> : <><strong>{row.t_code}</strong>{row.name_th?.trim() && <span lang="th">{row.name_th}</span>}{row.name_vi?.trim() && <span lang="vi">{row.name_vi}</span>}{row.name_en?.trim() && <span lang="en">{row.name_en}</span>}<span className={`product-row-status ${row.ready && !draft ? "is-ready" : ""}`}>{draft ? t("Unsaved changes") : row.ready ? t("Confirmed") : t("Needs review")}</span></>}
        {!!row.identity_choices.length && <select aria-label={t(`${prefix} official identity`)} disabled={busy} value={changes.identity_row || ""} onChange={e => edit(original, { identity_row: Number(e.target.value) })}><option value="">{t("Resolve duplicate SKU")}</option>{row.identity_choices.map(r => <option key={r.row} value={r.row}>{t("Row ")}{r.row}: {r.name_en || r.name_th}</option>)}</select>}
        {row.source_choices.length > 1 && <select aria-label={t(`${prefix} grouping row`)} disabled={busy} value={changes.grouping_row || row.grouping_row || ""} onChange={e => edit(original, { grouping_row: Number(e.target.value) })}><option value="">{t("Choose grouping row")}</option>{row.source_choices.map(r => <option key={r.row} value={r.row}>{t("Row ")}{r.row}: {r.name}</option>)}</select>}
        {!isNew && <label className="product-confirm"><input data-guide={guiding && !changes.confirm && !row.ready ? t(`Confirm the checked settings for ${prefix}`) : undefined} data-guide-detail={row.planning_basis === "HISTORY" ? t("Check classifications and handling. Exact packing combinations are approved separately in Packing history.") : t("Check temperature, classification, handling and occupied volume before confirming.")} data-guide-priority="30" aria-label={t(`${prefix} confirm settings`)} type="checkbox" checked={Boolean(changes.confirm)} disabled={busy || (changes.grouping_row !== undefined && changes.grouping_row !== original.grouping_row)} onChange={e => edit(original, { confirm: e.target.checked })} />{t("Confirm settings")}{row.planning_basis !== "HISTORY" && row.volume_estimated && !("volume_l_per_kg" in changes) ? t(" and estimate") : ""}</label>}
        {changes.grouping_row !== undefined && changes.grouping_row !== original.grouping_row && <small>{t("Save the source choice, then review the updated cells.")}</small>}{draft?.error && <p role="alert" className="product-cell-error">{t(draft.error)}</p>}
        {draft?.latest && <button disabled={busy} onClick={() => { setDrafts(c => { const n = { ...c }; delete n[row.id]; return n; }); if (dirty === 1) setShowChanges(false); setRefresh(v => v + 1); }}>{t("Reload this row")}</button>}
        {isNew && <div className="product-new-actions"><button className="primary-button" disabled={busy || !row.t_code.trim() || ![row.name_th, row.name_vi, row.name_en].some(name => name?.trim())} onClick={create}>{t("Save new product")}</button><button disabled={busy} onClick={() => { setAdding(null); setNewChanges({}); }}>{t("Cancel new row")}</button></div>}
      </td>
      <td><div className="product-temperature-cell">
        <select data-guide={guiding && !row.temperature ? t(`Choose the temperature for ${prefix}`) : undefined} data-guide-detail={t("Cold assigns foam. Normal assigns cardboard.")} data-guide-priority="15" disabled={busy} aria-label={t(`${prefix} box and temperature`)} value={row.temperature || ""} onChange={e => { const temperature = e.target.value; edit(original, { temperature, box_material: temperature === "COLD" ? "FOAM" : "CARDBOARD" }); }}><option value="" disabled>{t("Choose temperature")}</option><option value="COLD">{t("Cold / Foam")}</option><option value="NORMAL">{t("Normal (ambient) / Cardboard")}</option></select>
        {row.temperature ? <span className={`product-material-badge ${row.temperature === "COLD" ? "is-cold" : "is-ambient"}`}>{row.temperature === "COLD" ? <Snowflake size={14} aria-hidden /> : <ThermometerSun size={14} aria-hidden />}{row.temperature === "COLD" ? t("Foam box") : t("Cardboard box")}</span> : <small className="product-cell-error">{t("Temperature needs review. Choose a value to assign the box.")}</small>}
        <small className="product-temperature-source">{changes.temperature || row.temperature_source === "STAFF" ? t("Staff selection") : row.temperature_source === "SAVED" ? t("Previously saved temperature") : row.source_choices.length ? t(`Grouping list ${row.grouping_row ? `row ${row.grouping_row}` : row.source_choices.length === 1 ? `row ${row.source_choices[0].row}` : `rows ${row.source_choices.map(choice => choice.row).join(", ")}`}`) : t("No matching product in the grouping list")}</small>
        {row.foam_below_kg != null && <small className="product-cell-error">{t("Existing weight exception: foam below ")}{row.foam_below_kg} {t("kg. ")}<button type="button" disabled={busy} onClick={() => edit(original, { foam_below_kg: null })}>{t("Use temperature rule only")}</button></small>}
      </div></td>
      <td><label className="product-el"><input type="checkbox" data-guide={guiding && row.control_status === null ? t(`Confirm EL or Non-EL for ${prefix}`) : undefined} data-guide-priority="20" aria-label={t(`${prefix} EL classification`)} aria-checked={row.control_status === null ? "mixed" : row.control_status === "EL"} ref={node => { if (node) node.indeterminate = row.control_status === null; }} disabled={busy} checked={row.control_status === "EL"} onChange={e => edit(original, { control_status: e.target.checked ? "EL" : "COMBINABLE" })} />{row.control_status === null ? t("Unresolved") : row.control_status === "EL" ? t("EL") : t("Non-EL")}</label>{row.control_status === null && <button disabled={busy} onClick={() => edit(original, { control_status: "COMBINABLE" })}>{t("Set Non-EL")}</button>}</td>
      <td><div className="product-handling">{select("Group", "produce_type", categories)}{select("Weight class", "weight_class", ["LIGHT", "HEAVY"])}{select("Firmness", "firmness", ["SOFT", "SEMI_HARD", "HARD"])}</div>{!row.firmness && <small className="product-cell-error">{t("Choose firmness")}</small>}</td>
      <td><label>{t("Planning basis")}<select aria-label={t(`${prefix} planning basis`)} disabled={busy} value={row.planning_basis || "VOLUME"} onChange={e => edit(original, { planning_basis: e.target.value as "VOLUME" | "HISTORY" })}><option value="VOLUME">{t("Volume estimate")}</option><option value="HISTORY">{t("Reviewed packing history")}</option></select></label>{row.planning_basis === "HISTORY" ? <small>{t("Exact, approved box combinations are required in Packing history. L/kg is optional.")}</small> : <><label className="product-volume"><input type="number" data-guide={guiding && !row.volume_l_per_kg ? t(`Enter occupied litres per kg for ${prefix}`) : undefined} data-guide-priority="20" aria-label={t(`${prefix} litres per kg`)} disabled={busy} min=".000001" max="10000" step="any" value={row.volume_l_per_kg ?? ""} placeholder={t("Not entered")} onChange={e => edit(original, { volume_l_per_kg: e.target.value ? Number(e.target.value) : null })} /><span>{t("L/kg")}</span></label><small>{row.volume_estimated && !("volume_l_per_kg" in changes) ? t("Estimated") : t("Staff value")}</small>{!row.volume_l_per_kg && <small className="product-cell-error">{t("Volume needed")}</small>}</>}</td>
      <td><div className="product-limits">{row.limits.map((l, i) => { const b = boxes.find(b => b.id === l.box_catalogue_id); return <label key={l.box_catalogue_id}><span>{b ? `${label(b.box_type)} / ${b.size_name}` : t("Box")}</span><input type="number" min=".001" step="any" disabled={busy} aria-label={t(`${prefix} ${b?.size_name || "box"} limit kg`)} value={l.max_net_weight_kg} onChange={e => edit(original, { limits: row.limits.map((item, n) => n === i ? { ...item, max_net_weight_kg: Number(e.target.value) } : item) })} /><span>{t("kg")}</span></label>; })}</div>
        {!row.limits.length && <small>{t("Catalogue capacity applies")}</small>}
        <label>{t("Add a single-product limit")}<select aria-label={t(`${prefix} add box limit`)} disabled={busy || !boxes.some(box => box.box_type === row.box_material && !row.limits.some(limit => limit.box_catalogue_id === box.id))} value="" onChange={e => { const selected = boxes.find(box => box.id === e.target.value); if (selected) edit(original, { limits: [...row.limits, { box_catalogue_id: selected.id, max_net_weight_kg: selected.max_safe_net_weight_kg }] }); }}><option value="">{t("Choose a box")}</option>{boxes.filter(box => box.box_type === row.box_material && !row.limits.some(limit => limit.box_catalogue_id === box.id)).map(box => <option key={box.id} value={box.id}>{box.size_name}</option>)}</select></label>
        {row.limit_issues.map((issue, i) => <div className="product-limit-issue" key={i}><small lang="th">{issue.note}</small>{issue.max_net_weight_kg !== null && <select aria-label={t(`${prefix} map box note ${i + 1}`)} disabled={busy} value={changes.limit_resolutions?.[issue.note] || ""} onChange={e => { const id = e.target.value; if (id) edit(original, { limit_resolutions: { ...changes.limit_resolutions, [issue.note]: id } }); }}><option value="">{t("Map to approved box")}</option>{boxes.filter(b => b.box_type === row.box_material).map(b => <option key={b.id} value={b.id}>{b.size_name} ({b.length_cm} × {b.width_cm} × {b.height_cm})</option>)}</select>}</div>)}
        {!!row.limit_issues.length && <label className="product-confirm"><input type="checkbox" disabled={busy} checked={Boolean(changes.limits_guidance_only)} onChange={e => edit(original, { limits_guidance_only: e.target.checked })} />{t("Keep unmapped notes as guidance")}</label>}
        <small>{t("Single-product boxes only")}</small>
      </td>
    </tr>;
  }
  return <div className={`page packing-setup product-workspace ${historyStyles.workspace}`}>
    <header className="page-header split-header product-page-heading">
      <div className="product-heading-main"><h1>{t("Products & boxes")}</h1><div className="packing-setup-tabs" role="tablist" aria-label={t("Packing setup sections")}><button role="tab" aria-selected={tab === "products"} onClick={() => setTab("products")}>{t("Products")}</button><button role="tab" aria-selected={tab === "boxes"} onClick={() => setTab("boxes")}>{t("Box catalogue")}</button><button role="tab" aria-selected={tab === "history"} onClick={() => setTab("history")}>{t("Packing history")}</button></div></div>
      <div className="product-header-actions"><span data-workspace-guide-host /><Link className="invoice-secondary-button" href="/packing/orders">{t("Packing orders")}</Link>
        {tab === "products" && <button className="primary-button" data-guide={t("Save the reviewed product settings")} data-guide-priority={Object.values(drafts).some(draft => draft.changes.grouping_row !== undefined) ? "5" : "40"} disabled={!dirty || busy} onClick={save}><Save size={16} aria-hidden />{busy ? t("Saving...") : t("Save changes")}</button>}
      </div>
    </header>
    <StatusMessage error={error} success={message} />
    {tab === "history" ? <PackingHistoryPanel boxes={boxes} /> : tab === "products" ? <section aria-label={t("Product packing table")}>
      <div className="product-table-toolbar">
        <label>{t("Find product")}<input data-guide={t("Find the product to review")} data-guide-priority="90" data-guide-detail={t("Search by SKU or name, then check the packing settings in its row.")} type="search" placeholder={t("SKU, Thai, Vietnamese or English name")} value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} /></label>
        <label>{t("Temperature")}<select value={temperature} onChange={e => { setTemperature(e.target.value); setOffset(0); }}>
          <option value="all">{t("All temperatures")}{data?.summary.total !== undefined ? ` (${data.summary.total})` : ""}</option>
          <option value="COLD">{t("Cold / Foam")}{data?.summary.cold !== undefined ? ` (${data.summary.cold})` : ""}</option>
          <option value="NORMAL">{t("Normal / Cardboard")}{data?.summary.ambient !== undefined ? ` (${data.summary.ambient})` : ""}</option>
          <option value="unknown">{t("Needs temperature review")}{data?.summary.temperature_unknown !== undefined ? ` (${data.summary.temperature_unknown})` : ""}</option>
        </select></label>
        <label>{t("Status")}<select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="all">{t("All products")}</option><option value="review">{t("Needs review")}</option><option value="ready">{t("Confirmed")}</option></select></label>
        <label>{t("EL classification")}<select value={el} onChange={e => { setEl(e.target.value); setOffset(0); }}><option value="all">{t("All classifications")}</option><option value="EL">{t("EL")}</option><option value="COMBINABLE">{t("Non-EL")}</option><option value="unknown">{t("Unresolved")}</option></select></label>
        <button className="invoice-secondary-button" disabled={busy || !!adding} onClick={() => setAdding(empty())}><Plus size={16} aria-hidden />{t("Add product")}</button>
      </div>
      <div className="product-grid-scroll" role="region" aria-label={t("Editable product worksheet")} tabIndex={0} aria-busy={loaded !== key}><table className="product-grid"><caption className="sr-only">{t("Six packing settings per product. Scroll horizontally to see all columns on a small screen.")}</caption><thead><tr>{columns.map(c => <th scope="col" key={c.label}>{t(c.label)}{c.label === "Volume" && <small>{t("Occupied space per kg")}</small>}</th>)}</tr></thead><tbody>{adding && renderRow(adding)}{loaded !== key ? <TableSkeletonRows label={t("Loading product settings…")} columns={columns} /> : (showChanges ? Object.values(drafts).map(d => d.row) : data?.items)?.map(renderRow)}</tbody></table></div>
      {loaded === key && !error && !data?.items.length && <p>{t("No products match these filters.")}</p>}
      <div className="product-table-footer">
        <div className="product-save-status"><span role="status">{dirty ? t(`${dirty} unsaved row${dirty === 1 ? "" : "s"}`) : t("All changes saved")}{adding ? t(" | New product draft") : ""}</span>
          {(dirty > 0 || showChanges) && <button disabled={busy} onClick={() => setShowChanges(v => !v)}>{showChanges ? t("Show all products") : t("Show unsaved rows")}</button>}
        </div>
        <div className="register-pagination"><span>{data?.total || 0} {t("products | Page ")}{Math.floor(offset / 25) + 1}</span><button disabled={!offset || busy} onClick={() => setOffset(v => Math.max(0, v - 25))}>{t("Previous")}</button><button disabled={!data?.has_more || busy} onClick={() => setOffset(v => v + 25)}>{t("Next")}</button><button disabled={busy} onClick={() => setRefresh(v => v + 1)}>{t("Refresh")}</button></div>
      </div>
    </section> : <section aria-label={t("Box catalogue")}><div className="packing-setup-table" aria-busy={loaded !== key}><table><thead><tr><th>{t("Box type")}</th><th>{t("External dimensions (cm)")}</th><th>{t("Packing space estimate")}</th><th>{t("Safe net weight")}</th><th>{t("Status")}</th><th>{t("Action")}</th></tr></thead><tbody>{loaded !== key ? <TableSkeletonRows label={"Loading box catalogue" + String.fromCharCode(8230)} columns={["Box type", "External dimensions", "Packing space estimate", "Safe net weight", "Status", "Action"].map(label => ({ label }))} /> : boxes.map(b => <tr key={b.id}><td>{b.size_name}<small>{label(b.box_type)}</small></td><td>{b.length_cm} × {b.width_cm} × {b.height_cm}</td><td>{b.usable_volume_cm3 != null ? t(`${(b.usable_volume_cm3 / 1000).toFixed(2)} L`) : t("Needs review")}</td><td>{b.max_safe_net_weight_kg} {t("kg")}</td><td>{b.packing_reviewed ? t("Reviewed") : t("Needs review")}</td><td><button aria-label={t(`Edit box ${b.size_name} ${b.box_type}`)} onClick={() => setBox(b)}>{t("Edit")}</button></td></tr>)}</tbody></table></div></section>}
    {box && <BoxEditor initial={box} onClose={() => setBox(null)} onSaved={() => { setBox(null); setMessage("Box capacity saved."); setRefresh(v => v + 1); }} />}
  </div>;
}
