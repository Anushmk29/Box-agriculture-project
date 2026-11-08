"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { isRevisionConflict } from "@/lib/api-errors";
import { confirmAction } from "@/lib/confirm";
import { t, useLanguage } from "@/lib/language";
import { StatusMessage } from "@/components/status-message";
import { TaskDialog } from "@/components/task-dialog";

export type PackingItem = {
  item_id: string; product_id: string; product_code: string; produce_name: string;
  pack_size: string; packed_count: number; net_weight_kg: number;
};
type Product = { id: string; t_code: string; name_en: string; name_vi: string; name_th?: string };

export function PackingItemEditor({ boxId, revision, item, onClose, onSaved, onConflict, onReload }: {
  boxId: string; revision: number; item?: PackingItem; onClose: () => void;
  onSaved: () => Promise<void>; onConflict: () => void; onReload: () => void | Promise<void>;
}) {
  useLanguage();
  const [search, setSearch] = useState(item?.product_code || "");
  const [offset, setOffset] = useState(0), [retry, setRetry] = useState(0);
  const [products, setProducts] = useState<Product[]>([]);
  const [selected, setSelected] = useState<Product | null>(item ? { id: item.product_id, t_code: item.product_code, name_en: item.produce_name, name_vi: "" } : null);
  const [pack, setPack] = useState(item?.pack_size || "100g");
  const [quantity, setQuantity] = useState(String(item?.packed_count ?? 1));
  const [weight, setWeight] = useState(String(item?.net_weight_kg ?? 0.1));
  const [searchBusy, setSearchBusy] = useState(true), [searchError, setSearchError] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const saving = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearchBusy(true); setSearchError("");
      api<Product[]>(`/master-data/products?q=${encodeURIComponent(search)}&limit=100&offset=${offset}`, { signal: controller.signal })
        .then(value => { if (!controller.signal.aborted) setProducts(value); })
        .catch(reason => { if (!controller.signal.aborted) setSearchError(reason instanceof Error ? reason.message : "Unable to search products"); })
        .finally(() => { if (!controller.signal.aborted) setSearchBusy(false); });
    }, 250);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [search, offset, retry]);

  async function save(remove = false) {
    if (!selected || saving.current || conflict) return;
    if (!remove && (!quantity.trim() || !Number.isInteger(Number(quantity)) || Number(quantity) < (item ? 0 : 1) || !pack.trim() || !weight.trim() || !Number.isFinite(Number(weight)) || Number(weight) < 0 || (Number(quantity) > 0 && Number(weight) <= 0))) {
      setError("Enter a whole pack count and a positive total net weight. Use zero packs to remove an item."); return;
    }
    saving.current = true; setBusy(true); setError("");
    try {
      await api(`/packing/boxes/${boxId}/items`, { method: "PUT", body: JSON.stringify({
        expected_revision: revision, item_id: item?.item_id, product_id: remove ? item!.product_id : selected.id,
        pack_size: remove ? item!.pack_size : pack, quantity: remove ? 0 : Number(quantity),
        net_weight_kg: remove || Number(quantity) === 0 ? 0 : Number(weight),
      }) }, { background: true });
      await onSaved();
    } catch (reason) {
      if (isRevisionConflict(reason)) { setConflict(true); onConflict(); }
      setError(reason instanceof Error ? reason.message : "Unable to save item");
    } finally { saving.current = false; setBusy(false); }
  }

  async function remove() {
    if (!item || !await confirmAction({ title: "Remove this packing item?", body: "Remove this product from the box if it is out of stock or was not packed. Review the remaining contents before finalizing.", confirmLabel: "Remove item", tone: "danger" })) return;
    await save(true);
  }

  function changeSearch(value: string) { setSearch(value); setOffset(0); setProducts([]); setSearchBusy(true); setSearchError(""); }
  return <TaskDialog title={item ? t("Adjust packing item") : t("Add packing item")} description={t("Choose the product, pack size and actual quantity in this box. Review the updated contents before finalizing packing.")} busy={busy} onClose={onClose}>
    <StatusMessage error={error} />
    {conflict && <button type="button" disabled={busy} onClick={() => void onReload()}>{t("Reload latest packing for review")}</button>}
    <form className="dialog-form" onSubmit={event => { event.preventDefault(); void save(); }}>
      <label>{t("Find product")}<input value={search} disabled={busy} onChange={event => changeSearch(event.target.value)} /></label>
      <StatusMessage error={searchError} />
      {searchError && <button type="button" disabled={busy || searchBusy} onClick={() => setRetry(value => value + 1)}>{t("Retry search")}</button>}
      <div className="register-pagination"><button type="button" disabled={busy || !offset || searchBusy} onClick={() => { setOffset(value => Math.max(0, value - 100)); setSearchBusy(true); }}>{t("Previous products")}</button><span>{t("Products")} {products.length ? `${offset + 1}–${offset + products.length}` : "0"}</span><button type="button" disabled={busy || searchBusy || products.length < 100} onClick={() => { setOffset(value => value + 100); setSearchBusy(true); }}>{t("Next products")}</button></div>
      {searchBusy && <p role="status">{t("Searching products...")}</p>}
      {!searchBusy && !searchError && !products.length && <p>{t("No matching products. Try a partial name or SKU.")}</p>}
      <label>{t("Master product")}<select required disabled={busy || conflict} value={selected?.id || ""} onChange={event => setSelected(products.find(product => product.id === event.target.value) || null)}>
        <option value="">{t("Choose product")}</option>{selected && !products.some(product => product.id === selected.id) && <option value={selected.id}>{selected.t_code} · {selected.name_en || selected.name_vi}</option>}{products.map(product => <option value={product.id} key={product.id}>{product.t_code} · {product.name_en || product.name_vi}{product.name_th ? ` · ${product.name_th}` : ""}</option>)}
      </select></label>
      <label>{t("Pack size")}<input required maxLength={64} disabled={busy || conflict} value={pack} onChange={event => setPack(event.target.value)} /></label>
      <label>{t("Packed quantity")}<input type="number" required min={item ? 0 : 1} step={1} disabled={busy || conflict} value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
      <label>{t("Total item net weight (kg)")}<input type="number" required min={Number(quantity) > 0 ? 0.0001 : 0} step="0.0001" disabled={busy || conflict} value={weight} onChange={event => setWeight(event.target.value)} /></label>
      {item && <p>{t("Quantity zero removes this item. Replacing the master product records a substitution.")}</p>}
      <p>{t("Box totals are calculated from the saved items when packing is finalized.")}</p>
      <div className="task-dialog-actions"><button type="submit" className="primary-button" disabled={busy || conflict || !selected}>{busy ? t("Saving…") : t("Save packing adjustment")}</button>{item && <button type="button" className="danger-button" disabled={busy || conflict} onClick={() => void remove()}>{t("Remove item")}</button>}<button type="button" disabled={busy} onClick={onClose}>{t("Cancel")}</button></div>
    </form>
  </TaskDialog>;
}
