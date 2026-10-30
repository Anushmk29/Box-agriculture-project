"use client";

import { PageLoadingSkeleton, TableLoadingSkeleton } from "@/components/loading-skeleton";

import { Suspense, useCallback, useState, useEffect, useRef, useMemo } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Download,
  Search,
  ShoppingBag,
  Sliders,
  Verified,
  Zap
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { RegisterBackLink } from "@/components/record-register";
import { api, apiBlob } from "@/lib/api";
import { PRODUCT_CATEGORIES } from "@/lib/product-categories";
import { StatusMessage } from "@/components/status-message";
import { TaskDialog } from "@/components/task-dialog";
import { TaskPanel, WorkflowProgress } from "@/components/workflow-guidance";
import { scopedPacking } from "@/lib/workflow";
import { useSession } from "@/lib/session-context";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/empty-state";
import { confirmAction } from "@/lib/confirm";

type PurchasingItem = {
  item_id: string;
  product_id: string;
  product_code: string;
  product_name: string;
  produce_type?: string | null;
  pack_size: string;
  ordered_pack_count: number;
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

function getProduceEmoji(name: string, type?: string | null): string {
  const lower = `${name} ${type || ""}`.toLowerCase();
  if (lower.includes("chili") || lower.includes("ớt")) return "🌶️";
  if (lower.includes("krachai") || lower.includes("củ ngải") || lower.includes("gừng") || lower.includes("riềng") || lower.includes("root")) return "🫚";
  if (lower.includes("durian") || lower.includes("sầu riêng")) return "🍈";
  if (lower.includes("mango") || lower.includes("xoài")) return "🥭";
  if (lower.includes("basil") || lower.includes("húng") || lower.includes("chà ôm") || lower.includes("acacia") || lower.includes("herb")) return "🌿";
  if (lower.includes("morning glory") || lower.includes("muống") || lower.includes("leafy")) return "🥬";
  return "📦";
}

function getCategoryDisplay(item: PurchasingItem): string {
  const type = (item.produce_type || "").toUpperCase();
  const category = PRODUCT_CATEGORIES.find(category => category.value === type);
  if (category) return category.label;
  const lower = `${item.product_name} ${item.product_code}`.toLowerCase();
  if (type === "HERB" || lower.includes("herb") || lower.includes("basil") || lower.includes("chà ôm") || lower.includes("ngò")) {
    return "Herbs";
  }
  if (type === "ROOT" || lower.includes("root") || lower.includes("krachai") || lower.includes("gừng") || lower.includes("củ")) {
    return "Roots";
  }
  if (type === "FRUIT" || lower.includes("fruit") || lower.includes("mango") || lower.includes("durian") || lower.includes("xoài")) {
    return "Fruits";
  }
  if (type === "LEAFY" || lower.includes("leafy") || lower.includes("muống") || lower.includes("cải")) {
    return "Leafy";
  }
  return "General Produce";
}

export default function ProcurementPage() { return <Suspense fallback={<PageLoadingSkeleton label="Loading procurement…" />}><ProcurementContent /></Suspense>; }

function ProcurementContent() {
  const requestedBatchId = useSearchParams().get("batchId");
  const loadedBatchId = useRef("");
  const [batchIdInput, setBatchIdInput] = useState("");
  const [reviewedBatch, setReviewedBatch] = useState("");
  const [batch, setBatch] = useState<MasterPurchasingBatch | null>(null);
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
  const [activeFilter, setActiveFilter] = useState<"all" | "attention" | "pending" | "confirmed">("all");
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
        packs[it.item_id] = it.total_purchase_target_packs;
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
      setBatchIdInput(data.batch_reference);
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

  // 1-Click Match Single Target
  function handleMatchSingleTarget(item: PurchasingItem) {
    if (!canEnterPurchases) return;
    setConfirmedPacks(prev => ({ ...prev, [item.item_id]: item.total_purchase_target_packs }));
    setConfirmedWeights(prev => ({ ...prev, [item.item_id]: item.total_purchase_target_weight_kg }));
  }

  // Auto-fill all matching targets
  function handleAutoFillAllTargets() {
    if (!batch || !canEnterPurchases) return;
    const newPacks: Record<string, number> = { ...confirmedPacks };
    const newWts: Record<string, number> = { ...confirmedWeights };
    for (const it of batch.items) {
      newPacks[it.item_id] = it.total_purchase_target_packs;
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
      setSuccess(`Purchases confirmed for ${result.batch_reference} and sent to packing. Continue to the packing floor.`);
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
    const counts = { confirmed: 0, attention: 0, pending: 0 };
    for (const it of batch?.items ?? []) {
      const p = confirmedPacks[it.item_id];
      const w = confirmedWeights[it.item_id];
      if (!Number.isInteger(p) || p < 0 || !Number.isFinite(w) || w < 0 || (p === 0) !== (Math.round(w * 10000) === 0)) { statuses[it.item_id] = "pending"; counts.pending++; }
      else if (p === it.total_purchase_target_packs && w === it.total_purchase_target_weight_kg) { statuses[it.item_id] = "exact"; counts.confirmed++; }
      else if (p < it.total_purchase_target_packs || w < it.total_purchase_target_weight_kg) { statuses[it.item_id] = "under"; counts.attention++; }
      else { statuses[it.item_id] = "over"; counts.confirmed++; }
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
      if (activeFilter === "confirmed" && status !== "exact" && status !== "over") return false;

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
    <div className="page purchase-workspace" style={{ maxWidth: "1680px" }}>
      <PageHeader eyebrow="Stage 2 · Buyer desk" title="Purchasing" description="Enter what was actually bought at the market, then confirm and send it to packing."
        back={batch ? <RegisterBackLink fallback="/procurement/batches" /> : undefined}
        actions={batch || loading ? <Link className="invoice-secondary-button" href="/procurement/batches">Choose batch</Link> : undefined} />
      <WorkflowProgress current="procurement" done={Boolean(batch)} links={batch?.status === "HANDOFF_01_SENT" ? { packing: scopedPacking(batch.batch_id) } : {}} />
      {batch && <div className="purchase-summary"><TaskPanel reference={[batch.batch_reference, ...new Set(batch.source_files?.map(source => `${source.shipment_date} · ${source.route}`) || [])].join(" · ")}
        title={batch.status === "HANDOFF_01_SENT" ? "Purchases sent to packing" : "Review actual purchases"}
        detail={batch.status === "HANDOFF_01_SENT" ? "The packing team can prepare these customer orders. Continue with another purchase batch or view the linked packing work." : "Check actual packs and kilograms for every product. Confirmation saves the purchases and sends them to packing together."}
        nextTeam="Packing team" progress={`${batch.items.length} products · ${statusCounts.attention} under target`}
        primary={batch.status === "HANDOFF_01_SENT" ? { label: "View related packing orders", href: scopedPacking(batch.batch_id) } : { label: "Review purchase quantities", href: "#purchase-quantities" }}
        secondary={batch.status === "HANDOFF_01_SENT" ? <Link href="/procurement/batches">Next purchase batch</Link> : undefined} />
        <dl className="purchase-totals" aria-label="Purchase quantities">
          <div><dt>Ordered</dt><dd>{formatKg(batch.total_ordered_weight_kg)} kg</dd></div>
          <div><dt>Trimming buffer</dt><dd>{formatKg(batch.total_buffer_weight_kg)} kg</dd></div>
          <div><dt>Purchase target</dt><dd>{formatKg(batch.total_required_weight_kg)} kg</dd></div>
        </dl>
      </div>}
      {batch && <div className="purchase-export-toolbar">
        <label>Buyer sheet category <select value={purchaseCategory} onChange={event => setPurchaseCategory(event.target.value)}><option value="">All categories</option>{PRODUCT_CATEGORIES.map(category => <option key={category.value} value={category.value}>{category.label}</option>)}</select></label>
        <button type="button" className="invoice-secondary-button" disabled={!batch} onClick={openBuyerSheet}><Download size={16} aria-hidden />Export Buyer Sheet</button>
        <small>Talad Thai market cutoff: 04:30 AM</small>
        {batch.source_files?.length ? <details className="purchase-source-files"><summary>Source files ({batch.source_files.length})</summary><table className="batch-table"><thead><tr><th>Client</th><th>Order file</th><th>Shipment</th><th>Route</th></tr></thead><tbody>{batch.source_files.map(source => <tr key={source.file_id}><td>{source.customer}</td><td>{source.file_name}</td><td>{source.shipment_date}</td><td>{source.route}</td></tr>)}</tbody></table></details> : null}
      </div>}
      <StatusMessage error={error} success={success} />
      {purchaseTask === "merge" && permissions.includes("merge_products") && <TaskDialog title="Merge duplicate products" description="Choose the duplicate and the master product to keep. Pending quantities will be combined. Confirmed packing and invoices are preserved." busy={mergeBusy} onClose={() => setPurchaseTask(null)}>
        <StatusMessage error={error} /><div className="register-pagination"><button type="button" disabled={!mergeOffset} onClick={() => setMergeOffset(value => Math.max(0, value - 100))}>Previous products</button><span>Products {mergeOffset + 1}–{mergeOffset + mergeProducts.length}</span><button type="button" disabled={mergeProducts.length < 100} onClick={() => setMergeOffset(value => value + 100)}>Next products</button></div>
        <label>Find products <input value={mergeSearch} onChange={event => { setMergeSearch(event.target.value); setMergeOffset(0); }} /></label>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", margin: "12px 0" }}>
          <label>Duplicate to merge <select value={mergeSource?.id || ""} onChange={event => { setMergeSource(mergeProducts.find(p => p.id === event.target.value) || null); setMergeReviewed(false); }}>
            <option value="">Select duplicate SKU</option>{mergeSource && !mergeProducts.some(p => p.id === mergeSource.id) && <option value={mergeSource.id}>{mergeSource.t_code} · {mergeSource.name_en}</option>}
            {mergeProducts.map(product => <option value={product.id} key={product.id}>{product.t_code} · {product.name_en || product.name_vi}</option>)}
          </select></label>
          <label>Keep this master SKU <select value={mergeTarget?.id || ""} onChange={event => { setMergeTarget(mergeProducts.find(p => p.id === event.target.value) || null); setMergeReviewed(false); }}>
            <option value="">Select master SKU</option>{mergeTarget && !mergeProducts.some(p => p.id === mergeTarget.id) && <option value={mergeTarget.id}>{mergeTarget.t_code} · {mergeTarget.name_en}</option>}
            {mergeProducts.map(product => <option value={product.id} key={product.id}>{product.t_code} · {product.name_en || product.name_vi}</option>)}
          </select></label>
        </div>
        {mergeSource && mergeTarget && <p>{mergeSource.t_code} ({mergeSource.name_en}) → {mergeTarget.t_code} ({mergeTarget.name_en}). Pending order quantities will be combined and purchasing totals rebuilt.</p>}
        <label><input type="checkbox" checked={mergeReviewed} onChange={event => setMergeReviewed(event.target.checked)} /> I reviewed these SKUs and they are the same product.</label>{" "}
        <button type="button" className="primary-button" disabled={mergeBusy || !mergeReviewed || !mergeSource || !mergeTarget || mergeSource.id === mergeTarget.id} onClick={mergeSelectedProducts}>Merge duplicate SKU</button>
      </TaskDialog>}

      {batch ? (
        <div>
          <section className="workflow-panel-v2" aria-label="Purchase list">
            <div className="purchase-table-toolbar" style={{
              padding: "var(--purchase-toolbar-padding, 16px 20px)",
              borderBottom: "1px solid var(--line)",
              background: "var(--surface-2)",
              display: "flex",
              flexDirection: "column",
              gap: "12px"
            }}>
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
                <div style={{
                  display: "flex",
                  background: "var(--line)",
                  padding: "3px",
                  borderRadius: "10px",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  overflowX: "auto"
                }}>
                  <button
                    type="button"
                    className={`filter-pill ${activeFilter === "all" ? "active" : ""}`}
                    onClick={() => setActiveFilter("all")}
                  >
                    All Items <span style={{ marginLeft: "4px", padding: "1px 6px", borderRadius: "999px", background: "var(--surface-2)", fontSize: "0.68rem" }}>{batch.items.length}</span>
                  </button>
                  <button
                    type="button"
                    className={`filter-pill ${activeFilter === "attention" ? "active" : ""}`}
                    onClick={() => setActiveFilter("attention")}
                  >
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--turmeric)", display: "inline-block", marginRight: "4px" }} />
                    Attention Needed <span style={{ marginLeft: "4px", padding: "1px 6px", borderRadius: "999px", background: "var(--turmeric-soft)", color: "var(--turmeric-ink)", fontSize: "0.68rem", fontWeight: 700 }}>{statusCounts.attention}</span>
                  </button>
                  <button
                    type="button"
                    className={`filter-pill ${activeFilter === "pending" ? "active" : ""}`}
                    onClick={() => setActiveFilter("pending")}
                  >
                    Pending Input <span style={{ marginLeft: "4px", padding: "1px 6px", borderRadius: "999px", background: "var(--surface-2)", fontSize: "0.68rem" }}>{statusCounts.pending}</span>
                  </button>
                  <button
                    type="button"
                    className={`filter-pill ${activeFilter === "confirmed" ? "active" : ""}`}
                    onClick={() => setActiveFilter("confirmed")}
                  >
                    <CheckCircle2 size={13} color="var(--leaf)" style={{ display: "inline-block", marginRight: "4px", verticalAlign: "middle" }} />
                    Matched Targets <span style={{ marginLeft: "4px", padding: "1px 6px", borderRadius: "999px", background: "var(--leaf-soft)", color: "var(--leaf-ink)", fontSize: "0.68rem", fontWeight: 700 }}>{statusCounts.confirmed}</span>
                  </button>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                  <div style={{ position: "relative", width: "240px" }}>
                    <Search size={15} style={{ position: "absolute", left: "10px", top: "50%", transform: "translateY(-50%)", color: "var(--muted)" }} />
                    <input
                      type="text"
                      placeholder="Search SKU, produce, category..."
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                      style={{
                        width: "100%",
                        padding: "6px 10px 6px 32px",
                        borderRadius: "8px",
                        border: "1px solid var(--line-strong)",
                        fontSize: "0.78rem",
                        background: "var(--surface)"
                      }}
                    />
                  </div>

                  <button
                    type="button"
                    onClick={() => setShowBulkBufferModal(true)}
                    disabled={!canEditBuffers || batch.status !== "PURCHASING_CONSOLIDATED"}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "6px",
                      padding: "6px 12px",
                      borderRadius: "8px",
                      background: "var(--turmeric-soft)",
                      border: "1px solid var(--turmeric-line)",
                      color: "var(--turmeric-ink)",
                      fontSize: "0.76rem",
                      fontWeight: 700,
                      cursor: "pointer",
                      boxShadow: "var(--shadow-sm)"
                    }}
                  >
                    <Sliders size={14} />
                    <span>Buffer Bulk Adjust %</span>
                  </button>
                </div>
              </div>
            </div>

            <form id="purchase-quantities" aria-busy={confirmBusy} onFocusCapture={() => setReviewedBatch(batch.batch_id)} onSubmit={confirmPurchases}><div className="purchase-entry-help"><p>Actual purchase quantities are editable. Target quantities are calculated.</p><button type="button" disabled={!canEnterPurchases || confirmBusy} onClick={handleAutoFillAllTargets}>Fill actuals from targets</button></div>
              <p className="purchase-scroll-hint">Scroll across the worksheet to review actual packs, kilograms and validation.</p>
              <div className="purchase-table-scroll" style={{ overflowX: "auto" }} tabIndex={0} role="region" aria-label="Purchase quantities table">
                <table className="worksheet-table">
                  <thead>
                    <tr style={{ background: "var(--surface-2)" }}>
                      <th style={{ width: "260px" }}>Produce Item & SKU</th>
                      <th style={{ width: "130px" }}>Category & Size</th>
                      <th className="col-demand" style={{ width: "140px", color: "var(--lagoon-ink)" }}>1. Base Demand</th>
                      <th className="col-buffer" style={{ width: "230px", color: "var(--turmeric-ink)" }}>2. Buffer Allowance</th>
                      <th className="col-target" style={{ width: "160px", color: "var(--leaf-ink)" }}>3. Target Purchase</th>
                      <th className="col-confirmed" style={{ width: "290px", color: "var(--ink)" }}>Buyer Confirmation & Validation</th>
                      <th style={{ width: "110px", textAlign: "center" }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredItems.map(it => {
                      const catLabel = getCategoryDisplay(it);
                      const emoji = getProduceEmoji(it.product_name, it.produce_type);
                      const currentPackInput = confirmedPacks[it.item_id] ?? "";
                      const currentWeightInput = confirmedWeights[it.item_id] ?? "";

                      // Discrepancy calculation
                      const isPending = itemStatuses[it.item_id] === "pending";
                      const isExact = itemStatuses[it.item_id] === "exact";
                      const isUnder = itemStatuses[it.item_id] === "under";
                      const isOver = itemStatuses[it.item_id] === "over";
                      const packDiff = Number(currentPackInput) - it.total_purchase_target_packs;

                      return (
                        <tr key={it.item_id} style={{
                          background: isUnder ? "rgba(251, 239, 207, 0.1)" : "inherit"
                        }}>
                          <td>
                            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                              <div style={{
                                width: "34px",
                                height: "34px",
                                borderRadius: "8px",
                                background: "var(--leaf-soft)",
                                border: "1px solid var(--leaf-soft)",
                                display: "grid",
                                placeItems: "center",
                                fontSize: "1.1rem",
                                flexShrink: 0
                              }}>
                                {emoji}
                              </div>
                              <div style={{ minWidth: 0 }}>
                                <strong style={{ display: "block", color: "var(--ink)", fontSize: "0.85rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                  {it.product_name}
                                </strong>
                                <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "2px" }}>
                                  <span style={{
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "0.68rem",
                                    fontWeight: 700,
                                    padding: "1px 5px",
                                    borderRadius: "4px",
                                    background: "var(--leaf-soft)",
                                    color: "var(--leaf)",
                                    border: "1px solid var(--leaf-soft)"
                                  }}>
                                    SKU: {it.product_code}
                                  </span>
                                  {it.produce_type && (
                                    <span style={{ fontSize: "0.68rem", color: "var(--muted)" }}>
                                      {it.produce_type}
                                    </span>
                                  )}
                                </div>
                              </div>
                            </div>
                            <div style={{ marginTop: "8px", fontSize: "0.76rem" }}>
                              {substitutions[it.item_id] && <p>Buyer-approved substitute: <strong>{substitutions[it.item_id].t_code} {substitutions[it.item_id].name_en}</strong></p>}
                              {canSubstitute && <button type="button" disabled={confirmBusy}
                                aria-label={`Choose substitute for ${it.product_code}`}
                                onClick={() => { setSubstitutionSearch(""); setSubstitutionOffset(0); setSubstitutionProducts([]); setSubstitutionError(""); setSubstitutionLoading(true); setSubstitutionItem(it); }}>
                                {substitutions[it.item_id] ? "Change substitute" : "Choose substitute"}
                              </button>}
                              {canSubstitute && substitutions[it.item_id] && <button type="button" disabled={confirmBusy}
                                aria-label={`Remove substitute for ${it.product_code}`} onClick={() => setSubstitutions(current => {
                                  const next = { ...current }; delete next[it.item_id]; return next;
                                })}>Use original product</button>}
                            </div>
                          </td>

                          <td>
                            <div>
                              <span style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "0.75rem", fontWeight: 600, color: "var(--ink-2)" }}>
                                {catLabel}
                              </span>
                              <div style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "var(--muted)", marginTop: "2px" }}>
                                {it.pack_size}
                              </div>
                            </div>
                          </td>

                          <td className="col-demand">
                            <div>
                              <strong style={{ color: "var(--lagoon-ink)", fontSize: "0.86rem" }}>
                                {it.ordered_pack_count} packs
                              </strong>
                              <div style={{ fontFamily: "var(--font-mono)", fontSize: "0.74rem", color: "var(--lagoon)" }}>
                                {formatKg(it.ordered_weight_kg)} kg
                              </div>
                            </div>
                          </td>

                          <td className="col-buffer">
                            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                                <span style={{ fontWeight: 700, color: "var(--turmeric-ink)", fontSize: "0.82rem" }}>
                                  +{it.buffer_pack_count} packs <small style={{ fontWeight: 500, color: "var(--turmeric)" }}>(+{formatKg(it.buffer_weight_kg)} kg)</small>
                                </span>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingItem(it);
                                    setNewBufferPct(it.buffer_percentage);
                                  }}
                                  style={{
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "0.68rem",
                                    fontWeight: 700,
                                    padding: "2px 6px",
                                    borderRadius: "5px",
                                    background: "var(--turmeric-soft)",
                                    color: "var(--turmeric-ink)",
                                    border: "1px solid var(--turmeric-line)",
                                    cursor: "pointer"
                                  }}
                                  title="Edit buffer percentage"
                                  disabled={!canEditBuffers || batch.status !== "PURCHASING_CONSOLIDATED"}
                                >
                                  {it.buffer_percentage}% (edit)
                                </button>
                              </div>
                            </div>
                          </td>

                          <td className="col-target">
                            <div>
                              <strong style={{ color: "var(--leaf-ink)", fontSize: "0.95rem", display: "block" }}>
                                {it.total_purchase_target_packs} packs
                              </strong>
                              <span style={{ fontFamily: "var(--font-mono)", color: "var(--leaf)", fontWeight: 700, fontSize: "0.76rem" }}>
                                {formatKg(it.total_purchase_target_weight_kg)} kg target
                              </span>
                            </div>
                          </td>

                          <td className="col-confirmed">
                            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                              <div style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "4px",
                                padding: "3px 8px",
                                borderRadius: "6px",
                                background: "var(--surface)",
                                border: isUnder ? "1px solid var(--turmeric-line)" : "1px solid var(--line-strong)",
                                boxShadow: "var(--shadow-sm)"
                              }}>
                                <input
                                  type="number"
                                  min="0"
                                  value={currentPackInput}
                                  disabled={!canEnterPurchases || confirmBusy}
                                  placeholder="—"
                                  onChange={e => setConfirmedPacks(prev => {
                                    const next = { ...prev };
                                    if (e.target.value === "") delete next[it.item_id];
                                    else next[it.item_id] = Number(e.target.value);
                                    return next;
                                  })}
                                  style={{
                                    width: "44px",
                                    textAlign: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontWeight: 700,
                                    border: 0,
                                    padding: 0,
                                    color: isUnder ? "var(--turmeric-ink)" : "var(--ink)",
                                    outline: "none"
                                  }}
                                  title="Confirmed Pack Count"
                                />
                                <span style={{ fontSize: "0.68rem", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>pk</span>
                              </div>

                              <span style={{ color: "var(--line-strong)" }}>/</span>

                              <div style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "4px",
                                padding: "3px 8px",
                                borderRadius: "6px",
                                background: "var(--surface)",
                                border: isUnder ? "1px solid var(--turmeric-line)" : "1px solid var(--line-strong)",
                                boxShadow: "var(--shadow-sm)"
                              }}>
                                <input
                                  type="number"
                                  step="0.0001"
                                  min="0"
                                  value={currentWeightInput}
                                  disabled={!canEnterPurchases || confirmBusy}
                                  placeholder="—"
                                  onChange={e => setConfirmedWeights(prev => {
                                    const next = { ...prev };
                                    if (e.target.value === "") delete next[it.item_id];
                                    else next[it.item_id] = Number(e.target.value);
                                    return next;
                                  })}
                                  style={{
                                    width: "60px",
                                    textAlign: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontWeight: 700,
                                    border: 0,
                                    padding: 0,
                                    color: isUnder ? "var(--turmeric-ink)" : "var(--ink)",
                                    outline: "none"
                                  }}
                                  title="Confirmed Total Weight (kg)"
                                />
                                <span style={{ fontSize: "0.68rem", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>kg</span>
                              </div>

                              {isExact ? (
                                <span style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px",
                                  padding: "2px 8px",
                                  borderRadius: "999px",
                                  fontSize: "0.68rem",
                                  fontWeight: 700,
                                  background: "var(--leaf-soft)",
                                  color: "var(--leaf)",
                                  border: "1px solid var(--leaf-line)"
                                }}>
                                  <Check size={12} color="var(--leaf)" /> Exact Match
                                </span>
                              ) : isUnder ? (
                                <span style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px",
                                  padding: "2px 8px",
                                  borderRadius: "999px",
                                  fontSize: "0.68rem",
                                  fontWeight: 700,
                                  background: "var(--turmeric-soft)",
                                  color: "var(--turmeric-ink)",
                                  border: "1px solid var(--turmeric-line)"
                                }}>
                                  <AlertTriangle size={12} color="var(--turmeric)" />
                                  ▲ Under target: {Math.max(0, -packDiff)} pk / {formatKg(Math.max(0, it.total_purchase_target_weight_kg - Number(currentWeightInput)))} kg
                                </span>
                              ) : isOver ? (
                                <span style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px",
                                  padding: "2px 8px",
                                  borderRadius: "999px",
                                  fontSize: "0.68rem",
                                  fontWeight: 700,
                                  background: "var(--plum-soft)",
                                  color: "var(--plum)",
                                  border: "1px solid var(--plum-line)"
                                }}>
                                  +{packDiff} Over Target
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => handleMatchSingleTarget(it)}
                                  disabled={!canEnterPurchases || confirmBusy}
                                  style={{
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: "4px",
                                    padding: "3px 8px",
                                    borderRadius: "6px",
                                    background: "var(--leaf-soft)",
                                    color: "var(--leaf)",
                                    border: "1px solid var(--leaf-line)",
                                    fontSize: "0.68rem",
                                    fontWeight: 700,
                                    cursor: "pointer"
                                  }}
                                >
                                  <Zap size={11} /> Match Target
                                </button>
                              )}
                            </div>
                          </td>

                          <td style={{ textAlign: "center" }}>
                            {isPending ? <span>Pending Input</span> : isUnder ? (
                              <span style={{
                                padding: "3px 8px",
                                borderRadius: "999px",
                                fontSize: "0.68rem",
                                fontWeight: 700,
                                background: "var(--turmeric-soft)",
                                color: "var(--turmeric-ink)",
                                border: "1px solid var(--turmeric-line)"
                              }}>
                                Needs Attention
                              </span>
                            ) : (
                              <span style={{
                                padding: "3px 8px",
                                borderRadius: "999px",
                                fontSize: "0.68rem",
                                fontWeight: 700,
                                background: "var(--leaf-soft)",
                                color: "var(--leaf-ink)",
                                border: "1px solid var(--leaf-line)"
                              }}>
                                {batch.status === "PURCHASING_CONSOLIDATED" ? "Ready to confirm" : "Confirmed"}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="purchase-confirm-bar" style={{
                padding: "var(--purchase-toolbar-padding, 14px 20px)",
                background: "var(--surface-2)",
                borderTop: "1px solid var(--line)",
                display: "flex",
                flexWrap: "wrap",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "12px",
                fontSize: "0.76rem",
                color: "var(--muted)"
              }}>
                <div>
                  Showing <strong style={{ color: "var(--ink)" }}>{filteredItems.length}</strong> of {batch.items.length} wholesale produce lines •
                  Formula: <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink)" }}>Base Demand + Trimming Buffer = Target Purchase</span>
                </div>

                <div style={{ display: "flex", gap: "10px" }}>
                  <button
                    type="submit"
                    data-guide={reviewedBatch === batch.batch_id ? "Confirm the reviewed purchases" : undefined}
                    data-guide-detail="Check actual packs and kilograms first. Confirmation saves this batch and sends it to packing."
                    data-guide-priority="10"
                    disabled={!permissions.includes("confirm_procurement") || !["PURCHASING_CONSOLIDATED", "PROCUREMENT_CONFIRMED"].includes(batch.status || "") || confirmBusy}
                    className="primary-button"
                    style={{ background: "var(--leaf)", minHeight: "38px", fontSize: "0.82rem" }}
                  >
                    <Verified size={15} />
                    {confirmBusy ? "Confirming…" : "Confirm purchases and send to packing"}
                  </button>
                  
                </div>
              </div>
            </form>
          </section>


        </div>
      ) : (
        loading
          ? <TableLoadingSkeleton label="Loading selected batch" columns={["Produce Item & SKU", "Category & Size", "1. Base Demand", "2. Buffer Allowance", "3. Target Purchase", "Buyer Confirmation & Validation", "Status"]} />
          : <EmptyState icon={ShoppingBag} title="Choose a purchase batch"
              actions={<><Link className="primary-button" href="/procurement/batches">Choose batch</Link><Link className="invoice-secondary-button" href="/orders">Upload an order</Link></>}>
              Open a batch to see what to buy, enter the actual packs and kilograms, then send it to packing.
            </EmptyState>
      )}

      <details className="task-toolbar" aria-label="Purchase batch tools"><summary>Batch preparation and catalog tools</summary>
        <div><strong>Prepare your purchase batch</strong><p>Combine client files or resolve duplicate products before buying.</p></div>
        <div className="task-toolbar-actions">
          <Link className="invoice-secondary-button" href="/procurement/combine">Combine order files</Link>
          {permissions.includes("merge_products") && <button type="button" onClick={() => { setError(""); setPurchaseTask("merge"); }}>Merge duplicate products</button>}
        </div>
      </details>
      <details className="supporting-tools"><summary>Find a batch by reference</summary>
      <div className="procurement-batch-loader" style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "14px",
        padding: "10px 16px",
        marginBottom: "20px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: "10px",
        boxShadow: "var(--shadow-sm)"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          
          <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.8rem", fontWeight: 700, color: "var(--ink-2)" }}>
            Batch Reference:
            <input
              type="text"
              value={batchIdInput}
              onChange={e => setBatchIdInput(e.target.value)}
              placeholder="e.g. PUR-20260901-TONIO-DEMO"
              style={{
                padding: "5px 10px",
                borderRadius: "8px",
                border: "1px solid var(--line-strong)",
                fontFamily: "var(--font-mono)",
                fontSize: "0.78rem",
                width: "240px"
              }}
            />
          </label>
          <button
            type="button"
            onClick={() => loadBatchById(batchIdInput)}
            disabled={loading || !batchIdInput.trim()}
            style={{
              padding: "6px 14px",
              borderRadius: "8px",
              background: "var(--leaf)",
              color: "var(--surface)",
              border: 0,
              fontSize: "0.78rem",
              fontWeight: 700,
              cursor: "pointer"
            }}
          >
            {loading ? "Loading…" : "Load Batch"}
          </button>
          
        </div>

        {batch && (
          <div style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
            Loaded: <strong style={{ color: "var(--ink)" }}>{batch.items.length} SKUs</strong> •
            Reference: <span style={{ fontFamily: "var(--font-mono)" }}>{batch.batch_reference}</span>
          </div>
        )}
      </div>

      </details>

      {substitutionItem && <TaskDialog title="Choose substitute" description={`Replace ${substitutionItem.product_code} ${substitutionItem.product_name} for this purchase. The buyer approves the change. Keep the same pack size; the substitute's selling price will apply.`}
        onClose={() => setSubstitutionItem(null)}>
        <label>Search substitutes<input type="search" value={substitutionSearch} onChange={event => {
          setSubstitutionSearch(event.target.value); setSubstitutionOffset(0); setSubstitutionProducts([]); setSubstitutionError(""); setSubstitutionLoading(true);
        }} placeholder="Product code or name" /></label>
        {substitutionError && <p role="alert">{substitutionError}</p>}
        {substitutionLoading ? <p role="status">Loading substitutes…</p> : <ul>
          {substitutionProducts.filter(product => product.id !== substitutionItem.product_id).map(product => <li key={product.id}>
            <button type="button" onClick={() => {
              setSubstitutions(current => ({ ...current, [substitutionItem.item_id]: product })); setSubstitutionItem(null);
            }}>Approve {product.t_code} {product.name_en || product.name_vi}</button>
          </li>)}
        </ul>}
        {!substitutionLoading && !substitutionError && !substitutionProducts.some(product => product.id !== substitutionItem.product_id) && <p>No matching substitutes. Try another search.</p>}
        <div className="task-toolbar-actions">
          <button type="button" disabled={substitutionLoading || substitutionOffset === 0} onClick={() => { setSubstitutionOffset(value => Math.max(0, value - 50)); setSubstitutionLoading(true); setSubstitutionError(""); }}>Previous substitutes</button>
          <button type="button" disabled={substitutionLoading || substitutionProducts.length < 50} onClick={() => { setSubstitutionOffset(value => value + 50); setSubstitutionLoading(true); setSubstitutionError(""); }}>Next substitutes</button>
        </div>
      </TaskDialog>}

      {editingItem && <BufferDialog title="Adjust trimming buffer" description={`Extra stock bought to cover trimming loss for ${editingItem.product_code} (${editingItem.product_name}).`}
        value={newBufferPct} onChange={setNewBufferPct} busy={bufferBusy} submitLabel="Save buffer" onSubmit={updateBuffer} onClose={() => setEditingItem(null)} />}
      {showBulkBufferModal && <BufferDialog title="Adjust buffer for all products" description={`Apply one trimming buffer to all ${batch?.items.length ?? 0} products in this batch and recalculate what to buy.`}
        value={bulkBufferPct} onChange={setBulkBufferPct} busy={bulkBufferBusy} submitLabel="Apply to batch" onSubmit={applyBulkBuffer} onClose={() => setShowBulkBufferModal(false)} />}
    </div>
  );
}

// One form for the per-product and whole-batch trimming buffer.
function BufferDialog({ title, description, value, onChange, busy, submitLabel, onSubmit, onClose }: {
  title: string; description: string; value: number; onChange: (value: number) => void; busy: boolean;
  submitLabel: string; onSubmit: (event: React.FormEvent) => void; onClose: () => void;
}) {
  return <TaskDialog title={title} description={description} size="sm" busy={busy} onClose={onClose}>
    <form className="task-dialog-pad" onSubmit={onSubmit}>
      <label>Buffer (%)<input type="number" step="1" min="0" max="100" value={value} onChange={event => onChange(Number(event.target.value))} required /></label>
      <div className="task-dialog-actions"><button type="button" className="invoice-secondary-button" onClick={onClose}>Cancel</button>
        <button type="submit" className="primary-button" disabled={busy}>{busy ? "Saving…" : submitLabel}</button></div>
    </form>
  </TaskDialog>;
}
