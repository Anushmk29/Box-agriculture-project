"use client";

import { t, useLanguage } from "@/lib/language";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { StatusMessage } from "@/components/status-message";
import { TaskDialog } from "@/components/task-dialog";
import type { Box } from "@/components/packing-box-editor";
import styles from "@/components/packing-history.module.css";

type Product = { id: string; t_code: string; name_th: string; name_vi: string; name_en: string };
type RecipeItem = { product_id: string; pack_size: string; pack_count: number | null; net_weight_kg: number | null };
type SourceItem = RecipeItem & {
  code: string; name_th: string; name_vi: string; name_en: string; raw_weight: number | null;
  raw_pack_quantity: number | null; issues: string[]; candidates: Product[];
  matching: { status: string; matched_languages: string[]; missing_languages: string[]; conflicting_languages: string[] };
};
type Example = {
  id: string; shipment: string; subcustomer: string; box_number: number; box_name: string;
  source: string; pages: number[]; delivery_date: string; issues: string[]; items: SourceItem[];
  box_catalogue_id: string; review: null | { status: string; revision: number; note: string; source_current: boolean; approval_issues: string[];
    definition: { route_code?: string; packing_mode?: "EL" | "NON_EL"; box_catalogue_id?: string; items?: RecipeItem[] } };
};
type HistoryPage = { source_version: string; total: number; has_more: boolean; items: Example[];
  summary: { label_pages: number; examples: number; items: number; approved: number; examples_needing_source_review: number } };
type Outcomes = { observations: number; boxes: number; outcomes: Record<string, number>; population: string };

const names = (product: { name_th?: string; name_vi?: string; name_en?: string }) =>
  [product.name_th, product.name_vi, product.name_en].filter(name => name?.trim()).join(" / ");

function ProductChoice({ source, value, disabled, onChange }: {
  source: SourceItem; value: string; disabled: boolean; onChange: (id: string) => void;
}) {
  useLanguage();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Product[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!query.trim()) return;
    let active = true;
    const timer = setTimeout(() => {
      api<Product[]>(`/master-data/products?q=${encodeURIComponent(query)}&limit=100`)
        .then(rows => { if (active) { setFound(rows); setError(""); } })
        .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "Unable to search products"); });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [query]);
  const options = [...new Map([...source.candidates, ...found].map(product => [product.id, product])).values()];
  return <div className={styles.choice}>
    <label>{t("Find master product")}<input disabled={disabled} value={query} placeholder={t("SKU, Thai, Vietnamese or English")} onChange={event => setQuery(event.target.value)} /></label>
    <label>{t("Matched product")}<select required disabled={disabled} value={value} onChange={event => onChange(event.target.value)}>
      <option value="">{t("Choose a product")}</option>
      {value && !options.some(product => product.id === value) && <option value={value}>{t("Previously reviewed product")}</option>}
      {options.map(product => <option key={product.id} value={product.id}>{product.t_code} · {names(product)}</option>)}
    </select></label>
    {error && <small role="alert">{t(error)}</small>}
  </div>;
}

