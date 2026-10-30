"use client";

import { Suspense, useCallback, useState, useEffect, useRef } from "react";
import { PageHeader } from "@/components/page-header";
import { HintedButton } from "@/components/disabled-hint";
import {
  FileSpreadsheet,
  Upload,
  CheckCircle2,
  Split,
  Store,
  ArrowRight,
  AlertTriangle,
  PlusCircle,
  Download,
  Layers,
  Search,
  ChevronDown,
  CircleHelp,
  Pause,
  Play
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api, apiBlob, ApiError } from "@/lib/api";
import { beginSubmission, pendingSubmission, finishSubmission, type Submission } from "@/lib/submission";
import { fingerprintUpload, requireOriginalUpload, type UploadDetails } from "@/lib/upload-submission";
import { useSession } from "@/lib/session-context";
import { PRODUCT_CATEGORIES } from "@/lib/product-categories";
import { StatusMessage } from "@/components/status-message";
import { RecordRegister, StatusChip, recordLink, type RecordPage } from "@/components/record-register";
import { orderColumns } from "@/lib/register-columns";
import { TaskDialog } from "@/components/task-dialog";
import { WorkflowProgress } from "@/components/workflow-guidance";
import { OrderQuantityReview } from "@/components/order-quantity-review";
import type { QuantityReview } from "@/lib/order-quantities";
import { useGuidanceHints } from "@/lib/guidance-hints";
import { OrderTemplateReview } from "@/components/order-template-review";
import { OrderDisclosure } from "@/components/order-disclosure";
import { LoadingSkeleton, PageLoadingSkeleton } from "@/components/loading-skeleton";

type SubCustomerDetail = {
  client_code: string;
  route_group: string;
  sheets: string[];
};

type UnresolvedProduct = {
  product_code: string;
  name_vi?: string;
  name_en?: string;
  pack_size?: string;
  display_name?: string;
  suggestions?: Array<MasterProduct & { score: number }>;
};

type MasterProduct = {
  id: string;
  t_code: string;
  name_vi: string;
  name_en: string;
};

type MainCustomer = {
  id: string;
  client_code: string;
  company_name: string;
  is_main_customer: boolean;
  route_group?: string;
};

type ValidationResponse = {
  unresolved: UnresolvedProduct[];
  can_split: boolean;
  quantity_issue_count?: number;
  quantity_review?: QuantityReview;
};

type Ack = {
  raw_file_id: string;
  file_name: string;
  main_customer_id?: string;
  total_sub_customers_detected: number;
  sub_customer_codes: string[];
  sheets_detected: Array<{ sheet_name: string; sub_customer_count: number }>;
  group_summary?: Record<string, number>;
  sub_customer_details?: SubCustomerDetail[];
  unresolved_products?: UnresolvedProduct[];
  can_split?: boolean;
  verification_notice: string;
  status: string;
};

type SplitResponse = {
  raw_file_id: string;
  batch_id?: string;
  status: string;
  total_client_orders_created: number;
  total_line_items_created: number;
  total_estimated_weight_kg: number;
  sample_orders: Array<{
    order_ref_id: string;
    client_code: string;
    items_count: number;
    estimated_net_weight_kg: number;
  }>;
};

export default function OrdersPage() { return <Suspense fallback={<PageLoadingSkeleton label="Opening order…" />}><OrdersContent /></Suspense>; }

function OrdersContent() {
  const requestedFileId = useSearchParams().get("fileId");
  return <OrdersWorkspace key={requestedFileId || "new-order"} requestedFileId={requestedFileId} />;
}

