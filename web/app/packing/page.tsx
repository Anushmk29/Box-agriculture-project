"use client";

import { PageLoadingSkeleton, TableLoadingSkeleton } from "@/components/loading-skeleton";

import { Suspense, useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Boxes,
  ArrowRightLeft,
  Truck,
  Download,
  QrCode,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Search,
  SlidersHorizontal,
  Receipt
} from "lucide-react";
import { api } from "@/lib/api";
import { isRevisionConflict } from "@/lib/api-errors";
import { confirmAction } from "@/lib/confirm";
import { StatusMessage } from "@/components/status-message";
import { RegisterBackLink } from "@/components/record-register";
import { TaskDialog } from "@/components/task-dialog";
import { useSession } from "@/lib/session-context";
import { useCompactLayout } from "@/lib/use-compact-layout";
import { WorkflowProgress } from "@/components/workflow-guidance";
import { safeDestination } from "@/lib/workflow";
import QRCode from "qrcode";

type BoxItem = {
  item_id: string;
  product_id: string;
  product_code: string;
  produce_name: string;
  pack_size: string;
  packed_count: number;
  net_weight_kg: number;
  adjustment_type: string;
};

type BoxDetail = {
  box_id: string;
  box_number: number;
  box_type_name: string;
  box_catalogue_id: string;
  box_material: string;
  safe_net_capacity_kg: number;
  allocation_explanation: string;
  is_overridden: boolean;
  actual_gross_weight_kg: number | null;
  actual_net_weight_kg: number | null;
  volumetric_weight_kg: number | null;
  chargeable_weight_kg: number | null;
  is_locked: boolean;
  qr_code_uid: string;
  sticker_label: string;
  items: BoxItem[];
  packing_mode?: "EL" | "NON_EL" | null;
  needs_replan?: boolean;
  replan_reasons?: string[];
  volume_cm3?: number;
  usable_volume_cm3?: number;
  volume_utilization_pct?: number | null;
  layers?: Array<{ item_id: string; product_code: string; product_name: string; pack_size: string; packed_count: number; firmness: string; layer: string; volume_source?: string }>;
};

type PackingListResponse = {
  client_order_id: string;
  packing_revision: number;
  order_ref_id: string;
  status: string;
  packing_approval_status: string;
  packing_review_note: string | null;
  sub_customer_code: string;
  route_code: string;
  shipment_date: string;
  total_boxes_count: number;
  boxes: BoxDetail[];
};

type BoxCatalogue = {
  id: string;
  size_name: string;
  box_type: string;
  length_cm: number;
  width_cm: number;
  height_cm: number;
};

type VehicleLoadExport = {
  route_code: string;
  shipment_date: string;
  total_boxes_count: number;
  total_gross_weight_kg: number;
  total_net_weight_kg: number;
  customers: Array<{
    customer_code: string;
    company_name: string;
    boxes_count: number;
    box_numbers: number[];
    total_net_kg: number;
    total_gross_kg: number;
  }>;
  checklist_text: string;
  csv_content: string;
};

type ReplanPreview = { preview_token: string; warning: string; packing_mode: string; boxes: Array<{ box_number: number; box_catalogue_id: string; net_weight_kg: number; volume_cm3: number; items: Array<{ product_id: string; product_name?: string; pack_size: string; count: number }> }> };

type MasterProduct = { id: string; t_code: string; name_en: string; name_vi: string };
function packingStatus(status = "") {
  if (!status) return "Status unavailable";
  return ({ HANDOFF_01_SENT: "Not started", PACKING_DESIGNED: "Boxes prepared", FLOOR_PACKING: "Packing in progress", FINAL_PACK_LOCKED: "Locked: send to accounting", HANDOFF_02_RETURNED: "Packing complete" } as Record<string, string>)[status] || status.replaceAll("_", " ");
}

function displayCustomer(value: string) {
  return value.replace(/_+/g, " ").replace(/\s+/g, " ").trim();
}