function HistoryReview({ example, sourceVersion, boxes, onClose, onSaved }: {
  example: Example; sourceVersion: string; boxes: Box[]; onClose: () => void; onSaved: () => void;
}) {
  useLanguage();
  const [items, setItems] = useState<RecipeItem[]>(() => example.review?.definition.items || example.items.map(item => ({
    product_id: item.product_id, pack_size: item.pack_size, pack_count: item.pack_count, net_weight_kg: item.net_weight_kg,
  })));
  const [boxId, setBoxId] = useState(example.review?.definition.box_catalogue_id || example.box_catalogue_id);
  const [route, setRoute] = useState(example.review?.definition.route_code || example.shipment.split(" ")[0].toUpperCase());
  const [mode, setMode] = useState(example.review?.definition.packing_mode || "EL");
  const [note, setNote] = useState(example.review?.note || "");
  const [fit, setFit] = useState(false), [resolved, setResolved] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  useUnsavedChanges(true);
  const issues = [...example.issues, ...example.items.flatMap((item, index) => item.issues.map(issue => `Line ${index + 1}: ${issue}`))];
  function edit(index: number, patch: Partial<RecipeItem>) {
    setItems(current => current.map((item, position) => position === index ? { ...item, ...patch } : item));
    setFit(false);
  }
  async function save(status: "APPROVED" | "REJECTED") {
    setBusy(true); setError("");
    try {
      await api(`/packing-history/${example.id}/review`, { method: "PUT", body: JSON.stringify({
        source_version: sourceVersion, expected_revision: example.review?.revision || 0, status,
        box_catalogue_id: boxId, route_code: route, packing_mode: mode, items,
        verified_fit: fit, source_issues_acknowledged: resolved, note,
      }) });
      onSaved();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save the review"); }
    finally { setBusy(false); }
  }
  return <TaskDialog title={t(`Review ${example.subcustomer} · box ${example.box_number}`)} size="lg" busy={busy}
    description={t("Check product identities, physical pack counts and actual net kilograms. Approval applies to this exact combination and route.")} onClose={onClose}>
    <StatusMessage error={error} />
    {example.review?.approval_issues?.length ? <p role="alert">{example.review.approval_issues.join(". ")}{t(". Review this combination again.")}</p> : null}
    <p>{example.source} {t("· pages ")}{example.pages.join(", ")} {t("· delivery ")}{example.delivery_date}</p>
    {issues.length > 0 && <details open><summary>{issues.length} {t("source issues to resolve")}</summary><ul>{issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></details>}
    <div className={styles.settings}>
      <label>{t("Box type")}<select disabled={busy} value={boxId} onChange={event => { setBoxId(event.target.value); setFit(false); }}><option value="">{t("Choose an approved box")}</option>{boxes.map(box => <option key={box.id} value={box.id}>{box.size_name} · {box.box_type}{!box.packing_reviewed ? t(" · needs capacity review") : ""}</option>)}</select></label>
      <label>{t("Applicable route")}<input maxLength={32} disabled={busy} value={route} onChange={event => { setRoute(event.target.value); setFit(false); }} /></label>
      <label>{t("Packing rules")}<select disabled={busy} value={mode} onChange={event => { setMode(event.target.value as "EL" | "NON_EL"); setFit(false); }}><option value="EL">{t("EL rules")}</option><option value="NON_EL">{t("Non-EL rules")}</option></select></label>
    </div>
    <div className={styles.items}>{example.items.map((source, index) => <fieldset key={index}>
      <legend>{t("Line ")}{index + 1} · {source.code} · {names(source)}</legend>
      <p>{t("Source: Weight ")}{source.raw_weight ?? t("missing")}{t(", pack label ")}{source.pack_size || t("missing")}{t(", Pack column ")}{source.raw_pack_quantity ?? t("missing")}.</p>
      <small>{t("Matching: ")}{source.matching.status.toLowerCase()}{t("; languages matched: ")}{source.matching.matched_languages.join(", ") || t("none")}{t("; unavailable: ")}{source.matching.missing_languages.join(", ") || t("none")}.{source.matching.conflicting_languages.length > 0 && t(` Conflicting: ${source.matching.conflicting_languages.join(", ")}.`)}</small>
      <ProductChoice source={source} value={items[index]?.product_id || ""} disabled={busy} onChange={id => edit(index, { product_id: id })} />
      <div className={styles.settings}>
        <label>{t("Physical pack format")}<input disabled={busy} maxLength={64} value={items[index]?.pack_size || ""} placeholder={t("100g bag or 1pc")} onChange={event => edit(index, { pack_size: event.target.value })} /></label>
        <label>{t("Actual pack count")}<input disabled={busy} type="number" min="1" step="1" value={items[index]?.pack_count ?? ""} onChange={event => edit(index, { pack_count: event.target.value ? Number(event.target.value) : null })} /></label>
        <label>{t("Total net kg")}<input disabled={busy} type="number" min="0.0001" step="any" value={items[index]?.net_weight_kg ?? ""} onChange={event => edit(index, { net_weight_kg: event.target.value ? Number(event.target.value) : null })} /></label>
      </div>
    </fieldset>)}</div>
    <label>{t("Review note")}<textarea disabled={busy} rows={3} maxLength={2048} value={note} onChange={event => setNote(event.target.value)} placeholder={t("Record how identities, quantities and any source issues were checked.")} /></label>
    {issues.length > 0 && <label className={styles.check}><input type="checkbox" disabled={busy} checked={resolved} onChange={event => setResolved(event.target.checked)} />{t("I resolved the source issues and explained the corrections above.")}</label>}
    <label className={styles.check}><input type="checkbox" disabled={busy} checked={fit} onChange={event => setFit(event.target.checked)} />{t("Packing staff physically verified this exact combination, box and protection requirements.")}</label>
    <p className="task-note">{t("Confirm product settings before approving this example. A recorded box load does not establish a maximum capacity or physical layer order.")}</p>
    <div className="packing-setup-actions"><button type="button" disabled={busy} onClick={onClose}>{t("Cancel")}</button><button type="button" disabled={busy || note.trim().length < 8} onClick={() => void save("REJECTED")}>{t("Reject example")}</button><button type="button" className="primary-button" disabled={busy || !fit || !boxId || !route.trim() || note.trim().length < 8 || (issues.length > 0 && !resolved) || items.some(item => !item.product_id || !item.pack_count || !item.net_weight_kg || !item.pack_size.trim())} onClick={() => void save("APPROVED")}>{busy ? t("Saving…") : t("Approve checked combination")}</button></div>
  </TaskDialog>;
}

export function PackingHistoryPanel({ boxes }: { boxes: Box[] }) {
  useLanguage();
  const [query, setQuery] = useState(""), [status, setStatus] = useState("unreviewed"), [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0), [data, setData] = useState<HistoryPage | null>(null);
  const [outcomes, setOutcomes] = useState<Outcomes | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Example | null>(null);
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      setLoading(true); setError("");
      Promise.all([api<HistoryPage>(`/packing-history?q=${encodeURIComponent(query)}&status=${status}&offset=${offset}&limit=25`), api<Outcomes>("/packing-history/feedback/summary")])
        .then(([page, feedback]) => { if (active) { setData(page); setOutcomes(feedback); } })
        .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "Unable to load packing history"); })
        .finally(() => { if (active) setLoading(false); });
    }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [query, status, offset, refresh]);
  return <section aria-label={t("Packing history")} className={styles.panel}>
    <p>{t("Review documented box contents before using them as packing guidance. Thai, Vietnamese and English names are checked when available.")}</p>
    <StatusMessage error={error} success={message} />
    {data && <p>{data.summary.examples} {t("candidate boxes · ")}{data.summary.label_pages} {t("label pages · ")}{data.summary.approved} {t("approved combinations · ")}{data.summary.examples_needing_source_review} {t("with source issues")}</p>}
    <div className={styles.settings}>
      <label>{t("Find a packing example")}<input value={query} placeholder={t("Product, Thai, Vietnamese, English or customer")} onChange={event => { setQuery(event.target.value); setOffset(0); }} /></label>
      <label>{t("Review status")}<select value={status} onChange={event => { setStatus(event.target.value); setOffset(0); }}><option value="unreviewed">{t("Needs review")}</option><option value="APPROVED">{t("Approved")}</option><option value="REJECTED">{t("Rejected")}</option><option value="all">{t("All examples")}</option></select></label>
    </div>
    <div className={`${styles.scroll} packing-setup-table`} aria-busy={loading}><table><thead><tr><th>{t("Shipment / customer")}</th><th>{t("Box")}</th><th>{t("Documented contents")}</th><th>{t("Review")}</th></tr></thead><tbody>
      {loading ? <tr><td colSpan={4}>{t("Loading packing examples…")}</td></tr> : data?.items.map(example => <tr key={example.id}>
        <td>{example.shipment}<small>{example.subcustomer} · {example.delivery_date}</small></td>
        <td>{example.box_number} · {example.box_name}<small>{t("Source pages ")}{example.pages.join(", ")}</small></td>
        <td>{example.items.slice(0, 3).map((item, index) => <div key={index}>{names(item)} · {item.pack_size}</div>)}{example.items.length > 3 && <small>+{example.items.length - 3} {t("more products")}</small>}</td>
        <td><span>{example.review?.source_current && !example.review.approval_issues?.length ? t(example.review.status.toLowerCase()) : t("Needs review")}</span><button type="button" onClick={() => setSelected(example)}>{t("Review box ")}{example.box_number}</button></td>
      </tr>)}
      {!loading && data?.items.length === 0 && <tr><td colSpan={4}>{t("No examples match these filters.")}</td></tr>}
    </tbody></table></div>
    <div className="register-pagination"><span>{data?.total || 0} {t("examples · Page ")}{Math.floor(offset / 25) + 1}</span><button disabled={!offset || loading} onClick={() => setOffset(value => Math.max(0, value - 25))}>{t("Previous")}</button><button disabled={!data?.has_more || loading} onClick={() => setOffset(value => value + 25)}>{t("Next")}</button></div>
    {outcomes && <details><summary>{t("Packing feedback · ")}{outcomes.observations} {t("observations from ")}{outcomes.boxes} {t("boxes")}</summary><p>{outcomes.population}.</p><ul>{Object.entries(outcomes.outcomes).map(([outcome, count]) => <li key={outcome}>{t(outcome.toLowerCase().replaceAll("_", " "))}: {count}</li>)}</ul></details>}
    {selected && data && <HistoryReview key={selected.id} example={selected} sourceVersion={data.source_version} boxes={boxes} onClose={() => setSelected(null)} onSaved={() => { setSelected(null); setMessage("Packing example review saved."); setRefresh(value => value + 1); }} />}
  </section>;
}