function OrdersWorkspace({ requestedFileId }: { requestedFileId: string | null }) {
  const session = useSession();
  const intakeRequest = useRef(0);
  const [intakeRead, setIntakeRead] = useState<"opening" | "checking" | "ready" | "error">(requestedFileId ? "opening" : "ready");
  const [file, setFile] = useState<File>();
  const uploadFileInput = useRef<HTMLInputElement>(null);
  const [shipmentDate, setShipmentDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [route, setRoute] = useState("TETE");
  const [savedRoutes, setSavedRoutes] = useState<string[]>([]);
  const [customRoute, setCustomRoute] = useState(false);
  const [routeError, setRouteError] = useState("");
  const [routeRetry, setRouteRetry] = useState(0);
  const [result, setResult] = useState<Ack>();
  const uploadInFlight = useRef(false);
  const [uploadPending, setUploadPending] = useState<Submission | null>(null);
  const activeUploadPending = uploadPending?.actor === session?.user_id ? uploadPending : null;
  const [splitResult, setSplitResult] = useState<SplitResponse>();
  const [unresolved, setUnresolved] = useState<UnresolvedProduct[]>([]);
  const [quantityIssueCount, setQuantityIssueCount] = useState(0);
  const [quantityReview, setQuantityReview] = useState<QuantityReview>();
  const [masterProducts, setMasterProducts] = useState<MasterProduct[]>([]);
  const [mainCustomers, setMainCustomers] = useState<MainCustomer[]>([]);
  const [mainCustomerId, setMainCustomerId] = useState("");
  const [customerSearch, setCustomerSearch] = useState("");
  const [customerOffset, setCustomerOffset] = useState(0);
  const [customerRetry, setCustomerRetry] = useState(0);
  const [customerTotal, setCustomerTotal] = useState(0);
  const [customerLoading, setCustomerLoading] = useState(false);
  const [selectedCustomer, setSelectedCustomer] = useState<MainCustomer>();
  const [showNewCustomer, setShowNewCustomer] = useState(false);
  const [customerBusy, setCustomerBusy] = useState(false);
  const [customerError, setCustomerError] = useState("");
  const [customerSearchError, setCustomerSearchError] = useState("");
  const [newCustomerCode, setNewCustomerCode] = useState("");
  const [newCustomerName, setNewCustomerName] = useState("");
  const [newCustomerCountry, setNewCustomerCountry] = useState("");
  const [newCustomerEmail, setNewCustomerEmail] = useState("");
  const [productSearch, setProductSearch] = useState("");
  const [productOffset, setProductOffset] = useState(0);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const { enabled: hintsEnabled, toggle: toggleHints } = useGuidanceHints();
  const [activeGroup, setActiveGroup] = useState<string>("ALL");

  const [mappingModalItem, setMappingModalItem] = useState<UnresolvedProduct | null>(null);
  const [selectedCatalogProduct, setSelectedCatalogProduct] = useState<MasterProduct | null>(null);
  const selectedProductId = selectedCatalogProduct?.id || "";
  const [productLoading, setProductLoading] = useState(false);
  const [productSearchError, setProductSearchError] = useState("");
  const [productRetry, setProductRetry] = useState(0);
  const [mappingBusy, setMappingBusy] = useState(false);
  const [mappingError, setMappingError] = useState("");
  const [mappingMode, setMappingMode] = useState<"select" | "create">("select");
  const [newCode, setNewCode] = useState("");
  const [newNameEn, setNewNameEn] = useState("");
  const [newNameVi, setNewNameVi] = useState("");
  const [newNameTh, setNewNameTh] = useState("");
  const [newProduceType, setNewProduceType] = useState("");
  const [newBoxMaterial, setNewBoxMaterial] = useState("");
  const [newTemperature, setNewTemperature] = useState("");
  const [newWeightClass, setNewWeightClass] = useState("LIGHT");
  const [newControlStatus, setNewControlStatus] = useState("COMBINABLE");
  const [newPackSize, setNewPackSize] = useState("100g");

  // Manual order entry state
  const [adhocCustomer, setAdhocCustomer] = useState("");
  const [adhocProduct, setAdhocProduct] = useState("");
  const [adhocPackSize, setAdhocPackSize] = useState("100g");
  const [adhocQty, setAdhocQty] = useState(1);
  const manualProductInput = useRef<HTMLInputElement>(null);
  const [adhocBusy, setAdhocBusy] = useState(false);
  const adhocInFlight = useRef(false);
  const [adhocPending, setAdhocPending] = useState<Submission | null>(null);
  const adhocPath = result ? `/orders/${result.raw_file_id}/add-adhoc-order` : "";
  const activeAdhocPending = adhocPending?.actor === session?.user_id && adhocPending?.path === adhocPath ? adhocPending : null;
  useEffect(() => {
    if (!adhocPath || !session?.user_id) return;
    const timer = window.setTimeout(() => {
      try {
        const pending = pendingSubmission(window.sessionStorage, session.user_id, adhocPath);
        setAdhocPending(pending);
        if (pending) {
          const body = JSON.parse(pending.body);
          setAdhocCustomer(body.client_code); setAdhocProduct(body.items[0].t_code);
          setAdhocPackSize(body.items[0].pack_size); setAdhocQty(body.items[0].pack_count);
        }
      } catch { setError("The pending addition could not be recovered. Check this order before entering it again."); }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [adhocPath, session?.user_id]);
  const routeOptions = Array.from(new Set([
    ...savedRoutes,
    ...(!customRoute && route.trim() ? [route.trim().toUpperCase()] : []),
    selectedCustomer?.route_group || "",
    ...mainCustomers.map(customer => customer.route_group || ""),
  ].map(value => value.trim().toUpperCase()))).filter(Boolean).sort();

  const selectCustomer = useCallback((customer?: MainCustomer) => {
    setMainCustomerId(customer?.id || "");
    setSelectedCustomer(customer);
    if (customer?.route_group?.trim()) {
      setRoute(customer.route_group.trim().toUpperCase());
      setCustomRoute(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api<string[]>("/master-data/order-routes", { signal: controller.signal })
      .then(routes => {
        if (controller.signal.aborted) return;
        setSavedRoutes(routes);
        setRouteError("");
      })
      .catch(() => {
        if (!controller.signal.aborted) setRouteError("Saved routes could not be loaded. Retry or enter a route below.");
      });
    return () => controller.abort();
  }, [routeRetry]);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      setCustomerLoading(true); setCustomerSearchError("");
      api<RecordPage<MainCustomer>>(`/master-data/customer-register?q=${encodeURIComponent(customerSearch)}&offset=${customerOffset}&limit=25&sort=name&direction=asc`)
        .then(page => { if (!active) return; setMainCustomers(page.items); setCustomerTotal(page.total); if (page.total === 1 && !mainCustomerId) selectCustomer(page.items[0]); })
        .catch(reason => { if (active) setCustomerSearchError(reason instanceof Error ? reason.message : "Unable to find customers"); })
        .finally(() => { if (active) setCustomerLoading(false); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [customerSearch, customerOffset, mainCustomerId, customerRetry, selectCustomer]);


  async function createMainCustomer(event: React.FormEvent) {
    event.preventDefault();
    setCustomerBusy(true);
    setCustomerError("");
    try {
      const customer = await api<MainCustomer>("/master-data/customers", {
        method: "POST",
        body: JSON.stringify({ client_code: newCustomerCode.trim(), company_name: newCustomerName.trim(),
          route_group: route, destination_country: newCustomerCountry.trim(), billing_email: newCustomerEmail.trim() }),
      });
      setMainCustomers(customers => [...customers, customer].sort((a, b) => a.client_code.localeCompare(b.client_code)));
      selectCustomer(customer);
      setCustomerSearch("");
      setShowNewCustomer(false);
      setSuccess(`${customer.company_name} was added and selected.`);
    } catch (reason) {
      setCustomerError(reason instanceof Error ? reason.message : "Unable to add the customer account");
    } finally {
      setCustomerBusy(false);
    }
  }

  useEffect(() => {
    if (!session?.user_id) return;
    const timer = window.setTimeout(() => {
      try {
        const pending = pendingSubmission(window.sessionStorage, session.user_id, "/orders/upload");
        setUploadPending(pending);
        if (pending) {
          const details: UploadDetails = JSON.parse(pending.body);
          setShipmentDate(details.shipment_date); setRoute(details.route_line); setMainCustomerId(details.main_customer_id);
          setSelectedCustomer({ id: details.main_customer_id, client_code: "", company_name: "Previously selected customer", is_main_customer: true });
        }
      } catch { setError("The pending upload could not be recovered. Check the order files before uploading again."); }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [session?.user_id]);

  const resumeIntake = useCallback(async (rawFileId: string) => {
    const request = ++intakeRequest.current;
    setIntakeRead("opening");
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const ack = await api<Ack>(`/orders/files/${rawFileId}`);
      if (request !== intakeRequest.current) return;
      const pending = ["UPLOADED", "PARSED"].includes(ack.status);
      if (pending) setIntakeRead("checking");
      const validation = pending ? await api<ValidationResponse>(`/orders/${rawFileId}/validate`) : { unresolved: [] };
      if (request !== intakeRequest.current) return;
      setResult(ack); setSplitResult(undefined);
      setUnresolved(validation.unresolved || []);
      setQuantityIssueCount("quantity_issue_count" in validation ? validation.quantity_issue_count || 0 : 0);
      setQuantityReview("quantity_review" in validation ? validation.quantity_review : undefined);
      setMainCustomerId(ack.main_customer_id || "");
      setActiveGroup("ALL");
      setIntakeRead("ready");
      setSuccess(`${ack.file_name} is open for review.`);
    } catch (reason) {
      if (request === intakeRequest.current) {
        setError(reason instanceof Error ? reason.message : "Unable to resume intake review");
        setIntakeRead("error");
      }
    } finally {
      if (request === intakeRequest.current) setBusy(false);
    }
  }, []);

  const cancelIntakeRead = useCallback(() => { intakeRequest.current++; }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => { if (requestedFileId) void resumeIntake(requestedFileId); else { setResult(undefined); setSplitResult(undefined); } }, 0);
    return () => { cancelIntakeRead(); window.clearTimeout(timer); };
  }, [requestedFileId, resumeIntake, cancelIntakeRead]);

  useEffect(() => {
    if (!mappingModalItem) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const query = productSearch.trim()
        ? `?q=${encodeURIComponent(productSearch.trim())}&limit=100&offset=${productOffset}`
        : `?limit=100&offset=${productOffset}`;
      api<MasterProduct[]>(`/master-data/products${query}`, { signal: controller.signal })
        .then(products => {
          if (controller.signal.aborted) return;
          setMasterProducts(products || []);
          setProductSearchError("");
        })
        .catch(reason => {
          if (controller.signal.aborted) return;
          setMasterProducts([]);
          setProductSearchError(reason instanceof Error ? reason.message : "Failed to load the product catalogue");
        }).finally(() => { if (!controller.signal.aborted) setProductLoading(false); });
    }, 250);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [mappingModalItem, productSearch, productOffset, productRetry]);

  async function showUploadedFile(rawFileId: string, initial?: Ack) {
    const ack = initial ?? await api<Ack>(`/orders/files/${rawFileId}`);
    const validation = ["UPLOADED", "PARSED"].includes(ack.status)
      ? await api<ValidationResponse>(`/orders/${rawFileId}/validate`) : { unresolved: [] };
    setResult(ack); setSplitResult(undefined); setActiveGroup("ALL"); setIntakeRead("ready");
    setUnresolved(validation.unresolved || []);
    setQuantityIssueCount("quantity_issue_count" in validation ? validation.quantity_issue_count || 0 : 0);
    setQuantityReview("quantity_review" in validation ? validation.quantity_review : undefined);
    return ack;
  }

  async function recoverUpload() {
    if (uploadInFlight.current) return;
    uploadInFlight.current = true; setBusy(true); setError("");
    try {
      const pending = pendingSubmission(window.sessionStorage, session?.user_id, "/orders/upload");
      if (!pending) return;
      const details: UploadDetails = JSON.parse(pending.body);
      const result = await api<{ raw_file_id: string }>(`/orders/upload-results/${pending.key}?channel=${details.channel}`);
      const ack = await showUploadedFile(result.raw_file_id);
      finishSubmission(window.sessionStorage, session?.user_id, pending); setUploadPending(null); setFile(undefined);
      if (uploadFileInput.current) uploadFileInput.current.value = "";
      setSuccess(`${ack.file_name} was already saved and is open for review.`);
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 404
        ? "The upload is not confirmed yet. Select the original file and retry with its saved details."
        : reason instanceof Error ? reason.message : "Unable to confirm this upload. Keep the original file and try again.");
    } finally { uploadInFlight.current = false; setBusy(false); }
  }

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file || uploadInFlight.current) return;
    if (!session?.upload_replay_supported) { setError("Safe upload recovery is unavailable. Reload and try again."); return; }
    const ext = file.name.split(".").pop()?.toLowerCase() || "";
    if (!["xlsx", "xlsm", "xls", "pdf"].includes(ext)) { setError("Unsupported file format. Choose an Excel (.xlsx, .xlsm, .xls) or PDF order file."); return; }
    uploadInFlight.current = true; setBusy(true); setError(""); setSuccess("");
    let intent: Submission | null = null;
    let confirmed = false;
    try {
      const fingerprint = await fingerprintUpload(file);
      intent = pendingSubmission(window.sessionStorage, session?.user_id, "/orders/upload");
      let details: UploadDetails;
      if (intent) {
        details = JSON.parse(intent.body); requireOriginalUpload(details, fingerprint);
      } else {
        details = { ...fingerprint, channel: ["pdf", "xls"].includes(ext) ? "universal" : "matrix",
          shipment_date: shipmentDate, route_line: route, main_customer_id: mainCustomerId };
        intent = beginSubmission(window.sessionStorage, session?.user_id, "/orders/upload", JSON.stringify(details));
      }
      setUploadPending(intent);
      const body = new FormData(); body.set("file", file); body.set("shipment_date", details.shipment_date);
      body.set("route_line", details.route_line); body.set("main_customer_id", details.main_customer_id);
      const response = await api<Ack>(`/orders/upload-${details.channel}`, {
        method: "POST", headers: { "Idempotency-Key": intent.key }, body,
      });
      confirmed = true;
      const ack = await showUploadedFile(response.raw_file_id, details.channel === "matrix" ? response : undefined);
      finishSubmission(window.sessionStorage, session?.user_id, intent); setUploadPending(null); setFile(undefined);
      if (uploadFileInput.current) uploadFileInput.current.value = "";
      setSuccess(`${ack.file_name} received. Review the products and quantities before confirming.`);
    } catch (reason) {
      if (!confirmed && intent && reason instanceof ApiError && [400, 413, 422, 428].includes(reason.status)) {
        try { finishSubmission(window.sessionStorage, session?.user_id, intent); setUploadPending(null); }
        catch { setError("The pending upload could not be cleared. Check the order files before starting another upload."); return; }
      }
      setError(reason instanceof Error ? reason.message : "Upload confirmation was interrupted. Recover the pending upload before starting another.");
    } finally { uploadInFlight.current = false; setBusy(false); }
  }

  async function downloadSource() {
    if (!result) return;
    setBusy(true); setError("");
    try {
      const blob = await apiBlob(`/orders/${result.raw_file_id}/source`);
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = result.file_name; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to download the original file"); }
    finally { setBusy(false); }
  }

  function openMappingModal(item: UnresolvedProduct) {
    setMappingModalItem(item);
    setMappingMode("select");
    setSelectedCatalogProduct(null);
    setMasterProducts([]); setProductLoading(true); setProductSearchError("");
    setMappingError("");
    const searchHint = item.name_en || item.name_vi || item.product_code || "";
    setProductSearch(searchHint); setProductOffset(0);
    setNewCode(item.product_code || "");
    setNewNameEn(item.name_en || item.display_name || "");
    setNewNameVi(item.name_vi || "");
    setNewNameTh("");
    setNewProduceType("");
    setNewBoxMaterial("");
    setNewTemperature("");
    setNewWeightClass("LIGHT");
    setNewControlStatus("COMBINABLE");
    setNewPackSize(item.pack_size || "100g");
  }

  async function quickMapSku(event: React.FormEvent) {
    event.preventDefault();
    if (!mappingModalItem || !selectedProductId) return;
    setMappingBusy(true);
    setMappingError("");

    try {
      const rawText = mappingModalItem.name_vi || mappingModalItem.name_en || mappingModalItem.product_code;
      await api("/master-data/sku-mappings", {
        method: "POST",
        body: JSON.stringify({
          main_customer_id: result?.main_customer_id,
          client_sku_code: mappingModalItem.product_code,
          client_raw_name: rawText,
          product_id: selectedProductId,
          standard_pack_size: mappingModalItem.pack_size || "100g"
        })
      });

      // Remove from missing list
      const validation = await api<ValidationResponse>(`/orders/${result?.raw_file_id}/validate`);
      setUnresolved(validation.unresolved || []);
      setQuantityIssueCount("quantity_issue_count" in validation ? validation.quantity_issue_count || 0 : 0);
      setQuantityReview(validation.quantity_review);
      setSuccess(`✓ Successfully mapped '${mappingModalItem.product_code}'!`);
      setMappingModalItem(null);
      setSelectedCatalogProduct(null);
    } catch (reason) {
      setMappingError(reason instanceof Error ? reason.message : "SKU mapping failed");
    } finally {
      setMappingBusy(false);
    }
  }

  async function createAndMapNewProduct(event: React.FormEvent) {
    event.preventDefault();
    if (!mappingModalItem) return;
    if (!newProduceType || !newBoxMaterial || !newTemperature) {
      setMappingError("Choose a category, box material and temperature for this product");
      return;
    }
    if (!newCode.trim()) {
      setMappingError("Product SKU code is required");
      return;
    }
    if (!newNameEn.trim() && !newNameVi.trim()) {
      setMappingError("Please provide at least one produce name (English or Vietnamese)");
      return;
    }

    setMappingBusy(true);
    setMappingError("");

    try {
      // 1. Create the new Master Product in catalogue
      const created = await api<MasterProduct>("/master-data/products", {
        method: "POST",
        body: JSON.stringify({
          t_code: newCode.trim().toUpperCase(),
          name_en: newNameEn.trim(),
          name_vi: newNameVi.trim(),
          name_th: newNameTh.trim(),
          produce_type: newProduceType,
          box_material: newBoxMaterial,
          temperature: newTemperature,
          weight_class: newWeightClass,
          control_status: newControlStatus,
          default_pack_sizes: [newPackSize.trim() || "100g"],
        })
      });

      // 2. Link client's raw SKU & text to this newly created Master Product
      const rawText = mappingModalItem.name_vi || mappingModalItem.name_en || mappingModalItem.product_code;
      await api("/master-data/sku-mappings", {
        method: "POST",
        body: JSON.stringify({
          main_customer_id: result?.main_customer_id,
          client_sku_code: mappingModalItem.product_code,
          client_raw_name: rawText,
          product_id: created.id,
          standard_pack_size: newPackSize || mappingModalItem.pack_size || "100g",
        })
      });

      // 3. Add to local master product state so it's instantly available everywhere
      setMasterProducts(prev => [created, ...prev.filter(p => p.id !== created.id)]);

      // 4. Remove this item from unresolved missing list
      const validation = await api<ValidationResponse>(`/orders/${result?.raw_file_id}/validate`);
      setUnresolved(validation.unresolved || []);
      setQuantityIssueCount("quantity_issue_count" in validation ? validation.quantity_issue_count || 0 : 0);
      setQuantityReview(validation.quantity_review);
      setSuccess(`✓ Successfully created master SKU '${created.t_code} · ${created.name_en || created.name_vi}' and verified mapping!`);
      setMappingModalItem(null);
      setSelectedCatalogProduct(null);
    } catch (reason) {
      setMappingError(reason instanceof Error ? reason.message : "Failed to create master product and link mapping");
    } finally {
      setMappingBusy(false);
    }
  }

  async function authorizeAndSplit() {
    if (!result || !["UPLOADED", "PARSED"].includes(result.status)) return;
    if (unresolved.length > 0) {
      setError(`Cannot split: ${unresolved.length} missing SKUs remain unmapped. Please map all highlighted red items first.`);
      return;
    }
    setBusy(true);
    setError("");
    setSuccess("");

    try {
      const validation = await api<ValidationResponse>(`/orders/${result.raw_file_id}/validate`, { method: "GET" });
      setUnresolved(validation.unresolved || []);
      setQuantityIssueCount("quantity_issue_count" in validation ? validation.quantity_issue_count || 0 : 0);
      setQuantityReview(validation.quantity_review);
      if (!validation.can_split) {
        setError(`Cannot confirm: ${validation.unresolved.length} unmatched products and ${validation.quantity_issue_count || 0} quantity issues need review.`);
        return;
      }

      const res = await api<SplitResponse>(`/orders/${result.raw_file_id}/split`, { method: "POST" });
      setSplitResult(res); setResult(current => current ? { ...current, status: res.status } : current);
      setSuccess(`Success! ${res.total_client_orders_created} atomic sub-customer orders created (${res.total_estimated_weight_kg} kg).`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Splitting orders failed");
    } finally {
      setBusy(false);
    }
  }

  async function addAdhocItem(event: React.FormEvent) {
    event.preventDefault();
    if (!result || adhocInFlight.current) return;
    if (!session?.request_replay_supported) { setError("Safe submission recovery is unavailable. Reload and try again."); return; }
    adhocInFlight.current = true;
    setAdhocBusy(true); setError(""); setSuccess("");
    let intent: Submission | null = null;
    try {
      intent = pendingSubmission(window.sessionStorage, session?.user_id, adhocPath) ?? beginSubmission(window.sessionStorage, session?.user_id, adhocPath, JSON.stringify({
        client_code: adhocCustomer, source_notes: "Manual order entry",
        items: [{ t_code: adhocProduct, name: adhocProduct, pack_size: adhocPackSize, pack_count: Number(adhocQty) }],
      }));
      setAdhocPending(intent);
      const res = await api<{ message: string; customer_code: string }>(intent.path, {
        method: "POST", headers: { "Idempotency-Key": intent.key }, body: intent.body,
      });
      finishSubmission(window.sessionStorage, session?.user_id, intent);
      setAdhocPending(null); setSuccess(`${res.message}. You can add the next order below.`);
      setAdhocProduct(""); setAdhocQty(1); manualProductInput.current?.focus();
    } catch (reason) {
      if (intent && reason instanceof ApiError && [400, 404, 422, 428].includes(reason.status)) {
        try { finishSubmission(window.sessionStorage, session?.user_id, intent); setAdhocPending(null); }
        catch { setError("The pending addition could not be cleared. Check the order before entering it again."); return; }
      }
      setError(reason instanceof Error ? reason.message : "The response was interrupted. Retry this pending addition to confirm its result.");
    } finally { adhocInFlight.current = false; setAdhocBusy(false); }
  }

  // Filtered sub-customers by route group
  const details = result?.sub_customer_details || [];
  const groups = result?.group_summary || {};
  const groupKeys = Object.keys(groups).sort();

  const filteredShops = activeGroup === "ALL" 
    ? (result?.sub_customer_codes || [])
    : details.filter(d => d.route_group === activeGroup).map(d => d.client_code);

  const header = <PageHeader eyebrow="Customer orders" title="Orders" description="Upload, review and send customer orders to the buyers." />;
  const pageHeading = result
    ? <div className="order-review-heading">{header}<WorkflowProgress current="orders" /></div>
    : <>{header}<WorkflowProgress current="orders" /></>;

  if (intakeRead !== "ready") return <div className="page orders-workspace">
    {pageHeading}
    {intakeRead === "error" ? <section className="panel order-load-error" role="alert">
      <AlertTriangle size={24} aria-hidden="true" />
      <h2>Unable to open this order</h2><p>{error}</p>
      <div><button type="button" className="primary-button" onClick={() => { if (requestedFileId) void resumeIntake(requestedFileId); }}>Retry opening order</button><Link className="invoice-secondary-button" href="/orders/files">Browse order files</Link></div>
    </section> : <LoadingSkeleton label={intakeRead === "checking" ? "Checking products and quantities…" : "Opening order…"}
      description="Your saved order is loading. Its details and next action will appear here." />}
  </div>;

  const pending = Boolean(result && ["UPLOADED", "PARSED"].includes(result.status) && !splitResult);
  const confirmed = Boolean(result && !pending);
  const canReviewFormat = pending && /\.(xlsx|xlsm|xls)$/i.test(result?.file_name || "");
  const formatNeedsReview = Boolean(canReviewFormat && quantityReview?.template_issues?.length);
  const purchaseHref = splitResult?.batch_id ? `/procurement?batchId=${encodeURIComponent(splitResult.batch_id)}` : `/procurement/batches?raw_file_id=${encodeURIComponent(result?.raw_file_id || "")}&view=all`;
  const nextAction = confirmed ? "purchase" : pending
    ? formatNeedsReview ? "format" : unresolved.length ? "match" : quantityIssueCount ? "quantities" : "confirm"
    : !file ? "file" : !mainCustomerId ? "customer" : !shipmentDate ? "date" : !route.trim() ? "route" : "upload";
  const nextTitle = {
    purchase: "Continue with the linked orders",
    format: "Confirm the customer Excel format first",
    match: `Match ${unresolved.length} ${unresolved.length === 1 ? "product" : "products"} to continue`,
    quantities: `Review ${quantityIssueCount} quantity ${quantityIssueCount === 1 ? "issue" : "issues"}`,
    confirm: "Check the order, then confirm it",
    file: "Choose the customer's order file",
    customer: "Select the billing customer",
    date: "Choose the shipment date",
    route: "Choose or enter the route",
    upload: "Upload the file to check its contents",
  }[nextAction];
  const nextDetail = confirmed ? "Open the purchase batch, or open a customer order below to continue its current work."
    : pending ? formatNeedsReview ? "Check the spreadsheet columns and units before matching products or correcting lines. Re-reading the format replaces line corrections."
      : unresolved.length ? "Start with the highlighted Match product button. Each line must have a catalog match."
      : quantityIssueCount ? "Check the original amounts and units, then correct the highlighted quantity issues."
      : "Review the shops, products and pack counts below. Confirming creates the buyers' purchase batch."
    : "Excel and PDF orders are supported. Choose a file, check the customer and shipment details, then upload.";
  const highlight = (action: string) => nextAction === action && !busy && !customerBusy && !customerLoading ? "true" : undefined;
  function showNextAction() {
    const target = document.querySelector<HTMLElement>(".orders-workspace [data-next-action='true']");
    if (!target) return;
    let parent = target.parentElement;
    while (parent) { if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement; }
    target.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    if (target.matches("button, input, select, a")) target.focus({ preventScroll: true });
    else target.querySelector<HTMLElement>("input, button, select")?.focus({ preventScroll: true });
  }

  const nextStepButton = confirmed ? <Link className="primary-button" href={purchaseHref} data-next-action={highlight("purchase")}>Open purchase batch <ArrowRight size={17} aria-hidden="true" /></Link>
    : <button type="button" className="invoice-secondary-button" title={nextDetail} disabled={busy || customerLoading || customerBusy} onClick={showNextAction}>Show next action <ArrowRight size={17} aria-hidden="true" /></button>;
  const hintsButton = <button type="button" className="order-hints-toggle" aria-pressed={hintsEnabled} onClick={toggleHints}>{hintsEnabled ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}{hintsEnabled ? "Pause hints" : "Resume hints"}</button>;
  const summaryText = <>{result ? `${result.total_sub_customers_detected} ${result.total_sub_customers_detected === 1 ? "shop" : "shops"} in this file` : "Follow the highlighted field or button."}{pending && ` · ${unresolved.length} unmatched products · ${quantityIssueCount} quantity ${quantityIssueCount === 1 ? "issue" : "issues"}`}</>;

  return (
    <div className="page orders-workspace" data-order-state={result ? "review" : "upload"} data-order-status={pending ? "pending" : confirmed ? "confirmed" : undefined} data-hints={hintsEnabled ? "on" : "off"}>
      {pageHeading}
      {activeUploadPending && <section className="panel" aria-label="Pending upload"><h2>Confirm the pending upload</h2>
        <p>Check whether the last file was saved before starting another upload. If it is not confirmed, select the original file again; its customer and shipment details are retained.</p>
        <button type="button" className="primary-button" disabled={busy} onClick={recoverUpload}>Recover pending upload</button>
      </section>}
      <section className="order-overview" aria-label="Current task" aria-busy={busy}>
        <div className="order-overview-heading">
          <div className="order-file-heading"><span className="order-file-icon" aria-hidden="true">{confirmed ? <CheckCircle2 size={25} /> : <FileSpreadsheet size={25} />}</span><div>
            <h2>{confirmed ? "Orders confirmed" : result ? "Review this order" : "New customer order"}</h2>
            <p className="order-file-name">{result?.file_name || file?.name || "Start with an Excel or PDF order file"}</p>
          </div></div>
          {result && <StatusChip status={result.status} />}
        </div>
        <ol className="order-intake-progress" aria-label="Order intake progress">
          {["Upload file", "Review order", "Confirm orders"].map((label, index) => {
            const current = !result ? 0 : confirmed ? 3 : unresolved.length || quantityIssueCount ? 1 : 2;
            return <li key={label} aria-current={current === index ? "step" : undefined} className={index < current ? "is-complete" : ""}>
              <span aria-hidden="true">{index < current ? <CheckCircle2 size={17} /> : index + 1}</span>{label}
            </li>;
          })}
        </ol>
        {result ? <div className="order-summary-bottom">
          <div className="order-summary-task" aria-live="polite" aria-atomic="true"><h3>{busy ? "Updating the order…" : nextTitle}</h3><p>{summaryText}</p></div>
          <div className="order-summary-actions">{nextStepButton}{hintsButton}</div>
        </div> : <><div className="order-next-step">
          <div aria-live="polite" aria-atomic="true"><span className="order-next-label">{busy ? "Please wait" : "Next step"}</span><h3>{busy ? "Updating the order…" : nextTitle}</h3><p>{nextDetail}</p></div>
          {nextStepButton}
        </div>
        <div className="order-hints-control"><p>{summaryText}</p>{hintsButton}</div></>}
      </section>

      <StatusMessage error={error} success={success} />


      {confirmed && result && <section className="panel order-linked-orders" aria-label="Confirmed customer orders">
        <RecordRegister title="Linked customer orders" description="Open an order to continue its purchasing or packing work." endpoint={`/orders/files/${result.raw_file_id}/orders`} identity="order_id" columns={orderColumns} views={false} filters={false} mobileCards open={row => ["SPLIT_AND_VALIDATED", "PURCHASING_CONSOLIDATED", "PROCUREMENT_CONFIRMED"].includes(String(row.status)) ? `/procurement/batches?raw_file_id=${encodeURIComponent(result.raw_file_id)}&view=all` : recordLink("/packing", "orderId", row.order_id)} />
      </section>}
      <div className={`order-stage-layout${result ? " has-order" : ""}`}>
        <section id="order-review" className="panel result-panel">
          <div className="order-review-actions">
            <div>
              <h2>{confirmed ? "Original file details" : "Review the order"}</h2>
              <p className="order-section-description">{confirmed ? "View the shops and details from the uploaded file." : "Check shops and pack counts before sending the order to buyers."}</p>
            </div>

            {result && !splitResult && ["UPLOADED", "PARSED"].includes(result.status) && (
              <HintedButton onClick={authorizeAndSplit} data-next-action={highlight("confirm")}
                disabled={busy || formatNeedsReview || unresolved.length > 0 || quantityIssueCount > 0 || !["UPLOADED", "PARSED"].includes(result?.status || "")}
                reason={formatNeedsReview ? "Confirm the customer Excel format first" : unresolved.length > 0 ? `Match ${unresolved.length} ${unresolved.length === 1 ? "product" : "products"} first` : quantityIssueCount > 0 ? `Review ${quantityIssueCount} quantity issues first` : undefined}>
                <Split size={16} /> {busy ? "Confirming…" : "Confirm orders and create purchase batch"}
              </HintedButton>
            )}
          </div>

          {result ? (
            <div className="order-review-content">
              {pending && <div className="order-review-checks">
                {formatNeedsReview && <div className="order-review-check" data-check-state="required">
                  <OrderTemplateReview rawFileId={result.raw_file_id} onReviewed={() => resumeIntake(result.raw_file_id)} highlighted={Boolean(highlight("format"))} disabled={busy} />
                  <p><strong>Required first.</strong> Confirm columns and units before correcting order lines.</p>
                </div>}
                <div className="order-review-check" data-check-state={formatNeedsReview ? "waiting" : quantityIssueCount ? "required" : "clear"}>
                  <OrderQuantityReview rawFileId={result.raw_file_id} issues={quantityIssueCount} initialData={quantityReview} onReview={setQuantityReview} onIssues={setQuantityIssueCount} highlighted={Boolean(highlight("quantities"))} disabled={busy || formatNeedsReview} />
                  <p><strong>{formatNeedsReview ? "Waiting for Excel format." : quantityIssueCount ? `Required: ${quantityIssueCount} ${quantityIssueCount === 1 ? "issue" : "issues"}.` : "No quantity issues."}</strong> {formatNeedsReview ? "Quantities will be checked again after setup." : quantityIssueCount ? unresolved.length ? "Match products, then fix amounts and units before confirming." : "Fix amounts and units before confirming." : "No correction needed; viewing is optional."}</p>
                </div>
              </div>}

              {unresolved.length > 0 && (
                <section className="unmatched-products" aria-labelledby="unmatched-title">
                  <header><AlertTriangle size={20} aria-hidden /><div><h3 id="unmatched-title">Fix before confirming: {unresolved.length} unmatched {unresolved.length === 1 ? "product" : "products"}</h3>
                    <p>{formatNeedsReview ? "First confirm the customer Excel format above. Then match each product to the catalog." : "Required before confirmation. Match each product to the catalog, then resolve any quantity issues."}</p></div></header>
                  <ul tabIndex={0} aria-label="Products needing a catalog match">
                    {unresolved.map((item, idx) => (
                      <li key={idx}>
                        <div><strong>{item.product_code}</strong> {item.name_vi ? `· ${item.name_vi}` : ""} {item.name_en ? <span className="unmatched-en">({item.name_en})</span> : null}<span className="unmatched-pack">{item.pack_size || "100g"}</span></div>
                        <button type="button" className="invoice-secondary-button btn-sm" disabled={busy || formatNeedsReview} title={formatNeedsReview ? "Confirm the customer Excel format first" : undefined} data-next-action={idx === 0 ? highlight("match") : undefined} onClick={() => openMappingModal(item)}><PlusCircle size={14} /> Match product</button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {splitResult && (
                <div className="order-split-done">
                  <CheckCircle2 size={20} aria-hidden />
                  <div><strong>Orders confirmed and sent to buyers</strong>
                    <p>{splitResult.total_client_orders_created} customer orders · {splitResult.total_line_items_created} lines · about {splitResult.total_estimated_weight_kg} kg</p></div>
                </div>
              )}

            </div>
          ) : (
            <div style={{ marginTop: "16px", display: "grid", gap: "16px" }}>
              <p style={{ color: "var(--muted)", fontSize: "0.88rem", margin: 0 }}>
                Upload your customer order workbook. The system finds each shop and route group in the file and checks every product against the catalog.
              </p>
            </div>
          )}
        </section>
        {/* Supporting intake actions follow the active review in keyboard order. */}
        <div className="order-entry-tools">
          {result && <>
            <OrderDisclosure title={`${result.total_sub_customers_detected} ${result.total_sub_customers_detected === 1 ? "shop" : "shops"} in this order`} description={groupKeys.length ? `View shops across ${groupKeys.length} route ${groupKeys.length === 1 ? "group" : "groups"}` : "View the customer shops found in the file"} icon={<Store size={20} />} className="order-shops">
              {groupKeys.length > 0 && <div className="order-group-filter" role="group" aria-label="Route groups">
                <button type="button" aria-pressed={activeGroup === "ALL"} onClick={() => setActiveGroup("ALL")}>All ({result.total_sub_customers_detected})</button>
                {groupKeys.map(grp => <button key={grp} type="button" aria-pressed={activeGroup === grp} onClick={() => setActiveGroup(grp)}>{grp} ({groups[grp]})</button>)}
              </div>}
              <p className="order-shops-count">{filteredShops.length} shops in {activeGroup === "ALL" ? "all groups" : activeGroup}</p>
              <div className="code-cloud">
                {filteredShops.map(code => <span key={code}><Store size={12} aria-hidden /> {code}</span>)}
              </div>
            </OrderDisclosure>
            <OrderDisclosure title="File tools" description={canReviewFormat && !formatNeedsReview ? "Source file and Excel format settings" : "Original file and upload details"} icon={<FileSpreadsheet size={20} />} className="order-file-reference">
              {pending && result.verification_notice && <p className="order-verification-note">{result.verification_notice.replace(/sub-customers/gi, "shops")}</p>}
              <p className="record-id">File ID: {result.raw_file_id}</p>
              <button type="button" className="secondary-button" disabled={busy} onClick={downloadSource}><Download size={16} /> Download original file</button>
              {canReviewFormat && !formatNeedsReview && <OrderTemplateReview rawFileId={result.raw_file_id} onReviewed={() => resumeIntake(result.raw_file_id)} disabled={busy} actionLabel="Adjust Excel format" />}
            </OrderDisclosure>
          </>}
          <OrderDisclosure title={result ? "Upload another order" : "Upload order"} description={result ? "Start a new upload for another customer or shipment" : "Choose a file and check the shipment details"} icon={<Upload size={20} />} open={!result}>
          <form id="order-upload" className="panel form-panel" onSubmit={upload}>
            <div className="section-heading">
              <div>
                <h2>Upload order file</h2>
                <p>Excel order sheets (one or many shops) or PDF purchase orders</p>
              </div>
              <FileSpreadsheet />
            </div>

            <label className="file-drop" data-next-action={highlight("file")}>
              <Upload />
              <strong>{file?.name ?? "Choose an order file"}</strong>
              <span>Browse Excel (.xlsx, .xlsm, .xls) or PDF purchase orders</span>
              <span className="order-file-choose">{file ? "Change file" : "Browse files"}</span>
              <input
                type="file"
                ref={uploadFileInput}
                accept=".xlsx,.xls,.xlsm,.pdf"
                onChange={e => setFile(e.target.files?.[0])}
                required
              />
            </label>

            <div className="order-template-downloads">
              <span>Need the official format?</span>
              <div className="order-template-links">
                <a
                  href="/templates/Group_Order_Matrix_Template.xlsx"
                  download="Group_Order_Matrix_Template.xlsx"
                  style={{
                    color: "var(--leaf)",
                    fontWeight: 600,
                    textDecoration: "underline",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "3px",
                  }}
                >
                  <Download size={12} /> Group Matrix Template (.xlsx)
                </a>
                <span style={{ color: "var(--line-strong)" }}>·</span>
                <a
                  href="/templates/Single_Customer_Order_Template.xlsx"
                  download="Single_Customer_Order_Template.xlsx"
                  style={{
                    color: "var(--lagoon)",
                    fontWeight: 600,
                    textDecoration: "underline",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "3px",
                  }}
                >
                  <Download size={12} /> Single Order Template (.xlsx)
                </a>
              </div>
            </div>

            <fieldset disabled={Boolean(activeUploadPending)} style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="sr-only">Upload customer and shipment details</legend>
            <div className="order-intake-fields">
              <div className="field-row">
                <label>
                  Find billing customer
                  <input type="search" value={customerSearch} onChange={e => { setCustomerSearch(e.target.value); setCustomerOffset(0); }}
                    placeholder="Type a customer name or code" disabled={busy || customerBusy} />
                </label>
                <label>
                  Billing customer
                <select data-next-action={highlight("customer")} value={mainCustomerId} onChange={e => selectCustomer(mainCustomers.find(customer => customer.id === e.target.value) || (selectedCustomer?.id === e.target.value ? selectedCustomer : undefined))} required disabled={busy || customerBusy || customerLoading}>
                  <option value="">Select the billing customer</option>
                  {selectedCustomer && !mainCustomers.some(customer => customer.id === selectedCustomer.id) && <option value={selectedCustomer.id}>{selectedCustomer.company_name}</option>}{mainCustomers.map(customer => (
                    <option key={customer.id} value={customer.id}>
                      {customer.company_name} · {customer.route_group || customer.client_code}
                    </option>
                  ))}
                </select>
                </label>
              </div>
                <div className="register-pagination"><span>{customerLoading ? "Searching customers..." : `${customerTotal} customers`}</span><button type="button" disabled={customerLoading || customerOffset === 0} onClick={() => setCustomerOffset(value => Math.max(0, value - 25))}>Previous customers</button><button type="button" disabled={customerLoading || customerOffset + 25 >= customerTotal} onClick={() => setCustomerOffset(value => value + 25)}>Next customers</button></div>{customerSearchError && <p role="alert">{customerSearchError}<button type="button" onClick={() => setCustomerRetry(value => value + 1)}>Retry customer search</button></p>}
                <button type="button" className="customer-account-add" disabled={busy || customerBusy} onClick={() => {
                  setNewCustomerCode(""); setNewCustomerName(customerSearch.trim());
                  setNewCustomerCountry(""); setNewCustomerEmail(""); setCustomerError(""); setShowNewCustomer(true);
                }}><PlusCircle size={16} /> Add billing customer</button>
              <div className="field-row order-shipment-fields">
              <label>
                Shipment date
                <input type="date" data-next-action={highlight("date")} value={shipmentDate} onChange={e => setShipmentDate(e.target.value)} required />
              </label>
              <div className="order-route-field">
                <label htmlFor="order-route">Route</label>
                <select id="order-route" data-next-action={!customRoute ? highlight("route") : undefined}
                  value={customRoute ? "__custom__" : route} required disabled={busy || customerBusy}
                  aria-describedby="order-route-help" onChange={e => {
                    const isCustom = e.target.value === "__custom__";
                    setCustomRoute(isCustom); setRoute(isCustom ? "" : e.target.value);
                  }}>
                  <option value="">Select a route</option>
                  {routeOptions.map(line => <option key={line} value={line}>{line}</option>)}
                  <option value="__custom__">Other route…</option>
                </select>
                {customRoute && <input aria-label="New route" data-next-action={highlight("route")} value={route}
                  onChange={e => setRoute(e.target.value)} onBlur={() => setRoute(route.trim().toUpperCase())}
                  placeholder="Enter route code" required maxLength={32} pattern=".*\S.*" disabled={busy || customerBusy} />}
                <small id="order-route-help">Filled from the billing customer. Change it for this shipment if needed.</small>
                {routeError && <p role="alert">{routeError} <button type="button" onClick={() => setRouteRetry(value => value + 1)}>Retry routes</button></p>}
              </div>
              </div>
            </div>
            </fieldset>

            <button className="primary-button" data-next-action={highlight("upload")} disabled={busy || customerBusy || customerLoading || !file || !mainCustomerId || !shipmentDate || !route.trim()}>
              {busy ? "Reading order file…" : "Upload and check order"}
            </button>
          </form></OrderDisclosure>
          {showNewCustomer && <TaskDialog title="Add billing customer" description="Save a new billing customer and select it for this order upload."
            busy={customerBusy} onClose={() => setShowNewCustomer(false)}>
            <form onSubmit={createMainCustomer} style={{ display: "grid", gap: "16px" }}>
              <StatusMessage error={customerError} />
              <label>Customer code<input value={newCustomerCode} onChange={e => setNewCustomerCode(e.target.value)} required maxLength={64} disabled={customerBusy} /></label>
              <label>Company name<input value={newCustomerName} onChange={e => setNewCustomerName(e.target.value)} required maxLength={256} disabled={customerBusy} /></label>
              <label>Destination country<input value={newCustomerCountry} onChange={e => setNewCustomerCountry(e.target.value)} required maxLength={64} disabled={customerBusy} /></label>
              <label>Billing email (optional)<input type="email" value={newCustomerEmail} onChange={e => setNewCustomerEmail(e.target.value)} maxLength={255} disabled={customerBusy} /></label>
              <p className="task-note">Route line: {route}</p>
              <button type="submit" className="primary-button" disabled={customerBusy}>{customerBusy ? "Adding account…" : "Add and select account"}</button>
            </form>
          </TaskDialog>}

          {result && (
            <OrderDisclosure title="Add more orders" description="Add a product and pack quantity to this shipment" icon={<PlusCircle size={20} />}><form className="panel form-panel" onSubmit={addAdhocItem} style={{ borderTop: "3px solid var(--leaf)" }}>
              <div className="section-heading">
                <div>
                  <h2>Add more orders</h2>
                  <p>Select a customer and enter a product, pack size, and quantity. Add each entry to this shipment one by one.</p>
                </div>
                <PlusCircle color="var(--leaf)" />
              </div>

              {activeAdhocPending && <p role="status">An addition is awaiting confirmation. Retry it with the original details before adding another entry.</p>}
              <fieldset disabled={adhocBusy || Boolean(activeAdhocPending)} style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="sr-only">Order addition details</legend>
              <div className="field-row">
                <label>
                  Shop
                  <input
                    list="shops-datalist"
                    placeholder="e.g. BE-DUYÊN or CT-Trung"
                    value={adhocCustomer}
                    onChange={e => setAdhocCustomer(e.target.value)}
                    required
                  />
                  <datalist id="shops-datalist">
                    {result.sub_customer_codes.map(c => <option key={c} value={c} />)}
                  </datalist>
                </label>

                <label>
                  Produce Item (Code or Name)
                  <input
                    ref={manualProductInput}
                    placeholder="e.g. T024, Sweet basil, Chaplu leaf"
                    value={adhocProduct}
                    onChange={e => setAdhocProduct(e.target.value)}
                    required
                  />
                </label>
              </div>

              <div className="field-row">
                <label>
                  Pack Size
                  <select value={adhocPackSize} onChange={e => setAdhocPackSize(e.target.value)}>
                    <option value="100g">100g</option>
                    <option value="200g">200g</option>
                    <option value="500g">500g</option>
                    <option value="1kg">1kg</option>
                  </select>
                </label>

                <label>
                  Quantity (Packs)
                  <input
                    type="number"
                    min="1"
                    value={adhocQty}
                    onChange={e => setAdhocQty(Number(e.target.value))}
                    required
                  />
                </label>
              </div>

              </fieldset>
              <button className="primary-button" style={{ background: "var(--leaf)" }} disabled={adhocBusy}>
                {adhocBusy ? "Confirming addition..." : activeAdhocPending ? "Retry pending addition" : "+ Add order"}
              </button>
            </form></OrderDisclosure>
          )}
      <OrderDisclosure title="Order file format guide" description="See supported layouts and download the order templates" icon={<CircleHelp size={20} />} className="order-template-tools">
        <div className="format-guide">
          <section><h3><Layers size={15} aria-hidden /> Group order matrix (many shops)</h3><ul>
            <li><strong>Row 2 – route groups:</strong> merged headers such as <code>Group CT</code>, <code>Group BE</code>, <code>Group NGA</code>.</li>
            <li><strong>Row 3 – shop codes:</strong> one column per shop, e.g. <code>CT-Trung</code>, <code>BE-DUYÊN</code>.</li>
            <li><strong>Columns A–D – products:</strong> <code>Code</code>, <code>VIET DESCRIPTION</code>, <code>ENGLISH DESCRIPTION</code>, <code>Packing</code>.</li>
            <li><strong>Cells:</strong> pack counts per shop. Row and column totals are calculated.</li>
          </ul></section>
          <section><h3><FileSpreadsheet size={15} aria-hidden /> Single customer order</h3><ul>
            <li><strong>Order Info sheet:</strong> customer code, shipment date and route.</li>
            <li><strong>Order sheet:</strong> <code>Code</code>, <code>Product Name</code>, <code>Viet</code>, <code>Thai</code>, <code>Packing</code>, <code>Order</code>.</li>
            <li><strong>Weights:</strong> pack count × standard pack weight (100g = 0.10 kg).</li>
          </ul></section>
        </div>
      </OrderDisclosure>
        </div>
      </div>

      {mappingModalItem && (
        <TaskDialog title="Match to a catalog product" description="Choose the catalog product that this order item refers to." size="lg" className="product-match-dialog" guidance={false} busy={mappingBusy} onClose={() => setMappingModalItem(null)}
          footer={<>
            <div className="mapping-save-status" data-selected={Boolean(mappingMode === "select" && selectedCatalogProduct)} aria-live="polite" aria-atomic="true"><span>{mappingMode === "select" ? selectedCatalogProduct ? "Selected product" : "No product selected" : "New catalog product"}</span><strong>{mappingMode === "select" ? selectedCatalogProduct ? `${selectedCatalogProduct.t_code} · ${selectedCatalogProduct.name_en || selectedCatalogProduct.name_vi}` : "Select a product above to enable Save match." : "Complete the required details, then create and match."}</strong>
              {mappingMode === "select" && selectedCatalogProduct && <p>Check your choice, then save to link it to this order item.</p>}
            </div>
            <div className="mapping-footer-actions"><button type="button" className="invoice-secondary-button" disabled={mappingBusy} onClick={() => setMappingModalItem(null)}>Cancel</button>
              <button type="submit" form={mappingMode === "select" ? "product-match-form" : "product-create-match-form"} className="primary-button" disabled={mappingBusy || (mappingMode === "select" && !selectedProductId)}>{mappingBusy ? "Saving…" : mappingMode === "select" ? "Save match" : "Create product and match"}</button>
            </div>
          </>}>
          <div className="mapping-workspace">
            <aside className="mapping-source" aria-label="Order item to match">
              <div className="mapping-source-title"><FileSpreadsheet size={18} aria-hidden /><h3>Order item to match</h3></div>
              <div className="mapping-source-name"><strong>{mappingModalItem.name_vi || mappingModalItem.name_en || mappingModalItem.display_name || "Unnamed item"}</strong>{mappingModalItem.name_vi && mappingModalItem.name_en && <span>{mappingModalItem.name_en}</span>}</div>
              <dl className="mapping-source-facts">
              <div><dt>Code in file</dt><dd>{mappingModalItem.product_code || "Not provided"}</dd></div>
              <div><dt>Pack size in file</dt><dd>{mappingModalItem.pack_size || "Not provided"}</dd></div>
              </dl>
              <ol className="mapping-steps" aria-label="Product matching steps">
                {["Check the order item", mappingMode === "select" ? "Choose a catalog product" : "Enter product details", mappingMode === "select" ? "Save the match" : "Create and match"].map((step, index) => {
                  const current = mappingMode === "select" ? selectedProductId ? 2 : 1 : newCode.trim() && newNameEn.trim() && newProduceType && newBoxMaterial && newTemperature ? 2 : 1;
                  return <li key={step} aria-current={index === current ? "step" : undefined} className={index < current ? "is-complete" : undefined}><span aria-hidden>{index < current ? <CheckCircle2 size={16} /> : index + 1}</span>{step}</li>;
                })}
              </ol>
              <p className="mapping-source-note">Match the product name and variety. The order&apos;s pack size stays unchanged when you save.</p>
            </aside>
            <div className="mapping-editor">
            <div className="segmented mapping-mode" role="group" aria-label="How to match">
              <button type="button" disabled={mappingBusy} aria-pressed={mappingMode === "select"} onClick={() => { setMappingMode("select"); setMappingError(""); }}><Search size={14} /> Choose existing product</button>
              <button type="button" disabled={mappingBusy} aria-pressed={mappingMode === "create"} onClick={() => { setMappingMode("create"); setMappingError(""); }}><PlusCircle size={14} /> New catalog product</button>
            </div>

            {mappingMode === "select" ? (
              <form id="product-match-form" className="mapping-catalog-form" onSubmit={quickMapSku}>
                <StatusMessage error={mappingError} />
                <label className="mapping-search">Search the catalog<span className="mapping-search-control"><Search size={17} aria-hidden /><input type="search" autoFocus value={productSearch} disabled={mappingBusy} onChange={event => { setProductLoading(true); setProductSearchError(""); setProductSearch(event.target.value); setProductOffset(0); }} placeholder="Product name in any language, or code" /></span></label>
                <div className="mapping-results-heading"><strong>Choose a product</strong><span>Compare with the order item on the left</span></div>
                <div className="mapping-result-list" aria-label="Product choices" aria-busy={productLoading}>
                  {Boolean(mappingModalItem.suggestions?.length) && <section className="mapping-suggested" aria-label="Suggested products">
                    <div className="mapping-choice-heading"><strong>Suggested products <span>{mappingModalItem.suggestions?.length}</span></strong><p>Similar names — check the product before choosing.</p></div>
                    <div>{mappingModalItem.suggestions?.map(product => <button type="button" key={product.id} disabled={mappingBusy} aria-pressed={selectedProductId === product.id} onClick={() => { setSelectedCatalogProduct(product); setMappingError(""); }}>
                      <span className="mapping-choice-indicator" aria-hidden>{selectedProductId === product.id && <CheckCircle2 size={16} />}</span>
                      <span className="mapping-suggestion-copy"><strong>{product.name_en || product.name_vi}</strong><small>{product.t_code}{product.name_en && product.name_vi && product.name_en !== product.name_vi ? ` · ${product.name_vi}` : ""}</small></span>
                      <span className="mapping-suggestion-action">{selectedProductId === product.id ? "Selected" : "Select"}</span>
                    </button>)}</div>
                  </section>}
                  <div className="mapping-choice-heading mapping-search-heading"><strong>{productSearch.trim() ? "Search results" : "All catalog products"}</strong><span>{productLoading ? "Searching…" : productSearchError ? "Unavailable" : `${masterProducts.length} ${masterProducts.length === 1 ? "result" : "results"}`}</span></div>
                  <div role="radiogroup" aria-label="Catalog products">
                  {productLoading ? <p className="mapping-empty" role="status">Looking for matching products…</p> : productSearchError ? <div className="mapping-empty" role="alert"><strong>Catalog search unavailable</strong><p>{productSearchError}</p><button type="button" className="invoice-secondary-button" onClick={() => { setProductLoading(true); setProductRetry(value => value + 1); }}>Retry search</button></div> : masterProducts.length ? masterProducts.map(product => <label key={product.id} className={`mapping-result${selectedProductId === product.id ? " is-selected" : ""}`}>
                    <input type="radio" name="catalog-product" value={product.id} checked={selectedProductId === product.id} disabled={mappingBusy} onChange={() => { setSelectedCatalogProduct(product); setMappingError(""); }} />
                    <span className="mapping-result-copy"><span><code>{product.t_code}</code><strong>{product.name_en || product.name_vi}</strong></span>{product.name_en && product.name_vi && <small>{product.name_vi}</small>}</span>
                  </label>) : <div className="mapping-empty"><strong>No results for this search</strong><p>Try another language or a product code{mappingModalItem.suggestions?.length ? ", or check the suggestions above." : "."}</p><div className="mapping-empty-actions">{productSearch.trim() && <button type="button" className="invoice-secondary-button" onClick={() => { setProductLoading(true); setProductSearch(""); setProductOffset(0); }}>Show all products</button>}<button type="button" className="invoice-secondary-button" onClick={() => setMappingMode("create")}>Add it as a new product</button></div></div>}
                  </div>
                </div>
                {(productOffset > 0 || masterProducts.length >= 100) && <div className="mapping-pagination"><span>{productLoading ? "Searching…" : masterProducts.length ? `Products ${productOffset + 1}–${productOffset + masterProducts.length}` : "0 results"}</span><div><button type="button" disabled={mappingBusy || productLoading || productOffset === 0} onClick={() => { setProductLoading(true); setProductOffset(value => Math.max(0, value - 100)); }}>Previous products</button><button type="button" disabled={mappingBusy || productLoading || masterProducts.length < 100} onClick={() => { setProductLoading(true); setProductOffset(value => value + 100); }}>Next products</button></div></div>}
              </form>
            ) : (
              <form id="product-create-match-form" className="mapping-create-form" onSubmit={createAndMapNewProduct}>
                <p className="mapping-create-intro">Create a missing product and save its match. Fields marked * are required.</p>
                <StatusMessage error={mappingError} />
                <fieldset disabled={mappingBusy}><legend>Product identity</legend><div className="mapping-field-grid">
                  <label>Product code *<input aria-label="Product code" value={newCode} onChange={event => setNewCode(event.target.value)} placeholder="e.g. 11-009" required /></label>
                  <label>English name *<input aria-label="English name" value={newNameEn} onChange={event => setNewNameEn(event.target.value)} placeholder="e.g. CHINESE CABBAGE" required /></label>
                  <label>Vietnamese name<input value={newNameVi} onChange={event => setNewNameVi(event.target.value)} placeholder="e.g. Cải ngọt" /></label>
                  <label>Thai name<input value={newNameTh} onChange={event => setNewNameTh(event.target.value)} placeholder="e.g. กวางตุ้ง" /></label>
                </div></fieldset>
                <fieldset disabled={mappingBusy}><legend>Packing &amp; storage</legend><div className="mapping-field-grid">
                  <label>Category *<select required aria-label="Category" value={newProduceType} onChange={event => setNewProduceType(event.target.value)}><option value="">Choose category</option>{PRODUCT_CATEGORIES.map(category => <option key={category.value} value={category.value}>{category.label}</option>)}</select></label>
                  <label>Box Material *<select required aria-label="Box Material" value={newBoxMaterial} onChange={event => setNewBoxMaterial(event.target.value)}><option value="">Choose material</option><option value="FOAM">Foam</option><option value="CARDBOARD">Cardboard</option></select></label>
                  <label>Temp Zone *<select required aria-label="Temp Zone" value={newTemperature} onChange={event => setNewTemperature(event.target.value)}><option value="">Choose temperature</option><option value="COLD">Cold (chill)</option><option value="NORMAL">Normal (ambient)</option></select></label>
                  <label>Pack size<input value={newPackSize} onChange={event => setNewPackSize(event.target.value)} placeholder="e.g. 100g" /></label>
                </div></fieldset>
                <details className="mapping-category-help"><summary>Help choosing a category</summary><p>Fresh chili: Vegetables. Ground chili: Spices / seasonings. Rice crackers: Packaged food. Lip balm: Personal care. If unsure, choose Other / needs review, and check packaging and temperature on the product label.</p></details>
              </form>
            )}
            </div>
          </div>
        </TaskDialog>
      )}
    </div>
  );
}