function shipmentDate(value: string) {
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export default function PackingPage() { return <Suspense fallback={<PageLoadingSkeleton label="Loading packing…" />}><PackingContent /></Suspense>; }

function PackingContent() {
  const compact = useCompactLayout();
  const searchParams = useSearchParams();
  const requestedOrderId = searchParams.get("orderId");
  const requestedBox = searchParams.get("box");
  const returnPath = safeDestination(searchParams.get("returnTo"));
  const packingQueue = returnPath.startsWith("/packing/orders") ? returnPath : "/packing/orders";
  const focusedBox = useRef("");
  const [reviewedBoxes, setReviewedBoxes] = useState<Set<string>>(new Set());
  const [orderId, setOrderId] = useState("");
  const [packingData, setPackingData] = useState<PackingListResponse | null>(null);
  const [boxCatalogues, setBoxCatalogues] = useState<BoxCatalogue[]>([]);
  const [boxSearch, setBoxSearch] = useState("");
  const [boxPage, setBoxPage] = useState(0);
  const [boxLimit, setBoxLimit] = useState(25);
  const pageSize = compact ? 1 : boxLimit;

  const [packingMode, setPackingMode] = useState("");
  const [replanPreview, setReplanPreview] = useState<ReplanPreview | null>(null);
  const [replanError, setReplanError] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [transferModalOpen, setTransferModalOpen] = useState(false);
  const [transferRevision, setTransferRevision] = useState<number>();
  const [revisionConflict, setRevisionConflict] = useState(false);
  const [sourceBoxId, setSourceBoxId] = useState("");
  const [targetBoxId, setTargetBoxId] = useState("");
  const [removeEmptySource, setRemoveEmptySource] = useState(false);
  const [transferBusy, setTransferBusy] = useState(false);
  const [transferError, setTransferError] = useState("");

  const [vehicleExport, setVehicleExport] = useState<VehicleLoadExport | null>(null);
  const [exportBusy, setExportBusy] = useState(false);

  const [previewBox, setPreviewBox] = useState<BoxDetail | null>(null);
  const [previewQrDataUrl, setPreviewQrDataUrl] = useState("");
  const [stickerCodeCopied, setStickerCodeCopied] = useState(false);
  const permissions = useSession()?.permissions ?? [];
  const [itemSearchBusy, setItemSearchBusy] = useState(false);
  const [itemSearchError, setItemSearchError] = useState("");
  const orderRequest = useRef(0);
  const selectionBusy = useRef(false);
  const selectionRequest = useRef(0);
  const operationBusy = useRef(false);
  const [itemEditor, setItemEditor] = useState<{ boxId: string; itemId?: string; revision: number } | null>(null);
  const [itemSearch, setItemSearch] = useState("");
  const [itemOffset, setItemOffset] = useState(0);
  const [itemProducts, setItemProducts] = useState<MasterProduct[]>([]);
  const [itemProduct, setItemProduct] = useState("");
  const [selectedItemProduct, setSelectedItemProduct] = useState<MasterProduct | null>(null);
  const [itemPack, setItemPack] = useState("100g");
  const [itemQuantity, setItemQuantity] = useState("1");
  const [itemWeight, setItemWeight] = useState("0.1");
  const canEditPacking = !revisionConflict && ["PACKING_DESIGNED", "FLOOR_PACKING"].includes(packingData?.status || "") && permissions.includes("floor_packing");
  const orderStatus = packingData?.client_order_id === orderId ? packingData.status : "";
  const transferSource = packingData?.boxes.find(box => box.box_id === sourceBoxId);
  const transferSourceWeight = transferSource?.items.reduce((total, item) => total + item.net_weight_kg, 0) ?? 0;
  const eligibleTransferTargets = transferSource ? packingData!.boxes.filter(box =>
    box.box_id !== transferSource.box_id
    && !box.is_locked
    && box.box_material === transferSource.box_material
    && box.items.reduce((total, item) => total + item.net_weight_kg, 0) + transferSourceWeight <= box.safe_net_capacity_kg + 0.01
  ) : [];

  useEffect(() => {
    const menus = () => document.querySelectorAll<HTMLDetailsElement>(".packing-desk .packing-order-details[open], .packing-desk .packing-secondary-tools[open], .packing-desk .packing-list-options[open]");
    function dismissOutside(event: MouseEvent) {
      for (const menu of menus()) if (event.target instanceof Node && !menu.contains(event.target)) menu.open = false;
    }
    function dismissWithEscape(event: KeyboardEvent) {
      if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
      for (const menu of menus()) { menu.open = false; menu.querySelector("summary")?.focus(); }
    }
    document.addEventListener("click", dismissOutside);
    document.addEventListener("keydown", dismissWithEscape);
    return () => { document.removeEventListener("click", dismissOutside); document.removeEventListener("keydown", dismissWithEscape); };
  }, []);

  useEffect(() => {
    if (!itemEditor) return;
    let active = true;
    const timer = window.setTimeout(() => {
      setItemSearchBusy(true); setItemSearchError(""); setItemProducts([]);
      api<MasterProduct[]>(`/master-data/products?q=${encodeURIComponent(itemSearch)}&limit=100&offset=${itemOffset}`)
        .then(products => { if (active) setItemProducts(products); })
        .catch(reason => { if (active) setItemSearchError(reason instanceof Error ? reason.message : "Unable to search products"); })
        .finally(() => { if (active) setItemSearchBusy(false); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [itemEditor, itemSearch, itemOffset]);

  function editItem(boxId: string, item?: BoxItem) {
    if (!packingData || revisionConflict) return;
    setItemEditor({ boxId, itemId: item?.item_id, revision: packingData.packing_revision }); setError("");
    setSelectedItemProduct(item ? { id: item.product_id, t_code: item.product_code, name_en: item.produce_name, name_vi: "" } : null);
    setItemSearchBusy(true); setItemSearchError(""); setItemProducts([]);
    setItemOffset(0);
    setItemProduct(item?.product_id || ""); setItemSearch(item?.product_code || "");
    setItemPack(item?.pack_size || "100g"); setItemQuantity(String(item?.packed_count || 1)); setItemWeight(String(item?.net_weight_kg || 0.1));
  }

  async function saveItem(event: React.FormEvent) {
    event.preventDefault(); if (!itemEditor || revisionConflict) return;
    setBusy(true); setError("");
    try {
      await api(`/packing/boxes/${itemEditor.boxId}/items`, { method: "PUT", body: JSON.stringify({
        expected_revision: itemEditor.revision, item_id: itemEditor.itemId, product_id: itemProduct, pack_size: itemPack,
        quantity: Number(itemQuantity), net_weight_kg: Number(itemWeight),
      }) });
      setItemEditor(null); await loadPackingList(orderId); setMessage("Packing item saved. Review the contents before finalizing packing.");
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true); setError(reason instanceof Error ? reason.message : "Unable to save item"); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    if (!previewBox) return;
    let active = true;
    const scanUrl = `${window.location.origin}/scan/${encodeURIComponent(previewBox.qr_code_uid)}`;
    QRCode.toDataURL(scanUrl, { width: 280, margin: 1, errorCorrectionLevel: "M" })
      .then(dataUrl => { if (active) setPreviewQrDataUrl(dataUrl); })
      .catch(() => { if (active) setPreviewQrDataUrl(""); });
    return () => { active = false; };
  }, [previewBox]);

  async function copyStickerCode() {
    if (!previewBox) return;
    try {
      await navigator.clipboard.writeText(previewBox.qr_code_uid);
      setStickerCodeCopied(true);
    } catch {
      setError("Could not copy the sticker code. Select the code and copy it manually.");
    }
  }

  const loadPackingList = useCallback(async (id: string) => {
    if (!id.trim()) return false;
    const request = ++orderRequest.current;
    setBusy(true); setError("");
    try {
      const data = await api<PackingListResponse>(`/packing/orders/${encodeURIComponent(id.trim())}/packing-list`);
      if (request !== orderRequest.current) return false;
      setPackingData(data);
      setPackingMode(current => current || data.boxes[0]?.packing_mode || "");
      setOrderId(id.trim());
      return true;
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true);
      if (request === orderRequest.current) setError(reason instanceof Error ? reason.message : "Failed to load packing list");
      return false;
    } finally {
      if (request === orderRequest.current) setBusy(false);
    }
  }, []);

  const selectOrder = useCallback(async (id: string) => {
    if (selectionBusy.current || operationBusy.current) return false;
    const selection = ++selectionRequest.current;
    selectionBusy.current = true; setBusy(true); setError(""); setMessage("");
    setPackingData(null); setRevisionConflict(false); setItemEditor(null); setTransferModalOpen(false); setPackingMode(""); setBoxPage(0); setBoxSearch("");
    try {
      const existing = await api<PackingListResponse>(`/packing/orders/${encodeURIComponent(id)}/packing-list`);
      if (existing.status === "PACKING_DESIGNED" && existing.boxes[0]?.packing_mode) {
        await api(`/packing/orders/${encodeURIComponent(id)}/prepare`, { method: "POST", body: JSON.stringify({ expected_revision: existing.packing_revision, packing_mode: existing.boxes[0].packing_mode }) });
      }
      if (selection !== selectionRequest.current) return false;
      return await loadPackingList(id);
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true);
      if (selection === selectionRequest.current) setError(reason instanceof Error ? reason.message : "Unable to prepare boxes. Select this order to retry.");
      return false;
    } finally { if (selection === selectionRequest.current) { selectionBusy.current = false; setBusy(false); } }
  }, [loadPackingList]);

  async function reloadLatestPacking() {
    if ((itemEditor || transferModalOpen) && !await confirmAction({ title: "Discard unsaved packing changes?", body: "Your changes have not been saved. Load the latest packing record to review what changed.", confirmLabel: "Discard and reload", tone: "danger" })) return;
    setItemEditor(null); setTransferModalOpen(false); setReplanPreview(null);
    if (await loadPackingList(orderId)) { setRevisionConflict(false); setMessage("Latest packing loaded. Review the contents before making changes or finalizing."); }
  }

  async function generateBoxes() {
    if (!packingMode || operationBusy.current) return;
    operationBusy.current = true; setBusy(true); setError("");
    try {
      await api(`/packing/orders/${orderId}/prepare`, { method: "POST", body: JSON.stringify({ expected_revision: packingData?.packing_revision, packing_mode: packingMode }) });
      await loadPackingList(orderId);
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true); setError(reason instanceof Error ? reason.message : "Unable to generate boxes"); }
    finally { operationBusy.current = false; setBusy(false); }
  }

  async function previewReplan() {
    if (!packingMode || operationBusy.current) return;
    operationBusy.current = true; setBusy(true); setError(""); setReplanError("");
    try { setReplanPreview(await api<ReplanPreview>(`/packing/orders/${orderId}/replan-preview`, { method: "POST", body: JSON.stringify({ expected_revision: packingData?.packing_revision, packing_mode: packingMode }) })); }
    catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true); setError(reason instanceof Error ? reason.message : "Unable to preview a new plan"); }
    finally { operationBusy.current = false; setBusy(false); }
  }

  async function applyReplan() {
    if (!replanPreview || operationBusy.current) return;
    operationBusy.current = true; setBusy(true); setReplanError("");
    try {
      await api(`/packing/orders/${orderId}/replan-apply`, { method: "POST", body: JSON.stringify({ preview_token: replanPreview.preview_token }) });
      setReplanPreview(null); await loadPackingList(orderId); setMessage("Reviewed plan applied. Print new stickers for the replacement boxes.");
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true); setReplanError(reason instanceof Error ? reason.message : "Unable to apply the plan"); }
    finally { operationBusy.current = false; setBusy(false); }
  }

  async function changePackingMode(boxId: string, mode: string) {
    if (operationBusy.current) return;
    operationBusy.current = true; setBusy(true); setError("");
    try {
      await api(`/packing/boxes/${boxId}/packing-mode`, { method: "PUT", body: JSON.stringify({ expected_revision: packingData?.packing_revision, packing_mode: mode }) });
      await loadPackingList(orderId); setMessage("Box packing rules updated and contents revalidated.");
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true); setError(reason instanceof Error ? reason.message : "Unable to change packing rules"); }
    finally { operationBusy.current = false; setBusy(false); }
  }

  async function finalizePacking() {
    if (!packingData || revisionConflict || operationBusy.current) return;
    operationBusy.current = true; setBusy(true); setError(""); setMessage("");
    try {
      await api(`/packing/orders/${orderId}/finalize`, { method: "POST", body: JSON.stringify({ expected_revision: packingData.packing_revision }) });
      if (await loadPackingList(orderId)) setMessage("Packing finalized and sent to accounting. Box contents and totals are now locked.");

    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true); setError(reason instanceof Error ? reason.message : "Finalization failed. Retry Finalize Packing."); }
    finally { operationBusy.current = false; setBusy(false); }
  }

  useEffect(() => {
    // Load box catalogues for sizing drop-down
    api<BoxCatalogue[]>("/master-data/boxes")
      .then(cats => setBoxCatalogues(cats || []))
      .catch(reason => {
        setBoxCatalogues([]);
        setError(reason instanceof Error ? reason.message : "Failed to load the box catalogue");
      });

  }, []);

  const cancelPackingRead = useCallback(() => { orderRequest.current++; selectionRequest.current++; selectionBusy.current = false; }, []);
  useEffect(() => {
    const startup = window.setTimeout(() => { if (requestedOrderId) void selectOrder(requestedOrderId); }, 0);
    return () => { cancelPackingRead(); window.clearTimeout(startup); };
  }, [requestedOrderId, selectOrder, cancelPackingRead]);

  async function changeBoxSize(boxId: string, newCatalogueId: string) {
    setError("");
    try {
      await api(`/packing/boxes/${boxId}/override-size`, {
        method: "PUT",
        body: JSON.stringify({
          expected_revision: packingData?.packing_revision, new_catalogue_id: newCatalogueId,
          override_reason: "Floor box size adjustment via sizing dropdown"
        })
      });
      setMessage("✓ Box size updated successfully!");
      if (orderId) {
        await loadPackingList(orderId);
      }
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true);
      setError(reason instanceof Error ? reason.message : "Failed to override box size");
    }
  }

  async function handleTransferContents(e: React.FormEvent) {
    e.preventDefault();
    if (revisionConflict) return;
    if (!sourceBoxId || !targetBoxId || sourceBoxId === targetBoxId) {
      setError("Please select distinct source and target boxes for combining");
      return;
    }
    setTransferBusy(true);
    setError("");
    setTransferError("");

    try {
      await api("/packing/boxes/transfer-contents", {
        method: "POST",
        body: JSON.stringify({
          expected_revision: transferRevision, source_box_id: sourceBoxId,
          target_box_id: targetBoxId,
          remove_empty_source: removeEmptySource
        })
      });
      setMessage("✓ Items securely transferred to target box without re-typing SKUs!");
      setTransferModalOpen(false);
      if (orderId) {
        await loadPackingList(orderId);
      }
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true);
      const message = reason instanceof Error ? reason.message : "Transfer failed";
      setError(message);
      setTransferError(message);
    } finally {
      setTransferBusy(false);
    }
  }

  async function triggerVehicleLoadExport() {
    setExportBusy(true);
    setError("");
    try {
      const route = packingData?.route_code || "TETE";
      const date = packingData?.shipment_date ?? new Date().toISOString().slice(0, 10);
      const data = await api<VehicleLoadExport>(
        `/packing/transportation-manifests/export-vehicle-load?route_code=${route}&shipment_date=${date}`
      );
      setVehicleExport(data);
    } catch (reason) {
      if (isRevisionConflict(reason)) setRevisionConflict(true);
      setError(reason instanceof Error ? reason.message : "Failed to generate transportation export file");
    } finally {
      setExportBusy(false);
    }
  }

  function downloadExportFile() {
    if (!vehicleExport) return;
    const blob = new Blob([vehicleExport.csv_content], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", `Vehicle_Load_Totals_${vehicleExport.route_code}_${vehicleExport.shipment_date}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  const needsReplan = Boolean(packingData?.boxes.some(box => box.needs_replan));
  const packingComplete = orderStatus === "HANDOFF_02_RETURNED";
  const totalNetWeight = (packingData?.boxes || []).reduce((total, box) => total + box.items.reduce((weight, item) => weight + item.net_weight_kg, 0), 0);
  const readyBoxes = (packingData?.boxes || []).filter(box => box.items.length > 0);
  const matchingBoxes = (packingData?.boxes || []).filter(box => `${box.box_number} ${box.items.map(item => `${item.produce_name} ${item.product_code}`).join(" ")}`.toLowerCase().includes(boxSearch.toLowerCase()));
  const visibleStart = Math.min(boxPage, Math.max(0, matchingBoxes.length - 1));
  const visibleBoxes = matchingBoxes.slice(visibleStart, visibleStart + pageSize);
  useEffect(() => {
    if (!requestedBox || !packingData || focusedBox.current === `${packingData.client_order_id}:${requestedBox}:${pageSize}`) return;
    const index = packingData.boxes.findIndex(box => box.qr_code_uid === requestedBox);
    if (index < 0) return;
    const timer = window.setTimeout(() => {
      setBoxSearch(""); setBoxPage(Math.floor(index / pageSize) * pageSize);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [requestedBox, packingData, pageSize]);
  useEffect(() => {
    if (!requestedBox || !packingData || focusedBox.current === `${packingData.client_order_id}:${requestedBox}:${pageSize}`) return;
    const box = visibleBoxes.find(box => box.qr_code_uid === requestedBox);
    if (!box) return;
    const element = document.getElementById(`box-${box.box_id}`);
    if (element) { element.focus(); element.scrollIntoView({ block: "center" }); focusedBox.current = `${packingData.client_order_id}:${requestedBox}:${pageSize}`; }
  }, [requestedBox, packingData, visibleBoxes, pageSize]);

  return (
    <div className="page packing-workspace packing-desk">
      <div className="packing-back"><ArrowLeft size={15} aria-hidden /><RegisterBackLink fallback="/packing/orders" label="Packing orders" /></div>
      <header className="packing-workspace-heading">
        <div className="packing-title-row"><h1>Packing desk</h1>{packingData && <span key={orderStatus} className={`packing-order-status${packingComplete ? " is-complete" : ""}`}>
          {packingComplete ? <CheckCircle2 size={16} aria-hidden /> : <span className="packing-status-dot" aria-hidden />}{packingStatus(orderStatus)}
        </span>}</div>
        {!packingData && !requestedOrderId && <Link className="invoice-secondary-button" href={packingQueue}>Choose an order<ArrowRight size={16} aria-hidden /></Link>}
        {packingComplete && (permissions.includes("manage_billing") && packingData!.boxes.length > 0
          ? <Link className="primary-button packing-next-action" href={`/billing?orderId=${encodeURIComponent(packingData!.client_order_id)}`}><Receipt size={17} aria-hidden />Continue to billing<ArrowRight size={16} aria-hidden /></Link>
          : <Link className="primary-button packing-next-action" href={packingQueue}>Pack next order<ArrowRight size={16} aria-hidden /></Link>)}
        {orderStatus === "FINAL_PACK_LOCKED" && <button type="button" className="primary-button packing-next-action" disabled={busy} onClick={() => void finalizePacking()}>Send Final Packing to Accounting<ArrowRight size={16} aria-hidden /></button>}
      </header>

      <StatusMessage error={error} success={message} />
      {revisionConflict && <button type="button" data-guide="Reload the latest packing before editing" data-guide-priority="1" disabled={busy || transferBusy} onClick={() => void reloadLatestPacking()}>Reload latest packing for review</button>}
      {requestedBox && packingData && !packingData.boxes.some(box => box.qr_code_uid === requestedBox) && <StatusMessage error="This box is not part of the selected order. Scan the original sticker again." />}
      {packingData && <section className="packing-order-overview" aria-label="Current task">
        <div className="packing-order-context">
          <div className="packing-customer"><h2 title={packingData.sub_customer_code}>{displayCustomer(packingData.sub_customer_code)}</h2>
            <p className="packing-order-meta"><span>{packingData.boxes.length} {packingData.boxes.length === 1 ? "box" : "boxes"}</span><span>{totalNetWeight.toFixed(2)} kg net</span><time dateTime={packingData.shipment_date}>{shipmentDate(packingData.shipment_date)}</time></p>
          </div>
          <details className="packing-order-details" name="packing-order-menu"><summary>Order details<ChevronDown size={14} aria-hidden /></summary>
            <div className="packing-order-detail-body"><dl><div><dt>Order reference</dt><dd>{packingData.order_ref_id}</dd></div><div><dt>Route</dt><dd>{packingData.route_code}</dd></div></dl>
              <WorkflowProgress current="packing" done links={packingComplete ? { billing: `/billing?orderId=${encodeURIComponent(orderId)}` } : {}} />
            </div>
          </details>
        </div>
        <div className="packing-order-footer">
          <p className="packing-stage-note">{packingComplete ? <><Check size={16} aria-hidden />Packing complete. Ready for accounting.</> : canEditPacking ? "Open a box to review or adjust its contents." : orderStatus === "FINAL_PACK_LOCKED" ? "Boxes are locked and ready to send to accounting." : "Choose packing rules to prepare this order."}</p>
          <details className="packing-secondary-tools" name="packing-order-menu"><summary>Order tools<ChevronDown size={14} aria-hidden /></summary><div className="packing-secondary-actions">
            {canEditPacking && packingData.boxes.length > 1 && <button type="button" disabled={busy} onClick={() => { setSourceBoxId(""); setTargetBoxId(""); setTransferRevision(packingData.packing_revision); setTransferError(""); setTransferModalOpen(true); }}><ArrowRightLeft size={16} aria-hidden />Move items between boxes</button>}
            <button type="button" disabled={exportBusy} onClick={triggerVehicleLoadExport}><Truck size={16} aria-hidden />Export vehicle load</button>
            <Link href="/packing/setup">Products &amp; box settings</Link>
            <Link href={packingQueue}>{packingComplete ? "Pack next order" : "Change order"}</Link>
          </div></details>
        </div>
      </section>}

      {packingData && orderStatus === "HANDOFF_01_SENT" && <section className="packing-mode-start" aria-label="Choose packing rules"><h2>Choose the rules for these boxes</h2><p>Staff choose EL or Non-EL. The destination country does not select this mode.</p><label>Packing rules<select data-guide={!packingMode ? "Choose EL or Non-EL packing rules" : undefined} data-guide-detail="Choose the rules for this shipment. Country alone does not determine EL or Non-EL." value={packingMode} onChange={event => setPackingMode(event.target.value)}><option value="">Choose EL or Non-EL</option><option value="EL">EL rules</option><option value="NON_EL">Non-EL rules</option></select></label><button type="button" className="primary-button" disabled={busy || !packingMode} data-guide={packingMode ? "Generate the packing boxes" : undefined} onClick={() => void generateBoxes()}>Generate packing boxes</button></section>}
      {canEditPacking && <details className="packing-mode-start" aria-label="Review packing plan" open={needsReplan || undefined}><summary><strong>{needsReplan ? "Packing data changed" : "Review another box plan"}</strong></summary>{needsReplan && <p role="alert">Review and apply a current plan before finalizing. Your current packed quantities remain unchanged until you apply a preview.</p>}<label>Rules for the reviewed plan<select data-guide={needsReplan && !packingMode ? "Choose rules for the replacement plan" : undefined} data-guide-priority="10" value={packingMode} onChange={event => setPackingMode(event.target.value)}><option value="">Choose EL or Non-EL</option><option value="EL">EL rules</option><option value="NON_EL">Non-EL rules</option></select></label><button type="button" data-guide={needsReplan ? "Preview the updated packing plan" : undefined} data-guide-priority="10" data-guide-detail="Review the proposed boxes before applying them. Your saved quantities stay unchanged until you apply the plan." disabled={busy || !packingMode} onClick={() => void previewReplan()}>Preview replan</button></details>}
      {/* Review each box and open details to adjust contents. */}
      {packingData ? (
        <div className="packing-boxes-section">
          <div className="packing-list-head"><h2>Boxes <span className="packing-box-count">{packingData.boxes.length}</span></h2>
            {packingData.boxes.length > 1 && <div className="packing-list-controls"><label className="packing-box-search"><Search size={17} aria-hidden /><span className="sr-only">Find box or product</span><input type="search" placeholder="Find a box or product" value={boxSearch} onChange={event => { setBoxSearch(event.target.value); setBoxPage(0); }} /></label>
              {!compact && <details className="packing-list-options"><summary aria-label="View options"><SlidersHorizontal size={18} aria-hidden /></summary><label>Boxes per page<select value={boxLimit} onChange={event => { setBoxLimit(Number(event.target.value)); setBoxPage(0); }}>{[25, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label></details>}
            </div>}
          </div>
          {(matchingBoxes.length > pageSize || boxSearch) && <div className="register-pagination box-navigation"><span>{compact ? `Box ${matchingBoxes.length ? visibleStart + 1 : 0} of ${matchingBoxes.length}` : `${matchingBoxes.length} matching boxes · Page ${Math.floor(visibleStart / pageSize) + 1}`}</span>{matchingBoxes.length > pageSize && <><button type="button" disabled={visibleStart === 0} onClick={() => setBoxPage(Math.max(0, visibleStart - pageSize))}>{compact ? "Previous box" : "Previous boxes"}</button><button type="button" disabled={visibleStart + pageSize >= matchingBoxes.length} onClick={() => setBoxPage(visibleStart + pageSize)}>{compact ? "Next box" : "Next boxes"}</button></>}</div>}
          {!matchingBoxes.length && <p role="status">No matching boxes. Clear the search to see all boxes.</p>}

          <ol id="packing-boxes" className="packing-box-list">
            {visibleBoxes.map(box => {
              const state = box.needs_replan && !box.is_locked ? { label: "Needs replan", tone: "progress" } : box.is_locked ? { label: "Locked", tone: "success" } : box.items.length ? { label: "Ready for review", tone: "info" } : { label: "Needs contents", tone: "progress" };
              const size = boxCatalogues.find(catalogue => catalogue.id === box.box_catalogue_id);
              const editable = canEditPacking && !box.is_locked;
              const packs = box.items.reduce((total, item) => total + item.packed_count, 0);
              const preview = box.items.slice(0, 2).map(item => item.produce_name.split(" / ").at(-1) || item.produce_name).join(" · ");
              return <li key={box.box_id} id={`box-${box.box_id}`} tabIndex={-1} className={`packing-box-row${box.qr_code_uid === requestedBox ? " scanned-box" : ""}${box.is_locked ? " is-locked" : ""}`}>
                <details className="packing-box-details" name="packing-box" open={box.qr_code_uid === requestedBox || undefined} onToggle={event => { if (event.currentTarget.open) setReviewedBoxes(current => { const key = `${orderId}:${box.box_id}`; return current.has(key) ? current : new Set([...current, key]); }); }}>
                  <summary className="packing-box-main" data-guide={editable && !needsReplan && !reviewedBoxes.has(`${orderId}:${box.box_id}`) ? `Review Box ${box.box_number}` : undefined} data-guide-detail="Open the box and check products, packs and net weights. Edit a line only when it needs correcting.">
                    <span className="packing-box-number" aria-hidden>{String(box.box_number).padStart(2, "0")}</span>
                    <span className="packing-box-id"><strong>Box {box.box_number}</strong><small>{size ? `${size.size_name} · ${size.box_type.toLowerCase()}` : box.box_type_name || "Box"}</small></span>
                    <span className="packing-box-items"><span className="packing-product-preview">{preview || "No items yet"}{box.items.length > 2 && <small> +{box.items.length - 2} more</small>}</span><small>{box.items.length} {box.items.length === 1 ? "product" : "products"} · {packs} packs</small></span>
                    <span className="packing-box-saved"><strong>{box.items.reduce((total, item) => total + item.net_weight_kg, 0).toFixed(2)} <small>kg</small></strong><small>net weight</small></span>
                    <span key={state.label} className={`packing-box-state tone-${state.tone}`}>{box.is_locked ? <CheckCircle2 size={16} aria-hidden /> : <span aria-hidden />}{state.label}</span>
                    <ChevronRight className="packing-box-chevron" size={18} aria-hidden />
                  </summary>
                  <div className="packing-box-content">
                  <div className="packing-contents-heading"><h3>Packed items</h3><button type="button" className="invoice-secondary-button btn-sm" onClick={() => { setStickerCodeCopied(false); setPreviewBox(box); }}><QrCode size={15} aria-hidden />Sticker</button></div>
                  {editable && <div className="packing-box-tools">
                    <label>Box packing rules<select aria-label={`Box ${box.box_number} packing rules`} value={box.packing_mode || ""} disabled={busy || box.is_locked || !canEditPacking || box.needs_replan} onChange={event => void changePackingMode(box.box_id, event.target.value)}><option value="" disabled>{box.is_locked ? "Historical rules" : "Needs review"}</option><option value="EL">EL rules</option><option value="NON_EL">Non-EL rules</option></select></label>
                    <label>Box size
                      <select value={box.box_catalogue_id} disabled={busy || box.is_locked || !canEditPacking}
                        onChange={event => { const catalogue = boxCatalogues.find(item => item.id === event.target.value); if (catalogue) changeBoxSize(box.box_id, catalogue.id); }}>
                        {boxCatalogues.map(catalogue => <option value={catalogue.id} key={catalogue.id}>{catalogue.size_name} · {catalogue.box_type} ({catalogue.length_cm}×{catalogue.width_cm}×{catalogue.height_cm})</option>)}
                      </select>
                    </label>
                  </div>}
                  {box.needs_replan && <p className="message error">{box.replan_reasons?.join(". ")}</p>}
                  {editable && Boolean(box.layers?.length) && <div className="packing-layer-list"><strong>Pack from bottom to top</strong><ol>{box.layers!.map(item => <li key={item.item_id}>{item.layer}: {item.packed_count} × {item.product_name} {item.pack_size}{["RESEARCH", "PROXY"].includes(item.volume_source || "") && <small> (reviewed volume estimate)</small>}</li>)}</ol></div>}
                  <div className="packing-items-scroll"><table className="packing-items-table">
                    <thead><tr><th>Product</th><th>Pack</th><th className="numeric">Packed</th><th className="numeric">Net kg</th>{editable && <th scope="col"><span className="sr-only">Adjust</span></th>}</tr></thead>
                    <tbody>{box.items.map((item, index) => <tr key={index}>
                      <td className="packing-item-product"><strong>{item.produce_name}</strong><small>{item.product_code}</small></td><td data-label="Pack">{item.pack_size}</td>
                      <td className="numeric" data-label="Packed">{item.packed_count} packs</td><td className="numeric" data-label="Net kg">{item.net_weight_kg.toFixed(2)}</td>
                      {editable && <td><button type="button" className="ghost-button" disabled={busy || box.needs_replan} onClick={() => editItem(box.box_id, item)}>Edit / substitute</button></td>}
                    </tr>)}</tbody>
                  </table></div>
                  <div className="packing-box-foot">
                    {editable && <button type="button" className="invoice-secondary-button btn-sm" disabled={busy || box.needs_replan} onClick={() => editItem(box.box_id)}>Add packing item</button>}
                    <details className="packing-box-notes"><summary>Packing &amp; label details<ChevronDown size={14} aria-hidden /></summary><div>
                      <p><strong>Label</strong> {box.sticker_label || `${packingData.route_code}-${packingData.sub_customer_code} / ${box.box_number} of ${packingData.total_boxes_count}`}</p>
                      <p><strong>Rules</strong> {box.packing_mode === "EL" ? "EL" : box.packing_mode === "NON_EL" ? "Non-EL" : "Historical rules"} · <strong>Box</strong> {size ? `${size.size_name} · ${size.box_type} (${size.length_cm} × ${size.width_cm} × ${size.height_cm} cm)` : box.box_type_name}</p>
                      {box.usable_volume_cm3 ? <p><strong>Occupied volume</strong> {((box.volume_cm3 || 0) / 1000).toFixed(2)} L of {(box.usable_volume_cm3 / 1000).toFixed(2)} L{box.volume_utilization_pct != null && ` (${box.volume_utilization_pct.toFixed(1)}%)`}</p> : null}
                      {!editable && Boolean(box.layers?.length) && <div><strong>Pack from bottom to top</strong><ol>{box.layers!.map(item => <li key={item.item_id}>{item.layer}: {item.packed_count} × {item.product_name} {item.pack_size}</li>)}</ol></div>}
                      {box.allocation_explanation && <p>{box.allocation_explanation}</p>}
                    </div></details>
                  </div>
                  </div>
                </details>
              </li>;
            })}
          </ol>
          {canEditPacking && <div className="packing-action-bar" role="region" aria-label="Packing progress">
            <div className="packing-action-progress">
              <strong>{readyBoxes.length} of {packingData.boxes.length} boxes ready for review</strong>
              <span className="packing-progress-track" aria-hidden><span style={{ transform: `scaleX(${packingData.boxes.length ? readyBoxes.length / packingData.boxes.length : 0})` }} /></span>
            </div>
            <button type="button" className="primary-button" data-guide="Finalize the reviewed packing" data-guide-priority="40" data-guide-detail="Check all boxes before finalizing. This locks packed quantities and sends the shipment to accounting." disabled={busy || needsReplan || !packingData.boxes.length || readyBoxes.length !== packingData.boxes.length} onClick={() => void finalizePacking()}>Finalize Packing</button>
          </div>}

        </div>
      ) : requestedOrderId && !error ? (
        <TableLoadingSkeleton label="Loading packing order" columns={["Code", "Product", "Pack", "Packed", "Net kg", "Adjust"]} />
      ) : (
        <div className="panel packing-empty-state" style={{ textAlign: "center", padding: "40px" }}>
          <Boxes size={40} color="var(--muted)" style={{ margin: "0 auto 12px" }} />
          <h2>Choose the customer you are packing for</h2>
          <p style={{ color: "var(--muted)", maxWidth: "480px", margin: "0 auto 16px" }}>
            Select a customer order to prepare or resume its boxes, review contents, and finalize packing.
          </p>
          <Link className="primary-button" href="/packing/orders">Browse customer orders</Link>
        </div>
      )}

      {replanPreview && <TaskDialog title="Review replacement packing plan" description={replanPreview.warning} size="lg" busy={busy} onClose={() => setReplanPreview(null)}><StatusMessage error={replanError} /><p>{replanPreview.boxes.length} boxes using {replanPreview.packing_mode === "EL" ? "EL" : "Non-EL"} rules. Existing stickers will be replaced.</p><ol>{replanPreview.boxes.map(box => <li key={box.box_number}><strong>Box {box.box_number}: {boxCatalogues.find(c => c.id === box.box_catalogue_id)?.size_name || "Box"}</strong> ? {box.net_weight_kg.toFixed(2)} kg, {(box.volume_cm3 / 1000).toFixed(2)} L<ul>{box.items.map((item, index) => <li key={index}>{item.count} ? {item.product_name || "Product"} {item.pack_size}</li>)}</ul></li>)}</ol><div className="packing-setup-actions"><button type="button" disabled={busy} onClick={() => setReplanPreview(null)}>Keep current boxes</button><button type="button" className="primary-button" disabled={busy} onClick={() => void applyReplan()}>Apply reviewed plan and replace boxes</button></div></TaskDialog>}
      {itemEditor && <TaskDialog title={itemEditor.itemId ? "Adjust packing item" : "Add packing item"} description="Choose the product, pack size and actual quantity in this box. Review the updated contents before finalizing packing." busy={busy} onClose={() => setItemEditor(null)}>
          <StatusMessage error={error} />{revisionConflict && <button type="button" disabled={busy || transferBusy} onClick={() => void reloadLatestPacking()}>Reload latest packing for review</button>}
          <form onSubmit={saveItem} style={{ display: "grid", gap: 12 }}>
            <label>Find product <input value={itemSearch} onChange={event => { setItemSearch(event.target.value); setItemOffset(0); setItemSearchBusy(true); setItemSearchError(""); setItemProducts([]); }} /></label>
            <StatusMessage error={itemSearchError} /><div className="register-pagination"><button type="button" disabled={!itemOffset || itemSearchBusy} onClick={() => setItemOffset(value => Math.max(0, value - 100))}>Previous products</button><span>Products {itemOffset + 1}?{itemOffset + itemProducts.length}</span><button type="button" disabled={itemSearchBusy || itemProducts.length < 100} onClick={() => setItemOffset(value => value + 100)}>Next products</button></div>
            {itemSearchBusy && <p role="status">Searching products...</p>}
            {!itemSearchBusy && !itemSearchError && !itemProducts.length && <p>No matching products. Try a partial name or SKU.</p>}
            <label>Master product <select required value={itemProduct} onChange={event => { setItemProduct(event.target.value); setSelectedItemProduct(itemProducts.find(product => product.id === event.target.value) || null); }}>
              <option value="">Choose product</option>{selectedItemProduct && !itemProducts.some(product => product.id === selectedItemProduct.id) && <option value={selectedItemProduct.id}>{selectedItemProduct.t_code} · {selectedItemProduct.name_en || selectedItemProduct.name_vi}</option>}{itemProducts.map(product => <option value={product.id} key={product.id}>{product.t_code} · {product.name_en || product.name_vi}</option>)}
            </select></label>
            <label>Pack size <input required maxLength={64} value={itemPack} onChange={event => setItemPack(event.target.value)} /></label>
            <label>Packed quantity <input type="number" required min={itemEditor.itemId ? 0 : 1} step={1} value={itemQuantity} onChange={event => setItemQuantity(event.target.value)} /></label>
            <label>Total item net weight (kg) <input type="number" required min={0} step="0.0001" value={itemWeight} onChange={event => setItemWeight(event.target.value)} /></label>
            {itemEditor.itemId && <p>Quantity zero removes this item. Replacing the master product records a substitution.</p>}
            <p>Box totals are calculated from the saved items when packing is finalized.</p>
            <div className="task-dialog-actions"><button type="submit" className="primary-button" disabled={busy || !itemProduct}>Save packing adjustment</button>{" "}<button type="button" disabled={busy} onClick={() => setItemEditor(null)}>Cancel</button></div>
          </form>
        </TaskDialog>}
      {transferModalOpen && packingData && (
        <TaskDialog title="Move items between boxes" description="Combine what is left in a smaller box into a larger one. Products and weights move with them." size="sm" busy={transferBusy} onClose={() => setTransferModalOpen(false)}>
          <form className="task-dialog-pad dialog-form" onSubmit={handleTransferContents}>
            {(transferError || (sourceBoxId && eligibleTransferTargets.length === 0)) &&
              <p role="alert" className="message warning">{transferError || "These boxes cannot be combined. Choose a box of the same material with enough safe capacity."}</p>}
            {revisionConflict && <button type="button" disabled={transferBusy} onClick={() => void reloadLatestPacking()}>Reload latest packing for review</button>}
            <label>Move from box
              <select value={sourceBoxId} onChange={event => { setSourceBoxId(event.target.value); setTargetBoxId(""); setTransferError(""); }} required>
                <option value="">Choose a box</option>
                {packingData.boxes.filter(box => !box.is_locked && box.items.length > 0).map(box => <option key={box.box_id} value={box.box_id}>Box #{box.box_number} ({box.box_type_name} · {box.box_material} · {box.items.length} items · {box.items.reduce((total, item) => total + item.net_weight_kg, 0).toFixed(2)} kg)</option>)}
              </select>
            </label>
            <label>Into box
              <select value={targetBoxId} onChange={event => setTargetBoxId(event.target.value)} disabled={!sourceBoxId || eligibleTransferTargets.length === 0} required>
                <option value="">{sourceBoxId && eligibleTransferTargets.length === 0 ? "No compatible box" : "Choose a box"}</option>
                {eligibleTransferTargets.map(box => <option key={box.box_id} value={box.box_id}>Box #{box.box_number} ({box.box_type_name} · {box.box_material} · {box.items.length} items)</option>)}
              </select>
            </label>
            <div className="task-dialog-actions"><button type="button" className="invoice-secondary-button" onClick={() => setTransferModalOpen(false)}>Cancel</button>
              <button type="submit" className="primary-button" disabled={transferBusy || !sourceBoxId || !targetBoxId}>{transferBusy ? "Moving…" : "Move items"}</button></div>
          </form>
        </TaskDialog>
      )}

      {vehicleExport && (
        <TaskDialog title="Vehicle load check" description="Check the box totals per customer before loading the vehicle." size="md" onClose={() => setVehicleExport(null)}>
          <div className="task-dialog-pad">
            <ul className="vehicle-load-list">
              {vehicleExport.customers.map((customer, index) => <li key={`${customer.customer_code}-${index}`}><strong>{customer.customer_code}: {customer.boxes_count} boxes</strong>
                <span>Boxes #{customer.box_numbers.join(", #")} · {customer.total_net_kg.toFixed(2)} kg</span></li>)}
            </ul>
            <p className="message success">Total load: {vehicleExport.total_boxes_count} boxes · {vehicleExport.total_gross_weight_kg.toFixed(2)} kg gross</p>
            <div className="task-dialog-actions"><button type="button" className="invoice-secondary-button" onClick={() => setVehicleExport(null)}>Close</button>
              <button type="button" className="primary-button" onClick={downloadExportFile}><Download size={16} /> Download CSV</button></div>
          </div>
        </TaskDialog>
      )}

      {previewBox && (
        <TaskDialog title="Box sticker" description="Print the sticker or copy its code to label this box." size="sm" onClose={() => setPreviewBox(null)}>
          <div className="task-dialog-pad">
            <div className="print-sticker sticker-preview">
              <strong className="sticker-label">{previewBox.sticker_label}</strong>
              <div className="sticker-qr">{previewQrDataUrl ? <Image unoptimized src={previewQrDataUrl} alt={`QR code for ${previewBox.sticker_label}`} width={136} height={136} /> : <QrCode size={90} />}</div>
              <small>Sticker code</small>
              <code>{previewBox.qr_code_uid}</code>
              <p>Size <strong>{previewBox.box_type_name}</strong> · Net <strong>{previewBox.items.reduce((total, item) => total + item.net_weight_kg, 0).toFixed(2)} kg</strong></p>
            </div>
            <div className="task-dialog-actions"><button type="button" className="invoice-secondary-button" onClick={() => setPreviewBox(null)}>Close</button>
              <button type="button" onClick={copyStickerCode} className="invoice-secondary-button" aria-live="polite">{stickerCodeCopied ? "Copied" : "Copy code"}</button>
              <button type="button" onClick={() => window.print()} className="primary-button">Print sticker</button></div>
          </div>
        </TaskDialog>
      )}
    </div>
  );
}
