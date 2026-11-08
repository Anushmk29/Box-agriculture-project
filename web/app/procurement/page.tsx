"use client";

import { t, useLanguage } from "@/lib/language";

import { PageLoadingSkeleton, TableLoadingSkeleton } from "@/components/loading-skeleton";

import { Suspense, useCallback, useState, useEffect, useRef, useMemo } from "react";
import {
  AlertTriangle,
  ArrowLeftRight,
  ArrowRight,
  Building2,
  CalendarDays,
  CheckCircle2,
  Download,
  Search,
  ShoppingBag,
  Sliders,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { RegisterBackLink } from "@/components/record-register";
import { api, apiBlob } from "@/lib/api";
import { PRODUCT_CATEGORIES } from "@/lib/product-categories";
import { StatusMessage } from "@/components/status-message";
import { TaskDialog } from "@/components/task-dialog";
import { scopedPacking } from "@/lib/workflow";
import { useSession } from "@/lib/session-context";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/empty-state";
import { confirmAction } from "@/lib/confirm";
import { useGuidanceHints } from "@/lib/guidance-hints";
import styles from "./procurement.module.css";

type PurchasingItem = {
  item_id: string;
  product_id: string;
  product_code: string;
  product_name: string;
  produce_type?: string | null;
  pack_size: string;
  ordered_pack_count: number;
  pack_count_pending?: boolean;
  ordered_weight_kg: number;
  buffer_percentage: number;
  buffer_pack_count: number;
  buffer_weight_kg: number;
  total_purchase_target_packs: number;
  total_purchase_target_weight_kg: number;
  required_packs: number;
  required_weight_kg: number;
  confirmed_purchased_pack_count?: number | null;
  confirmed_purchased_weight_kg?: number | null;
  shortage_reason?: string | null;
  replacement_product_id?: string | null;
  replacement_product_code?: string | null;
  replacement_product_name?: string | null;
};

type MasterPurchasingBatch = {
  batch_id: string;
  batch_reference: string;
  status?: string | null;
  substitutions_supported?: boolean;
  created_at?: string | null;
  source_files?: { file_id: string; file_name: string; customer: string; shipment_date: string; route: string }[];
  total_skus: number;
  total_ordered_weight_kg: number;
  total_buffer_weight_kg: number;
  total_required_weight_kg: number;
  items: PurchasingItem[];
};

type MasterProduct = { id: string; t_code: string; name_en: string; name_vi: string };

function formatKg(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

export default function ProcurementPage() {
  useLanguage(); return <Suspense fallback={<PageLoadingSkeleton label={t("Loading procurement…")} />}><ProcurementContent /></Suspense>; }

function ProcurementContent() {
  useLanguage();
  const requestedBatchId = useSearchParams().get("batchId");
  const loadedBatchId = useRef("");
  const [showBatchDetails, setShowBatchDetails] = useState(false);
  const [batch, setBatch] = useState<MasterPurchasingBatch | null>(null);
  const batchFileNames = [...new Set(batch?.source_files?.map(source => source.file_name))].join(", ") || "Selected order file";
  const [confirmationBaseline, setConfirmationBaseline] = useState("");
  const [confirmedPacks, setConfirmedPacks] = useState<Record<string, number>>({});
  const [confirmedWeights, setConfirmedWeights] = useState<Record<string, number>>({});
  const [substitutions, setSubstitutions] = useState<Record<string, MasterProduct>>({});
  const [substitutionItem, setSubstitutionItem] = useState<PurchasingItem | null>(null);
  const [substitutionSearch, setSubstitutionSearch] = useState("");
  const [substitutionOffset, setSubstitutionOffset] = useState(0);
  const [substitutionProducts, setSubstitutionProducts] = useState<MasterProduct[]>([]);
  const [substitutionLoading, setSubstitutionLoading] = useState(false);
  const [substitutionError, setSubstitutionError] = useState("");
  const [loading, setLoading] = useState(false);
  const batchRequest = useRef(0);
  const confirmRequest = useRef(false);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Filters & Views
  const [activeFilter, setActiveFilter] = useState<"all" | "attention" | "pending">("all");
  const [searchQuery, setSearchQuery] = useState("");

  // Buffer Adjustment Modal
  const [editingItem, setEditingItem] = useState<PurchasingItem | null>(null);
  const [newBufferPct, setNewBufferPct] = useState<number>(10);
  const [bufferBusy, setBufferBusy] = useState(false);

  // Bulk Buffer Modal
  const [showBulkBufferModal, setShowBulkBufferModal] = useState(false);
  const [bulkBufferPct, setBulkBufferPct] = useState<number>(10);
  const [bulkBufferBusy, setBulkBufferBusy] = useState(false);
  const session = useSession();
  const permissions = useMemo(() => session?.permissions ?? [], [session]);
  const [purchaseCategory, setPurchaseCategory] = useState("");
  const [mergeSearch, setMergeSearch] = useState("");
  const [mergeOffset, setMergeOffset] = useState(0);
  const [mergeProducts, setMergeProducts] = useState<MasterProduct[]>([]);
  const [mergeSource, setMergeSource] = useState<MasterProduct | null>(null);
  const [mergeTarget, setMergeTarget] = useState<MasterProduct | null>(null);
  const [mergeReviewed, setMergeReviewed] = useState(false);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [purchaseTask, setPurchaseTask] = useState<"merge" | null>(null);
  const canEditBuffers = permissions.includes("edit_buffers");
  const purchasesSent = batch?.status === "HANDOFF_01_SENT";
  const canEnterPurchases = permissions.includes("confirm_procurement") && batch?.status === "PURCHASING_CONSOLIDATED";
  const canSubstitute = canEnterPurchases && batch?.substitutions_supported === true;
  const purchasesComplete = !!batch?.items.length && batch.items.every(it =>
    Number.isInteger(confirmedPacks[it.item_id]) && confirmedPacks[it.item_id] >= 0 &&
    Number.isFinite(confirmedWeights[it.item_id]) && confirmedWeights[it.item_id] >= 0 &&
    (confirmedPacks[it.item_id] === 0) === (Math.round(confirmedWeights[it.item_id] * 10000) === 0)
  );

  const purchasesDirty = Boolean(batch && confirmationBaseline && JSON.stringify([confirmedPacks, confirmedWeights, substitutions]) !== confirmationBaseline);
  useUnsavedChanges(purchasesDirty);

  useEffect(() => {
    if (!substitutionItem || !canSubstitute) return;
    let active = true;
    const timer = window.setTimeout(() => {
      api<MasterProduct[]>(`/master-data/products?q=${encodeURIComponent(substitutionSearch)}&limit=50&offset=${substitutionOffset}`)
        .then(products => { if (active) setSubstitutionProducts(products); })
        .catch(reason => { if (active) setSubstitutionError(reason instanceof Error ? reason.message : "Unable to load substitutes"); })
        .finally(() => { if (active) setSubstitutionLoading(false); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [substitutionItem, substitutionSearch, substitutionOffset, canSubstitute]);


  useEffect(() => {
    if (!permissions.includes("merge_products")) return;
    let active = true;
    const timer = window.setTimeout(() => {
      api<MasterProduct[]>(`/master-data/products?q=${encodeURIComponent(mergeSearch)}&limit=100&offset=${mergeOffset}`)
        .then(products => { if (active) setMergeProducts(products); }).catch(reason => { if (active) setError(String(reason)); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [mergeSearch, mergeOffset, permissions]);

  async function mergeSelectedProducts() {
    if (!mergeSource || !mergeTarget || !mergeReviewed) return;
    setMergeBusy(true); setError("");
    try {
      await api("/master-data/products/merge", { method: "POST", body: JSON.stringify({ source_id: mergeSource.id, target_id: mergeTarget.id }) });
      setSuccess(`${mergeSource.t_code} merged into ${mergeTarget.t_code}. Pending demand was recalculated.`);
      setMergeSource(null); setMergeTarget(null); setMergeReviewed(false);
      setMergeProducts(await api<MasterProduct[]>(`/master-data/products?q=${encodeURIComponent(mergeSearch)}&limit=100&offset=${mergeOffset}`));
      if (batch) await loadBatchById(batch.batch_id);
      setPurchaseTask(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to merge products"); }
    finally { setMergeBusy(false); }
  }

  const initializeConfirmations = useCallback((items: PurchasingItem[], status?: string | null) => {
    const packs: Record<string, number> = {};
    const wts: Record<string, number> = {};
    const replacements: Record<string, MasterProduct> = {};
    for (const it of items) {
      if (it.replacement_product_id) replacements[it.item_id] = {
        id: it.replacement_product_id, t_code: it.replacement_product_code ?? "",
        name_en: it.replacement_product_name ?? "Replacement", name_vi: "",
      };
      if (status !== "PURCHASING_CONSOLIDATED" && it.confirmed_purchased_pack_count !== null && it.confirmed_purchased_pack_count !== undefined) {
        packs[it.item_id] = it.confirmed_purchased_pack_count;
        if (it.confirmed_purchased_weight_kg != null) wts[it.item_id] = it.confirmed_purchased_weight_kg;
      } else if (status === "PURCHASING_CONSOLIDATED") {
        if (!it.pack_count_pending) packs[it.item_id] = it.total_purchase_target_packs;
        wts[it.item_id] = it.total_purchase_target_weight_kg;
      }
    }
    setConfirmationBaseline(JSON.stringify([packs, wts, replacements]));
    setConfirmedPacks(packs);
    setConfirmedWeights(wts);
    setSubstitutions(replacements);
    setSubstitutionItem(null);
  }, []);

  const fetchBatch = useCallback(async (refOrId: string) => {
    if (!refOrId.trim() || confirmRequest.current) return;
    const request = ++batchRequest.current;
    setLoading(true);
    setBatch(null);
    setActiveFilter("all");
    setSearchQuery("");
    setError("");
    setSuccess("");
    try {
      const data = await api<MasterPurchasingBatch>(`/procurement/batches/${encodeURIComponent(refOrId.trim())}`);
      if (request !== batchRequest.current) return;
      loadedBatchId.current = data.batch_id;
      setBatch(data);
      const params = new URLSearchParams(window.location.search); params.set("batchId", data.batch_id); window.history.replaceState(null, "", `/procurement?${params}`);
      initializeConfirmations(data.items, data.status);

    } catch (reason) {
      if (request === batchRequest.current) setError(reason instanceof Error ? reason.message : "Failed to load procurement batch");
    } finally {
      if (request === batchRequest.current) setLoading(false);
    }
  }, [initializeConfirmations]);

  async function loadBatchById(refOrId: string) {
    if (purchasesDirty && !await confirmAction({ title: "Discard purchase changes?", body: "Your unsaved packs and kilograms will be lost when the batch reloads.", confirmLabel: "Discard and reload", tone: "danger" })) return;
    await fetchBatch(refOrId);
  }

  const cancelBatchRead = useCallback(() => { batchRequest.current++; }, []);
  useEffect(() => {
    if (requestedBatchId && requestedBatchId !== loadedBatchId.current) void fetchBatch(requestedBatchId);
    return cancelBatchRead;
  }, [requestedBatchId, fetchBatch, cancelBatchRead]);

  // Auto-fill all matching targets
  function handleAutoFillAllTargets() {
    if (!batch || !canEnterPurchases) return;
    const newPacks: Record<string, number> = { ...confirmedPacks };
    const newWts: Record<string, number> = { ...confirmedWeights };
    for (const it of batch.items) {
      if (!it.pack_count_pending) newPacks[it.item_id] = it.total_purchase_target_packs;
      newWts[it.item_id] = it.total_purchase_target_weight_kg;
    }
    setConfirmedPacks(newPacks);
    setConfirmedWeights(newWts);
    setSuccess("Filled target quantities for all produce lines in batch.");
  }

  // Confirm purchases to backend API
  async function confirmPurchases(e?: React.FormEvent) {
    if (e) e.preventDefault();
    if (!batch || confirmRequest.current || !permissions.includes("confirm_procurement")) return;
    if (batch.status === "PURCHASING_CONSOLIDATED" && !purchasesComplete) {
      const invalid = batch.items.filter(item => !Number.isInteger(confirmedPacks[item.item_id]) || confirmedPacks[item.item_id] < 0 || !Number.isFinite(confirmedWeights[item.item_id]) || confirmedWeights[item.item_id] < 0 || (confirmedPacks[item.item_id] === 0) !== (Math.round(confirmedWeights[item.item_id] * 10000) === 0));
      setActiveFilter("pending");
      setError(`Purchase confirmation cannot be completed. ${invalid.length} products require attention: ${invalid.map(item => `${item.product_code} ${item.product_name} (${!Number.isInteger(confirmedPacks[item.item_id]) || confirmedPacks[item.item_id] < 0 ? "pack count" : "purchase weight"})`).join("; ")}. Correct the highlighted pending products.`);
      return;
    }
    confirmRequest.current = true;
    setConfirmBusy(true);
    setError("");
    setSuccess("");

    const confirmations = batch.items.map(it => ({
      item_id: it.item_id,
      purchased_pack_count: confirmedPacks[it.item_id],
      purchased_weight_kg: confirmedWeights[it.item_id],
      ...(substitutions[it.item_id] ? { replacement_product_id: substitutions[it.item_id].id } : {}),
    }));

    try {
      const result = await api<{ status: string; batch_reference: string }>(
        `/procurement/batches/${batch.batch_id}/confirm`,
        {
          method: "PUT",
          body: JSON.stringify({ items: confirmations }),
        }
      );
      setBatch(current => current?.batch_id === batch.batch_id ? { ...current, status: result.status } : current);
      setConfirmationBaseline(JSON.stringify([confirmedPacks, confirmedWeights, substitutions]));
      setSuccess(`Purchases confirmed for ${batchFileNames} and sent to packing. Continue to the packing floor.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Confirmation failed");
    } finally {
      confirmRequest.current = false;
      setConfirmBusy(false);
    }
  }

  // Open Talad Thai Buyer Sheet
  async function openBuyerSheet() {
    if (!batch) return;
    setError("");
    try {
      const blob = await apiBlob(`/procurement/batches/${batch.batch_id}/buyer-sheet?format=html${purchaseCategory ? `&category=${purchaseCategory}` : ""}`);
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Buyer sheet could not be opened");
    }
  }

  // Update Single Buffer %
  async function updateBuffer(e: React.FormEvent) {
    e.preventDefault();
    if (!editingItem || !batch) return;
    setBufferBusy(true);
    try {
      await api(`/procurement/items/${editingItem.item_id}/buffer`, {
        method: "PUT",
        body: JSON.stringify({
          buffer_percentage: Number(newBufferPct),
          save_as_default: true,
        }),
      });
      setSuccess(`Updated buffer to ${newBufferPct}% for ${editingItem.product_code}`);
      setEditingItem(null);
      await loadBatchById(batch.batch_reference);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to update buffer percentage");
    } finally {
      setBufferBusy(false);
    }
  }

  // Bulk Apply Buffer %
  async function applyBulkBuffer(e: React.FormEvent) {
    e.preventDefault();
    if (!batch) return;
    setBulkBufferBusy(true);
    setError("");
    try {
      await api(`/procurement/batches/${batch.batch_id}/buffer`, {
        method: "PUT",
        body: JSON.stringify({ buffer_percentage: Number(bulkBufferPct) }),
      });
      setSuccess(`Applied ${bulkBufferPct}% buffer across all ${batch.items.length} produce lines.`);
      setShowBulkBufferModal(false);
      await loadBatchById(batch.batch_reference);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to apply bulk buffer");
    } finally {
      setBulkBufferBusy(false);
    }
  }

  // Each item's purchase status drives the filter tabs and the row badges.
  const { itemStatuses, statusCounts } = useMemo(() => {
    const statuses: Record<string, "exact" | "under" | "over" | "pending"> = {};
    const counts = { attention: 0, pending: 0 };
    for (const it of batch?.items ?? []) {
      const p = confirmedPacks[it.item_id];
      const w = confirmedWeights[it.item_id];
      if (!Number.isInteger(p) || p < 0 || !Number.isFinite(w) || w < 0 || (p === 0) !== (Math.round(w * 10000) === 0)) { statuses[it.item_id] = "pending"; counts.pending++; }
      else if ((it.pack_count_pending || p === it.total_purchase_target_packs) && w === it.total_purchase_target_weight_kg) { statuses[it.item_id] = "exact"; }
      else if ((!it.pack_count_pending && p < it.total_purchase_target_packs) || w < it.total_purchase_target_weight_kg) { statuses[it.item_id] = "under"; counts.attention++; }
      else { statuses[it.item_id] = "over"; counts.attention++; }
    }
    return { itemStatuses: statuses, statusCounts: counts };
  }, [batch, confirmedPacks, confirmedWeights]);

  // Filtered and searched items
  const filteredItems = useMemo(() => {
    if (!batch || !batch.items) return [];

    return batch.items.filter(it => {
      if (purchaseCategory && it.produce_type !== purchaseCategory) return false;
      // Filter tab
      const status = itemStatuses[it.item_id];
      if (activeFilter === "attention" && status !== "under" && status !== "over") return false;
      if (activeFilter === "pending" && status !== "pending") return false;

      // Search query
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase().trim();
        const matchName = it.product_name.toLowerCase().includes(query);
        const matchCode = it.product_code.toLowerCase().includes(query);
        const matchSize = it.pack_size.toLowerCase().includes(query);
        const matchType = (it.produce_type || "").toLowerCase().includes(query);
        if (!matchName && !matchCode && !matchSize && !matchType) return false;
      }

      return true;
    });
  }, [batch, activeFilter, searchQuery, itemStatuses, purchaseCategory]);


  return (
    <div className={styles.workspace}>
      <PageHeader title={<span className={styles.pageTitle}><span className={styles.titleIcon}><ShoppingBag size={24} strokeWidth={1.7} aria-hidden /></span>{t("Purchasing")}</span>} description={t("Check what you bought, then send it to packing.")}
        back={batch ? <RegisterBackLink fallback="/procurement/batches" /> : undefined}
        actions={batch || loading ? <div className={styles.headerActions}>
          {batch && <><button type="button" className="invoice-secondary-button" onClick={openBuyerSheet}><Download size={16} aria-hidden />{t("Export Buyer Sheet")}</button>
            <button type="button" className={styles.textButton} onClick={() => setShowBatchDetails(true)}>{t("Batch details")}</button></>}
          <Link className={styles.textButton} href="/procurement/batches">{t("Choose batch")}</Link>
        </div> : undefined} />
      {batch && <><div className={styles.batchOverview}>
        <div className={styles.batchContext}>
          <strong>{batchFileNames}</strong>
          {batch.source_files?.length ? <><span>{[...new Set(batch.source_files.map(source => source.customer))].join(", ")}</span><span className={styles.shipment}><CalendarDays size={14} aria-hidden />{[...new Set(batch.source_files.map(source => `${source.shipment_date} · ${source.route}`))].join(" / ")}</span></>
            : <strong><Building2 size={16} aria-hidden />{batch.items.length} {t("products in this batch")}</strong>}
        </div>
        <dl className={styles.totals} aria-label={t("Purchase quantities")}>
          <div><dt>{t("Ordered")}</dt><dd>{formatKg(batch.total_ordered_weight_kg)} <span>{t("kg")}</span></dd></div>
          <div><dt>{t("Trimming buffer")}</dt><dd>{formatKg(batch.total_buffer_weight_kg)} <span>{t("kg")}</span></dd></div>
          <div><dt>{t("To buy")}</dt><dd>{formatKg(batch.total_required_weight_kg)} <span>{t("kg")}</span></dd></div>
        </dl>
        </div>
        {purchasesSent && <section className={styles.sent} aria-labelledby="packing-next-step">
          <CheckCircle2 size={24} aria-hidden />
          <div className={styles.sentCopy}>
            <span>{t("Purchasing complete")}</span>
            <h2 id="packing-next-step">{t("Next: Packing")}</h2>
            <p>{t("Open this batch’s orders to start packing.")}</p>
          </div>
          <PackingNextAction batchId={batch.batch_id} />
        </section>}
      </>}
      {batch && showBatchDetails && <TaskDialog title={t("Batch details")} description={t("Uploaded order files and shipment details.")} guidance={false} onClose={() => setShowBatchDetails(false)}>
        {batch.source_files?.length ? <div className={styles.sourceFiles}><table className="batch-table"><thead><tr><th>{t("Client")}</th><th>{t("Order file")}</th><th>{t("Shipment")}</th><th>{t("Route")}</th></tr></thead><tbody>{batch.source_files.map(source => <tr key={source.file_id}><td>{source.customer}</td><td>{source.file_name}</td><td>{source.shipment_date}</td><td>{source.route}</td></tr>)}</tbody></table></div> : <p>{t("No source files listed.")}</p>}
      </TaskDialog>}
      <StatusMessage error={error} success={success} />
      {purchaseTask === "merge" && permissions.includes("merge_products") && <TaskDialog title={t("Merge duplicate products")} description={t("Choose the duplicate and the master product to keep. Pending quantities will be combined. Confirmed packing and invoices are preserved.")} busy={mergeBusy} onClose={() => setPurchaseTask(null)}>
        <StatusMessage error={error} /><div className="register-pagination"><button type="button" disabled={!mergeOffset} onClick={() => setMergeOffset(value => Math.max(0, value - 100))}>{t("Previous products")}</button><span>{t("Products ")}{mergeOffset + 1}–{mergeOffset + mergeProducts.length}</span><button type="button" disabled={mergeProducts.length < 100} onClick={() => setMergeOffset(value => value + 100)}>{t("Next products")}</button></div>
        <label>{t("Find products ")}<input value={mergeSearch} onChange={event => { setMergeSearch(event.target.value); setMergeOffset(0); }} /></label>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", margin: "12px 0" }}>
          <label>{t("Duplicate to merge ")}<select value={mergeSource?.id || ""} onChange={event => { setMergeSource(mergeProducts.find(p => p.id === event.target.value) || null); setMergeReviewed(false); }}>
            <option value="">{t("Select duplicate SKU")}</option>{mergeSource && !mergeProducts.some(p => p.id === mergeSource.id) && <option value={mergeSource.id}>{mergeSource.t_code} · {mergeSource.name_en}</option>}
            {mergeProducts.map(product => <option value={product.id} key={product.id}>{product.t_code} · {product.name_en || product.name_vi}</option>)}
          </select></label>
          <label>{t("Keep this master SKU ")}<select value={mergeTarget?.id || ""} onChange={event => { setMergeTarget(mergeProducts.find(p => p.id === event.target.value) || null); setMergeReviewed(false); }}>
            <option value="">{t("Select master SKU")}</option>{mergeTarget && !mergeProducts.some(p => p.id === mergeTarget.id) && <option value={mergeTarget.id}>{mergeTarget.t_code} · {mergeTarget.name_en}</option>}
            {mergeProducts.map(product => <option value={product.id} key={product.id}>{product.t_code} · {product.name_en || product.name_vi}</option>)}
          </select></label>
        </div>
        {mergeSource && mergeTarget && <p>{mergeSource.t_code} ({mergeSource.name_en}) → {mergeTarget.t_code} ({mergeTarget.name_en}{t("). Pending order quantities will be combined and purchasing totals rebuilt.")}</p>}
        <label><input type="checkbox" checked={mergeReviewed} onChange={event => setMergeReviewed(event.target.checked)} /> {t("I reviewed these SKUs and they are the same product.")}</label>{" "}
        <button type="button" className="primary-button" disabled={mergeBusy || !mergeReviewed || !mergeSource || !mergeTarget || mergeSource.id === mergeTarget.id} onClick={mergeSelectedProducts}>{t("Merge duplicate SKU")}</button>
      </TaskDialog>}

      {batch ? <section className={styles.purchaseList} aria-label={t("Purchase list")} data-purchase-state={purchasesSent ? "complete" : "pending"}>
        <div className={styles.toolbar}>
          <div className={styles.filters} role="group" aria-label={t("Purchase status")}>
            <button type="button" aria-pressed={activeFilter === "all"} onClick={() => setActiveFilter("all")}>{t("All Items ")}<span>{batch.items.length}</span></button>
            <button type="button" aria-pressed={activeFilter === "pending"} onClick={() => setActiveFilter("pending")}>{t("Pending Input ")}<span>{statusCounts.pending}</span></button>
            {(statusCounts.attention > 0 || activeFilter === "attention") && <button type="button" aria-pressed={activeFilter === "attention"} onClick={() => setActiveFilter("attention")}>{t("Needs attention ")}<span>{statusCounts.attention}</span></button>}
          </div>
          <div className={styles.findProducts}>
            <label className={styles.search}><Search size={17} aria-hidden /><span className="sr-only">{t("Search products")}</span><input type="search" placeholder={t("Search product or code")} value={searchQuery} onChange={event => setSearchQuery(event.target.value)} /></label>
            <label><span className="sr-only">{t("Buyer sheet category")}</span><select value={purchaseCategory} onChange={event => setPurchaseCategory(event.target.value)}><option value="">{t("All categories")}</option>{PRODUCT_CATEGORIES.map(category => <option key={category.value} value={category.value}>{t(category.label)}</option>)}</select></label>
          </div>
        </div>
        <div className={styles.tableActions}>
          <span>{purchasesSent ? t("Confirmed purchase quantities. Continue to packing for the next step.") : t("Enter the actual packs and kilograms purchased.")}</span>
          {!purchasesSent && <div>
            <button type="button" className={styles.textButton} disabled={!canEditBuffers || batch.status !== "PURCHASING_CONSOLIDATED"} onClick={() => setShowBulkBufferModal(true)}><Sliders size={15} aria-hidden />{t("Adjust buffers")}</button>
            <button type="button" className={styles.textButton} disabled={!canEnterPurchases || confirmBusy} onClick={handleAutoFillAllTargets}>{t("Fill actuals from targets")}</button>
          </div>}
        </div>
        <form id="purchase-quantities" aria-busy={confirmBusy} onSubmit={confirmPurchases}>
          <div className={styles.tableScroll} tabIndex={0} role="region" aria-label={t("Purchase quantities table")}>
            <table className={styles.table}>
              <colgroup><col className={styles.productColumn} /><col className={styles.quantityColumn} /><col className={styles.bufferColumn} /><col className={styles.quantityColumn} /><col className={styles.actualColumn} /></colgroup>
              <thead><tr><th scope="col">{t("Product")}</th><th scope="col">{t("Ordered")}</th><th scope="col">{t("Buffer")}</th><th scope="col">{t("To buy")}</th><th scope="col">{t("Actual purchase")}</th></tr></thead>
              <tbody>{filteredItems.map(it => {
                const currentPackInput = confirmedPacks[it.item_id] ?? "";
                const currentWeightInput = confirmedWeights[it.item_id] ?? "";
                const isPending = itemStatuses[it.item_id] === "pending";
                const isUnder = itemStatuses[it.item_id] === "under";
                const isOver = itemStatuses[it.item_id] === "over";
                const packsInvalid = !Number.isInteger(currentPackInput) || Number(currentPackInput) < 0;
                const weightInvalid = typeof currentWeightInput !== "number" || !Number.isFinite(currentWeightInput) || currentWeightInput < 0;
                const packDifference = Number(currentPackInput) - it.total_purchase_target_packs;
                const weightDifference = Number(currentWeightInput) - it.total_purchase_target_weight_kg;
                const difference = [
                  !it.pack_count_pending && (isUnder ? packDifference < 0 : packDifference > 0) ? `${Math.abs(packDifference)} packs` : "",
                  (isUnder ? weightDifference < 0 : weightDifference > 0) ? `${formatKg(Math.abs(weightDifference))} kg` : "",
                ].filter(Boolean).join(" / ");
                return <tr key={it.item_id} data-purchase-status={itemStatuses[it.item_id]}>
                  <td data-label={t("Product")} className={styles.product}>
                    <strong>{it.product_name}</strong>
                    <p className={styles.productMeta}>{it.product_code}<span aria-hidden> · </span>{it.pack_size}</p>
                    {substitutions[it.item_id] && <p className={styles.substitute}>{t("Buyer-approved substitute: ")}<strong>{substitutions[it.item_id].t_code} {substitutions[it.item_id].name_en}</strong></p>}
                    {canSubstitute && <div className={styles.rowTools}><button type="button" className={styles.textButton} disabled={confirmBusy} aria-label={t(`Choose substitute for ${it.product_code}`)}
                      onClick={() => { setSubstitutionSearch(""); setSubstitutionOffset(0); setSubstitutionProducts([]); setSubstitutionError(""); setSubstitutionLoading(true); setSubstitutionItem(it); }}><ArrowLeftRight size={12} aria-hidden />{substitutions[it.item_id] ? t("Change substitute") : t("Substitute")}</button>
                      {substitutions[it.item_id] && <button type="button" className={styles.textButton} disabled={confirmBusy} aria-label={t(`Remove substitute for ${it.product_code}`)} onClick={() => setSubstitutions(current => { const next = { ...current }; delete next[it.item_id]; return next; })}>{t("Use original product")}</button>}
                    </div>}
                  </td>
                  <td data-label={t("Ordered")} className={styles.quantity}><strong>{formatKg(it.ordered_weight_kg)} {t("kg")}</strong><small>{it.pack_count_pending ? it.ordered_pack_count > 0 ? t(`${it.ordered_pack_count} packs + weight orders`) : t("Ordered by weight") : t(`${it.ordered_pack_count} packs`)}</small></td>
                  <td data-label={t("Buffer")} className={styles.buffer}>
                    <button type="button" className={styles.bufferButton} title={t("Edit buffer percentage")} aria-label={t(`Edit buffer for ${it.product_code}`)} disabled={!canEditBuffers || batch.status !== "PURCHASING_CONSOLIDATED"} onClick={() => { setEditingItem(it); setNewBufferPct(it.buffer_percentage); }}>{it.buffer_percentage}%</button>
                    <small>+{formatKg(it.buffer_weight_kg)} {t("kg")}</small>
                  </td>
                  <td data-label={t("To buy")} className={`${styles.quantity} ${styles.target}`}><strong>{formatKg(it.total_purchase_target_weight_kg)} {t("kg")}</strong><small>{it.pack_count_pending ? t("Record actual packs") : t(`${it.total_purchase_target_packs} packs`)}</small></td>
                  <td data-label={t("Actual purchase")} className={styles.actual}>
                    <div className={styles.inputs}>
                      <label><span>{t("Packs")}</span><input type="number" step="1" min="0" value={currentPackInput} title={t("Confirmed Pack Count")} aria-label={t(`Actual packs for ${it.product_code}`)} aria-invalid={isPending && packsInvalid} disabled={!canEnterPurchases || confirmBusy} placeholder="—" onChange={event => setConfirmedPacks(prev => { const next = { ...prev }; if (event.target.value === "") delete next[it.item_id]; else next[it.item_id] = Number(event.target.value); return next; })} /></label>
                      <label><span>{t("Kilograms")}</span><input type="number" step="0.0001" min="0" value={currentWeightInput} title={t("Confirmed Total Weight (kg)")} aria-label={t(`Actual kilograms for ${it.product_code}`)} aria-invalid={isPending && weightInvalid} disabled={!canEnterPurchases || confirmBusy} placeholder="—" onChange={event => setConfirmedWeights(prev => { const next = { ...prev }; if (event.target.value === "") delete next[it.item_id]; else next[it.item_id] = Number(event.target.value); return next; })} /></label>
                    </div>
                    {isPending && <p className={styles.pending}>{packsInvalid ? t("Enter actual packs") : t("Check actual quantities")}</p>}
                    {isUnder && <p className={styles.warning}><AlertTriangle size={14} aria-hidden /><span>{t("Needs Attention")}</span> · {difference} {t("below target")}</p>}
                    {isOver && <p className={styles.difference}>{difference} {t("above target")}</p>}
                  </td>
                </tr>;
              })}</tbody>
            </table>
            {!filteredItems.length && <p className={styles.noResults}>{t("No products in this view.")}{(searchQuery || purchaseCategory) && <button type="button" className={styles.textButton} onClick={() => { setSearchQuery(""); setPurchaseCategory(""); }}>{t("Clear search and category")}</button>}</p>}
          </div>
          <div className={styles.confirmBar}>
            <p>{purchasesSent ? <><CheckCircle2 size={17} aria-hidden />{t("Purchasing complete · Next: Packing")}</> : statusCounts.pending ? <><strong>{statusCounts.pending} {t("products")}</strong> {t("still need quantities")}</> : <><CheckCircle2 size={17} aria-hidden />{t("Quantities ready to confirm")}</>}</p>
            {purchasesSent ? <PackingNextAction batchId={batch.batch_id} />
              : <button type="submit" disabled={!permissions.includes("confirm_procurement") || !["PURCHASING_CONSOLIDATED", "PROCUREMENT_CONFIRMED"].includes(batch.status || "") || confirmBusy} className="primary-button">{confirmBusy ? t("Confirming…") : t("Confirm purchases and send to packing")}<ArrowRight size={17} aria-hidden /></button>}
          </div>
        </form>
      </section> : loading ? <TableLoadingSkeleton label={t("Loading selected batch")} columns={["Product", "Ordered", "Buffer", "To buy", "Actual purchase"]} />
        : <EmptyState icon={ShoppingBag} title={t("Choose a purchase batch")} actions={<><Link className="primary-button" href="/procurement/batches">{t("Choose batch")}</Link><Link className="invoice-secondary-button" href="/orders">{t("Upload an order")}</Link></>}>{t("Open a batch to review quantities and send purchases to packing.")}</EmptyState>}
      {permissions.includes("merge_products") && <details className="task-toolbar" aria-label={t("Purchase batch tools")}><summary>{t("Batch preparation and catalog tools")}</summary>
        <div><strong>{t("Review your product catalogue")}</strong><p>{t("Resolve duplicate products before buying.")}</p></div>
        <div className="task-toolbar-actions">
          <button type="button" onClick={() => { setError(""); setPurchaseTask("merge"); }}>{t("Merge duplicate products")}</button>
        </div>
      </details>}
      {substitutionItem && <TaskDialog title={t("Choose substitute")} description={t(`Replace ${substitutionItem.product_code} ${substitutionItem.product_name} for this purchase. The buyer approves the change. Keep the same pack size; the substitute's selling price will apply.`)}
        onClose={() => setSubstitutionItem(null)}>
        <label>{t("Search substitutes")}<input type="search" value={substitutionSearch} onChange={event => {
          setSubstitutionSearch(event.target.value); setSubstitutionOffset(0); setSubstitutionProducts([]); setSubstitutionError(""); setSubstitutionLoading(true);
        }} placeholder={t("Product code or name")} /></label>
        {substitutionError && <p role="alert">{substitutionError}</p>}
        {substitutionLoading ? <p role="status">{t("Loading substitutes…")}</p> : <ul>
          {substitutionProducts.filter(product => product.id !== substitutionItem.product_id).map(product => <li key={product.id}>
            <button type="button" onClick={() => {
              setSubstitutions(current => ({ ...current, [substitutionItem.item_id]: product })); setSubstitutionItem(null);
            }}>{t("Approve ")}{product.t_code} {product.name_en || product.name_vi}</button>
          </li>)}
        </ul>}
        {!substitutionLoading && !substitutionError && !substitutionProducts.some(product => product.id !== substitutionItem.product_id) && <p>{t("No matching substitutes. Try another search.")}</p>}
        <div className="task-toolbar-actions">
          <button type="button" disabled={substitutionLoading || substitutionOffset === 0} onClick={() => { setSubstitutionOffset(value => Math.max(0, value - 50)); setSubstitutionLoading(true); setSubstitutionError(""); }}>{t("Previous substitutes")}</button>
          <button type="button" disabled={substitutionLoading || substitutionProducts.length < 50} onClick={() => { setSubstitutionOffset(value => value + 50); setSubstitutionLoading(true); setSubstitutionError(""); }}>{t("Next substitutes")}</button>
        </div>
      </TaskDialog>}

      {editingItem && <BufferDialog title={t("Adjust trimming buffer")} description={t(`Extra stock bought to cover trimming loss for ${editingItem.product_code} (${editingItem.product_name}).`)}
        value={newBufferPct} onChange={setNewBufferPct} busy={bufferBusy} submitLabel="Save buffer" onSubmit={updateBuffer} onClose={() => setEditingItem(null)} />}
      {showBulkBufferModal && <BufferDialog title={t("Adjust buffer for all products")} description={t(`Apply one trimming buffer to all ${batch?.items.length ?? 0} products in this batch and recalculate what to buy.`)}
        value={bulkBufferPct} onChange={setBulkBufferPct} busy={bulkBufferBusy} submitLabel="Apply to batch" onSubmit={applyBulkBuffer} onClose={() => setShowBulkBufferModal(false)} />}
    </div>
  );
}

function PackingNextAction({ batchId }: { batchId: string }) {
  useLanguage();
  const ref = useRef<HTMLAnchorElement>(null);
  const { enabled } = useGuidanceHints();

  useEffect(() => {
    const link = ref.current;
    if (!link) return;
    let inView = false;
    const update = () => { link.dataset.nextActionActive = String(inView && !document.hidden); };
    const observer = new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; update(); });
    observer.observe(link);
    document.addEventListener("visibilitychange", update);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", update); };
  }, []);

  return <Link ref={ref} className={`primary-button ${styles.nextAction}`} href={scopedPacking(batchId)}
    data-next-action-hints={enabled} title={t("Next step: open this batch’s orders for packing")}>
    {t("Continue to packing")}<ArrowRight size={17} aria-hidden />
  </Link>;
}

// One form for the per-product and whole-batch trimming buffer.
function BufferDialog({ title, description, value, onChange, busy, submitLabel, onSubmit, onClose }: {
  title: string; description: string; value: number; onChange: (value: number) => void; busy: boolean;
  submitLabel: string; onSubmit: (event: React.FormEvent) => void; onClose: () => void;
}) {
  useLanguage();
  return <TaskDialog title={t(title)} description={t(description)} size="sm" busy={busy} onClose={onClose}>
    <form className="task-dialog-pad" onSubmit={onSubmit}>
      <label>{t("Buffer (%)")}<input type="number" step="1" min="0" max="100" value={value} onChange={event => onChange(Number(event.target.value))} required /></label>
      <div className="task-dialog-actions"><button type="button" className="invoice-secondary-button" onClick={onClose}>{t("Cancel")}</button>
        <button type="submit" className="primary-button" disabled={busy}>{busy ? t("Saving…") : submitLabel}</button></div>
    </form>
  </TaskDialog>;
}
