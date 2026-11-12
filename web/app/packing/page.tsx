"use client";

import { t, useLanguage, displayLocale } from "@/lib/language";

import { PackingItemEditor, type PackingItem } from "@/components/packing-item-editor";
import { PackingFitFeedback } from "@/components/packing-fit-feedback";
import styles from "./packing.module.css";

import { PageLoadingSkeleton, TableLoadingSkeleton } from "@/components/loading-skeleton";

import { Suspense, useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Boxes,
  ArrowRightLeft,
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

type BoxItem = PackingItem;

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
  volume_cm3?: number | null;
  history_evidence?: { source: string; pages: number[]; basis: string } | null;
  usable_volume_cm3?: number;
  volume_utilization_pct?: number | null;
  layers?: Array<{ item_id: string; product_code: string; product_name: string; pack_size: string; packed_count: number; firmness: string; layer: string; volume_source?: string }>;
};

type PackingListResponse = {
  client_order_id: string;
  raw_file_id?: string;
  file_name?: string;
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

type ReplanPreview = { preview_token: string; warning: string; packing_mode: string; boxes: Array<{ box_number: number; box_catalogue_id: string; net_weight_kg: number; volume_cm3: number | null; history_evidence?: { source: string; pages: number[] } | null; items: Array<{ product_id: string; product_name?: string; pack_size: string; count: number }> }> };

function packingStatus(status = "") {
  if (!status) return "Status unavailable";
  return ({ HANDOFF_01_SENT: "Not started", PACKING_DESIGNED: "Boxes prepared", FLOOR_PACKING: "Packing in progress", FINAL_PACK_LOCKED: "Locked: send to accounting", HANDOFF_02_RETURNED: "Packing complete" } as Record<string, string>)[status] || status.replaceAll("_", " ");
}

function displayCustomer(value: string) {
  return value.replace(/_+/g, " ").replace(/\s+/g, " ").trim() || "Current order";
}

function shipmentDate(value: string) {
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(displayLocale(), { day: "numeric", month: "short", year: "numeric" });
}

export default function PackingPage() {
  useLanguage(); return <Suspense fallback={<PageLoadingSkeleton label={t("Loading packing…")} />}><PackingContent /></Suspense>; }

function PackingContent() {
  useLanguage();
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


  const [previewBox, setPreviewBox] = useState<BoxDetail | null>(null);
  const [previewQrDataUrl, setPreviewQrDataUrl] = useState("");
  const [stickerCodeCopied, setStickerCodeCopied] = useState(false);
  const permissions = useSession()?.permissions ?? [];
  const orderRequest = useRef(0);
  const selectionBusy = useRef(false);
  const selectionRequest = useRef(0);
  const operationBusy = useRef(false);
  const [itemEditor, setItemEditor] = useState<{ boxId: string; item?: PackingItem; revision: number } | null>(null);
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

  function editItem(boxId: string, item?: BoxItem) {
    if (!packingData || revisionConflict) return;
    setItemEditor({ boxId, item, revision: packingData.packing_revision }); setError("");
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
    <div className={`page packing-workspace packing-desk ${styles.workspace}`}>
      <div className="packing-back"><ArrowLeft size={15} aria-hidden /><RegisterBackLink fallback="/packing/orders" label={t("Packing orders")} /></div>
      <header className="packing-workspace-heading">
        <div className="packing-title-row"><span className={styles.titleIcon}><Boxes size={24} strokeWidth={1.7} aria-hidden /></span><h1>{t("Packing desk")}</h1>{packingData && <span key={orderStatus} className={`packing-order-status${packingComplete ? " is-complete" : ""}`}>
          {packingComplete ? <CheckCircle2 size={16} aria-hidden /> : <span className="packing-status-dot" aria-hidden />}{packingStatus(orderStatus)}
        </span>}</div>
        {!packingData && !requestedOrderId && <Link className="invoice-secondary-button" href={packingQueue}>{t("Choose an order")}<ArrowRight size={16} aria-hidden /></Link>}
        {packingComplete && (permissions.includes("manage_billing") && packingData!.boxes.length > 0
          ? <Link className="primary-button packing-next-action" href={`/billing?orderId=${encodeURIComponent(packingData!.client_order_id)}`}><Receipt size={17} aria-hidden />{t("Continue to billing")}<ArrowRight size={16} aria-hidden /></Link>
          : <Link className="primary-button packing-next-action" href={packingQueue}>{t("Pack next order")}<ArrowRight size={16} aria-hidden /></Link>)}
        {orderStatus === "FINAL_PACK_LOCKED" && <button type="button" className="primary-button packing-next-action" disabled={busy} onClick={() => void finalizePacking()}>{t("Send Final Packing to Accounting")}<ArrowRight size={16} aria-hidden /></button>}
      </header>

      <StatusMessage error={error} success={message} />
      {revisionConflict && <button type="button" data-guide={t("Reload the latest packing before editing")} data-guide-priority="1" disabled={busy || transferBusy} onClick={() => void reloadLatestPacking()}>{t("Reload latest packing for review")}</button>}
      {requestedBox && packingData && !packingData.boxes.some(box => box.qr_code_uid === requestedBox) && <StatusMessage error="This box is not part of the selected order. Scan the original sticker again." />}
      {packingData && <section className="packing-order-overview" aria-label={t("Current task")}>
        <div className="packing-order-context">
          <div className="packing-customer">
            {packingData.file_name && <p className={styles.sourceFile}>{t("Order file: ")}<Link href={packingData.raw_file_id ? `/packing/orders?raw_file_id=${encodeURIComponent(packingData.raw_file_id)}&view=all` : packingQueue}>{packingData.file_name}</Link></p>}
            <h2 title={packingData.sub_customer_code}>{displayCustomer(packingData.sub_customer_code)}</h2>
            <p className="packing-order-meta"><span>{packingData.boxes.length} {packingData.boxes.length === 1 ? t("box") : t("boxes")}</span><span>{totalNetWeight.toFixed(2)} {t("kg net")}</span><time dateTime={packingData.shipment_date}>{shipmentDate(packingData.shipment_date)}</time></p>
          </div>
          <details className="packing-order-details" name="packing-order-menu"><summary>{t("Order details")}<ChevronDown size={14} aria-hidden /></summary>
            <div className="packing-order-detail-body"><dl><div><dt>{t("Order file")}</dt><dd>{packingData.file_name || t("Source filename unavailable")}</dd></div><div><dt>{t("Route")}</dt><dd>{packingData.route_code}</dd></div></dl>
              <WorkflowProgress current="packing" done links={packingComplete ? { billing: `/billing?orderId=${encodeURIComponent(orderId)}` } : {}} />
            </div>
          </details>
        </div>
        <div className="packing-order-footer">
          <p className="packing-stage-note">{packingComplete ? <><Check size={16} aria-hidden />{t("Packing complete. Ready for accounting.")}</> : canEditPacking ? t("Open a box to review or adjust its contents.") : orderStatus === "FINAL_PACK_LOCKED" ? t("Boxes are locked and ready to send to accounting.") : t("Choose packing rules to prepare this order.")}</p>
          <details className="packing-secondary-tools" name="packing-order-menu"><summary>{t("Order tools")}<ChevronDown size={14} aria-hidden /></summary><div className="packing-secondary-actions">
            {canEditPacking && packingData.boxes.length > 1 && <button type="button" disabled={busy} onClick={() => { setSourceBoxId(""); setTargetBoxId(""); setTransferRevision(packingData.packing_revision); setTransferError(""); setTransferModalOpen(true); }}><ArrowRightLeft size={16} aria-hidden />{t("Move items between boxes")}</button>}
            <Link href="/packing/setup">{t("Products & box settings")}</Link>
            <Link href={packingQueue}>{packingComplete ? t("Pack next order") : t("Change order")}</Link>
          </div></details>
        </div>
      </section>}

      {packingData && orderStatus === "HANDOFF_01_SENT" && <section className="packing-mode-start" aria-label={t("Choose packing rules")}><h2>{t("Choose the rules for these boxes")}</h2><p>{t("Staff choose EL or Non-EL. The destination country does not select this mode.")}</p><label>{t("Packing rules")}<select data-guide={!packingMode ? t("Choose EL or Non-EL packing rules") : undefined} data-guide-detail={t("Choose the rules for this shipment. Country alone does not determine EL or Non-EL.")} value={packingMode} onChange={event => setPackingMode(event.target.value)}><option value="">{t("Choose EL or Non-EL")}</option><option value="EL">{t("EL rules")}</option><option value="NON_EL">{t("Non-EL rules")}</option></select></label><button type="button" className="primary-button" disabled={busy || !packingMode} data-guide={packingMode ? t("Generate the packing boxes") : undefined} onClick={() => void generateBoxes()}>{t("Generate packing boxes")}</button></section>}
      {canEditPacking && <details className={`packing-mode-start ${styles.plan}`} aria-label={t("Review packing plan")} open={needsReplan || undefined}><summary><SlidersHorizontal size={16} aria-hidden /><strong>{needsReplan ? t("Packing data changed") : t("Review another box plan")}</strong><ChevronDown size={16} aria-hidden /></summary>{needsReplan && <p role="alert">{t("Review and apply a current plan before finalizing. Your current packed quantities remain unchanged until you apply a preview.")}</p>}<label>{t("Rules for the reviewed plan")}<select data-guide={needsReplan && !packingMode ? t("Choose rules for the replacement plan") : undefined} data-guide-priority="10" value={packingMode} onChange={event => setPackingMode(event.target.value)}><option value="">{t("Choose EL or Non-EL")}</option><option value="EL">{t("EL rules")}</option><option value="NON_EL">{t("Non-EL rules")}</option></select></label><button type="button" data-guide={needsReplan ? t("Preview the updated packing plan") : undefined} data-guide-priority="10" data-guide-detail={t("Review the proposed boxes before applying them. Your saved quantities stay unchanged until you apply the plan.")} disabled={busy || !packingMode} onClick={() => void previewReplan()}>{t("Preview replan")}</button></details>}
      {/* Review each box and open details to adjust contents. */}
      {packingData ? (
        <div className="packing-boxes-section">
          <div className="packing-list-head"><h2>{t("Boxes ")}<span className="packing-box-count">{packingData.boxes.length}</span></h2>
            {packingData.boxes.length > 1 && <div className="packing-list-controls"><label className="packing-box-search"><Search size={17} aria-hidden /><span className="sr-only">{t("Find box or product")}</span><input type="search" placeholder={t("Find a box or product")} value={boxSearch} onChange={event => { setBoxSearch(event.target.value); setBoxPage(0); }} /></label>
              {!compact && <details className="packing-list-options"><summary aria-label={t("View options")}><SlidersHorizontal size={18} aria-hidden /></summary><label>{t("Boxes per page")}<select value={boxLimit} onChange={event => { setBoxLimit(Number(event.target.value)); setBoxPage(0); }}>{[25, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label></details>}
            </div>}
          </div>
          {(matchingBoxes.length > pageSize || boxSearch) && <div className="register-pagination box-navigation"><span>{compact ? t(`Box ${matchingBoxes.length ? visibleStart + 1 : 0} of ${matchingBoxes.length}`) : t(`${matchingBoxes.length} matching boxes · Page ${Math.floor(visibleStart / pageSize) + 1}`)}</span>{matchingBoxes.length > pageSize && <><button type="button" disabled={visibleStart === 0} onClick={() => setBoxPage(Math.max(0, visibleStart - pageSize))}>{compact ? t("Previous box") : t("Previous boxes")}</button><button type="button" disabled={visibleStart + pageSize >= matchingBoxes.length} onClick={() => setBoxPage(visibleStart + pageSize)}>{compact ? t("Next box") : t("Next boxes")}</button></>}</div>}
          {!matchingBoxes.length && <p role="status">{t("No matching boxes. Clear the search to see all boxes.")}</p>}

          <ol id="packing-boxes" className="packing-box-list">
            {visibleBoxes.map(box => {
              const state = box.needs_replan && !box.is_locked ? { label: "Needs replan", tone: "progress" } : box.is_locked ? { label: "Locked", tone: "success" } : box.items.length ? { label: "Ready for review", tone: "info" } : { label: "Needs contents", tone: "progress" };
              const size = boxCatalogues.find(catalogue => catalogue.id === box.box_catalogue_id);
              const editable = canEditPacking && !box.is_locked;
              const packs = box.items.reduce((total, item) => total + item.packed_count, 0);
              const preview = box.items.slice(0, 2).map(item => item.produce_name.split(" / ").at(-1) || item.produce_name).join(" · ");
              return <li key={box.box_id} id={`box-${box.box_id}`} tabIndex={-1} className={`packing-box-row${box.qr_code_uid === requestedBox ? " scanned-box" : ""}${box.is_locked ? " is-locked" : ""}`}>
                <details className="packing-box-details" name="packing-box" open={box.qr_code_uid === requestedBox || undefined} onToggle={event => { if (event.currentTarget.open) setReviewedBoxes(current => { const key = `${orderId}:${box.box_id}`; return current.has(key) ? current : new Set([...current, key]); }); }}>
                  <summary className="packing-box-main" data-guide={editable && !needsReplan && !reviewedBoxes.has(`${orderId}:${box.box_id}`) ? t(`Review Box ${box.box_number}`) : undefined} data-guide-detail={t("Open the box and check products, packs and net weights. Edit a line only when it needs correcting.")}>
                    <span className="packing-box-number" aria-hidden>{String(box.box_number).padStart(2, "0")}</span>
                    <span className="packing-box-id"><strong>{t("Box ")}{box.box_number}</strong><small>{size ? `${size.size_name} · ${t(size.box_type.toLowerCase())}` : box.box_type_name || t("Box")}</small></span>
                    <span className="packing-box-items"><span className="packing-product-preview">{preview || t("No items yet")}{box.items.length > 2 && <small> +{box.items.length - 2} {t("more")}</small>}</span><small>{box.items.length} {box.items.length === 1 ? t("product") : t("products")} · {packs} {t("packs")}</small></span>
                    <span className="packing-box-saved"><strong>{box.items.reduce((total, item) => total + item.net_weight_kg, 0).toFixed(2)} <small>{t("kg")}</small></strong><small>{t("net weight")}</small></span>
                    <span key={state.label} className={`packing-box-state tone-${state.tone}`}>{box.is_locked ? <CheckCircle2 size={16} aria-hidden /> : <span aria-hidden />}{t(state.label)}</span>
                    <ChevronRight className="packing-box-chevron" size={18} aria-hidden />
                  </summary>
                  <div className="packing-box-content">
                  <div className="packing-contents-heading"><h3>{t("Packed items")}</h3><div>{permissions.includes("floor_packing") && <PackingFitFeedback key={`${box.box_id}:${packingData.packing_revision}`} boxId={box.box_id} boxNumber={box.box_number} revision={packingData.packing_revision} />}<button type="button" className="invoice-secondary-button btn-sm" onClick={() => { setStickerCodeCopied(false); setPreviewBox(box); }}><QrCode size={15} aria-hidden />{t("Sticker")}</button></div></div>
                  {editable && <div className="packing-box-tools">
                    <label>{t("Box packing rules")}<select aria-label={t(`Box ${box.box_number} packing rules`)} value={box.packing_mode || ""} disabled={busy || box.is_locked || !canEditPacking || box.needs_replan} onChange={event => void changePackingMode(box.box_id, event.target.value)}><option value="" disabled>{box.is_locked ? t("Historical rules") : t("Needs review")}</option><option value="EL">{t("EL rules")}</option><option value="NON_EL">{t("Non-EL rules")}</option></select></label>
                    <label>{t("Box size")}<select value={box.box_catalogue_id} disabled={busy || box.is_locked || !canEditPacking}
                        onChange={event => { const catalogue = boxCatalogues.find(item => item.id === event.target.value); if (catalogue) changeBoxSize(box.box_id, catalogue.id); }}>
                        {boxCatalogues.map(catalogue => <option value={catalogue.id} key={catalogue.id}>{catalogue.size_name} · {catalogue.box_type} ({catalogue.length_cm}×{catalogue.width_cm}×{catalogue.height_cm})</option>)}
                      </select>
                    </label>
                  </div>}
                  {box.needs_replan && <p className="message error">{box.replan_reasons?.join(". ")}</p>}
                  {editable && Boolean(box.layers?.length) && <div className="packing-layer-list"><strong>{t("Pack from bottom to top")}</strong><ol>{box.layers!.map(item => <li key={item.item_id}>{item.layer}: {item.packed_count} × {item.product_name} {item.pack_size}{["RESEARCH", "PROXY"].includes(item.volume_source || "") && <small> {t("(reviewed volume estimate)")}</small>}</li>)}</ol></div>}
                  <div className="packing-items-scroll"><table className="packing-items-table">
                    <thead><tr><th>{t("Product")}</th><th>{t("Pack")}</th><th className="numeric">{t("Packed")}</th><th className="numeric">{t("Net kg")}</th>{editable && <th scope="col"><span className="sr-only">{t("Adjust")}</span></th>}</tr></thead>
                    <tbody>{box.items.map((item, index) => <tr key={index}>
                      <td className="packing-item-product"><strong>{item.produce_name}</strong><small>{item.product_code}</small></td><td data-label={t("Pack")}>{item.pack_size}</td>
                      <td className="numeric" data-label={t("Packed")}>{item.packed_count} {t("packs")}</td><td className="numeric" data-label={t("Net kg")}>{item.net_weight_kg.toFixed(2)}</td>
                      {editable && <td><button type="button" className="ghost-button" disabled={busy || box.needs_replan} onClick={() => editItem(box.box_id, item)}>{t("Edit / substitute")}</button></td>}
                    </tr>)}</tbody>
                  </table></div>
                  <div className="packing-box-foot">
                    {editable && <button type="button" className="invoice-secondary-button btn-sm" disabled={busy || box.needs_replan} onClick={() => editItem(box.box_id)}>{t("Add packing item")}</button>}
                    <details className="packing-box-notes"><summary>{t("Packing & label details")}<ChevronDown size={14} aria-hidden /></summary><div>
                      <p><strong>{t("Label")}</strong> {box.sticker_label || t(`${packingData.route_code}-${packingData.sub_customer_code} / ${box.box_number} of ${packingData.total_boxes_count}`)}</p>
                      <p><strong>{t("Rules")}</strong> {box.packing_mode === "EL" ? t("EL") : box.packing_mode === "NON_EL" ? t("Non-EL") : t("Historical rules")} · <strong>{t("Box")}</strong> {size ? t(`${size.size_name} · ${size.box_type} (${size.length_cm} × ${size.width_cm} × ${size.height_cm} cm)`) : box.box_type_name}</p>
                      {box.history_evidence && <p><strong>{t("Reviewed historical combination")}</strong> {box.history_evidence.source} {t("· pages ")}{box.history_evidence.pages.join(", ")}{t(". Occupied volume has not been measured.")}</p>}
                      {box.usable_volume_cm3 && box.volume_cm3 != null ? <p><strong>{t("Occupied volume")}</strong> {(box.volume_cm3 / 1000).toFixed(2)} {t("L of ")}{(box.usable_volume_cm3 / 1000).toFixed(2)} {t("L")}{box.volume_utilization_pct != null && ` (${box.volume_utilization_pct.toFixed(1)}%)`}</p> : null}
                      {!editable && Boolean(box.layers?.length) && <div><strong>{t("Pack from bottom to top")}</strong><ol>{box.layers!.map(item => <li key={item.item_id}>{item.layer}: {item.packed_count} × {item.product_name} {item.pack_size}</li>)}</ol></div>}
                      {box.allocation_explanation && <p>{box.allocation_explanation}</p>}
                    </div></details>
                  </div>
                  </div>
                </details>
              </li>;
            })}
          </ol>
          {canEditPacking && <div className="packing-action-bar" role="region" aria-label={t("Packing progress")}>
            <div className="packing-action-progress">
              <strong>{readyBoxes.length} {t("of ")}{packingData.boxes.length} {t("boxes ready for review")}</strong>
              <span className="packing-progress-track" aria-hidden><span style={{ transform: `scaleX(${packingData.boxes.length ? readyBoxes.length / packingData.boxes.length : 0})` }} /></span>
            </div>
            <button type="button" className="primary-button" data-guide={t("Finalize the reviewed packing")} data-guide-priority="40" data-guide-detail={t("Check all boxes before finalizing. This locks packed quantities and sends the shipment to accounting.")} disabled={busy || needsReplan || !packingData.boxes.length || readyBoxes.length !== packingData.boxes.length} onClick={() => void finalizePacking()}>{t("Finalize Packing")}</button>
          </div>}

        </div>
      ) : requestedOrderId && !error ? (
        <TableLoadingSkeleton label={t("Loading packing order")} columns={["Code", "Product", "Pack", "Packed", "Net kg", "Adjust"]} />
      ) : (
        <div className="panel packing-empty-state" style={{ textAlign: "center", padding: "40px" }}>
          <Boxes size={40} color="var(--muted)" style={{ margin: "0 auto 12px" }} />
          <h2>{t("Choose the customer you are packing for")}</h2>
          <p style={{ color: "var(--muted)", maxWidth: "480px", margin: "0 auto 16px" }}>
            {t("Select a customer order to prepare or resume its boxes, review contents, and finalize packing.")}</p>
          <Link className="primary-button" href="/packing/orders">{t("Browse customer orders")}</Link>
        </div>
      )}

      {replanPreview && <TaskDialog title={t("Review replacement packing plan")} description={replanPreview.warning} size="lg" busy={busy} onClose={() => setReplanPreview(null)}><StatusMessage error={replanError} /><p>{replanPreview.boxes.length} {t("boxes using ")}{replanPreview.packing_mode === "EL" ? t("EL") : t("Non-EL")} {t("rules. Existing stickers will be replaced.")}</p><ol>{replanPreview.boxes.map(box => <li key={box.box_number}><strong>{t("Box ")}{box.box_number}: {boxCatalogues.find(c => c.id === box.box_catalogue_id)?.size_name || t("Box")}</strong> ? {box.net_weight_kg.toFixed(2)} {t("kg, ")}{box.volume_cm3 == null ? t("reviewed historical fit") : t(`${(box.volume_cm3 / 1000).toFixed(2)} L`)}<ul>{box.items.map((item, index) => <li key={index}>{item.count} ? {item.product_name || t("Product")} {item.pack_size}</li>)}</ul></li>)}</ol><div className="packing-setup-actions"><button type="button" disabled={busy} onClick={() => setReplanPreview(null)}>{t("Keep current boxes")}</button><button type="button" className="primary-button" disabled={busy} onClick={() => void applyReplan()}>{t("Apply reviewed plan and replace boxes")}</button></div></TaskDialog>}
      {itemEditor && <PackingItemEditor {...itemEditor} onClose={() => setItemEditor(null)} onConflict={() => setRevisionConflict(true)} onReload={reloadLatestPacking} onSaved={async () => {
        setItemEditor(null);
        if (await loadPackingList(orderId)) setMessage("Packing item saved. Review the contents before finalizing packing.");
        else setRevisionConflict(true);
      }} />}
      {transferModalOpen && packingData && (
        <TaskDialog title={t("Move items between boxes")} description={t("Combine what is left in a smaller box into a larger one. Products and weights move with them.")} size="sm" busy={transferBusy} onClose={() => setTransferModalOpen(false)}>
          <form className="task-dialog-pad dialog-form" onSubmit={handleTransferContents}>
            {(transferError || (sourceBoxId && eligibleTransferTargets.length === 0)) &&
              <p role="alert" className="message warning">{transferError || t("These boxes cannot be combined. Choose a box of the same material with enough safe capacity.")}</p>}
            {revisionConflict && <button type="button" disabled={transferBusy} onClick={() => void reloadLatestPacking()}>{t("Reload latest packing for review")}</button>}
            <label>{t("Move from box")}<select value={sourceBoxId} onChange={event => { setSourceBoxId(event.target.value); setTargetBoxId(""); setTransferError(""); }} required>
                <option value="">{t("Choose a box")}</option>
                {packingData.boxes.filter(box => !box.is_locked && box.items.length > 0).map(box => <option key={box.box_id} value={box.box_id}>{t("Box #")}{box.box_number} ({box.box_type_name} · {box.box_material} · {box.items.length} {t("items · ")}{box.items.reduce((total, item) => total + item.net_weight_kg, 0).toFixed(2)} {t("kg)")}</option>)}
              </select>
            </label>
            <label>{t("Into box")}<select value={targetBoxId} onChange={event => setTargetBoxId(event.target.value)} disabled={!sourceBoxId || eligibleTransferTargets.length === 0} required>
                <option value="">{sourceBoxId && eligibleTransferTargets.length === 0 ? t("No compatible box") : t("Choose a box")}</option>
                {eligibleTransferTargets.map(box => <option key={box.box_id} value={box.box_id}>{t("Box #")}{box.box_number} ({box.box_type_name} · {box.box_material} · {box.items.length} {t("items)")}</option>)}
              </select>
            </label>
            <div className="task-dialog-actions"><button type="button" className="invoice-secondary-button" onClick={() => setTransferModalOpen(false)}>{t("Cancel")}</button>
              <button type="submit" className="primary-button" disabled={transferBusy || !sourceBoxId || !targetBoxId}>{transferBusy ? t("Moving…") : t("Move items")}</button></div>
          </form>
        </TaskDialog>
      )}

      {previewBox && (
        <TaskDialog title={t("Box sticker")} description={t("Print the sticker or copy its code to label this box.")} size="sm" onClose={() => setPreviewBox(null)}>
          <div className="task-dialog-pad">
            <div className="print-sticker sticker-preview">
              <strong className="sticker-label">{previewBox.sticker_label}</strong>
              <div className="sticker-qr">{previewQrDataUrl ? <Image unoptimized src={previewQrDataUrl} alt={t(`QR code for ${previewBox.sticker_label}`)} width={136} height={136} /> : <QrCode size={90} />}</div>
              <small>{t("Sticker code")}</small>
              <code>{previewBox.qr_code_uid}</code>
              <p>{t("Size ")}<strong>{previewBox.box_type_name}</strong> {t("· Net ")}<strong>{previewBox.items.reduce((total, item) => total + item.net_weight_kg, 0).toFixed(2)} {t("kg")}</strong></p>
            </div>
            <div className="task-dialog-actions"><button type="button" className="invoice-secondary-button" onClick={() => setPreviewBox(null)}>{t("Close")}</button>
              <button type="button" onClick={copyStickerCode} className="invoice-secondary-button" aria-live="polite">{stickerCodeCopied ? t("Copied") : t("Copy code")}</button>
              <button type="button" onClick={() => window.print()} className="primary-button">{t("Print sticker")}</button></div>
          </div>
        </TaskDialog>
      )}
    </div>
  );
}
