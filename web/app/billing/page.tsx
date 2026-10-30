"use client";

import { PageLoadingSkeleton, TableLoadingSkeleton } from "@/components/loading-skeleton";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CircleCheckBig, FileText, ArrowRight } from "lucide-react";
import { api, apiBlob, ApiError } from "@/lib/api";
import { StatusMessage } from "@/components/status-message";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { BillingRegister } from "@/components/billing-register";
import { RegisterBackLink, type RegisterRow } from "@/components/record-register";
import { TaskDialog } from "@/components/task-dialog";
import { TaskPanel, WorkflowProgress } from "@/components/workflow-guidance";
import { StatusChip, statusLabel } from "@/components/record-register";
import { confirmAction } from "@/lib/confirm";

const confirmDiscard = () => confirmAction({ title: "Discard invoice changes?", body: "Your edits to this draft have not been saved.", confirmLabel: "Discard changes", tone: "danger" });

type InvoiceLine = {
  line_item_id: string; source_order_id: string; source_order_ref: string; source_customer_code: string;
  product_code: string; product_name: string; box_number: number;
  pack_size: string; billed_packs: number; billed_weight_kg: number;
  unit_price_eur: number; line_total_eur: number;
};

type Invoice = {
  invoice_id: string; revision: number; client_order_id: string; invoice_number: string; invoice_type: string;
  main_customer_seller: string; sub_customer_buyer: string; subtotal_eur: number;
  currency: string; total_amount_eur: number; amount_paid_eur: number; status: string;
  internal_notes: string | null; source_order_count: number; items: InvoiceLine[];
};

type BillingQueueItem = {
  order_id: string; order_ref_id: string; customer_code: string; customer_name: string;
  order_status: string; invoice_id: string | null; invoice_number: string | null; invoice_status: string | null;
};


function money(value: number, currency = "THB") {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(value);
}

export default function BillingPage() { return <Suspense fallback={<PageLoadingSkeleton label="Loading billing…" />}><BillingContent /></Suspense>; }

function BillingContent() {
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const [resolvedRead, setResolvedRead] = useState<string>();
  const detailLoading = Boolean(searchParams.get("invoiceId") || searchParams.getAll("orderId").length === 1) && resolvedRead !== query;
  const [refreshKey, setRefreshKey] = useState(0);
  const [reviewedInvoice, setReviewedInvoice] = useState("");
  const [approvalRows, setApprovalRows] = useState<RegisterRow[]>([]);
  const router = useRouter();
  const [tab, setTab] = useState<"create" | "invoices">("create");
  const [mode, setMode] = useState<"individual" | "master">("individual");
  const [billingTask, setBillingTask] = useState<"approve" | null>(null);
  const [orderId, setOrderId] = useState("");
  const [workQueue, setWorkQueue] = useState<BillingQueueItem[]>([]);
  const [selectedOrders, setSelectedOrders] = useState<string[]>([]);
  const [selectedInvoices, setSelectedInvoices] = useState<string[]>([]);
  const [invoice, setInvoice] = useState<Invoice>();
  const [invoiceBaseline, setInvoiceBaseline] = useState("");
  const [revisionConflict, setRevisionConflict] = useState(false);
  useUnsavedChanges(Boolean(invoice && invoiceBaseline && JSON.stringify(invoice) !== invoiceBaseline));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function refreshData() { setRefreshKey(value => value + 1); }

  useEffect(() => {
    let active = true;
    const startup = window.setTimeout(() => {
    const params = new URLSearchParams(query);
    const invoiceId = params.get("invoiceId"); const requestedOrderId = params.get("orderId");
    setInvoice(undefined); setRevisionConflict(false); setWorkQueue([]); setOrderId(requestedOrderId || ""); setError("");
    setTab(params.get("tab") === "invoices" || invoiceId ? "invoices" : "create");
    if (invoiceId) {
      api<Invoice>(`/invoicing/${encodeURIComponent(invoiceId)}`).then(value => { if (active) { setInvoice(value); setInvoiceBaseline(JSON.stringify(value)); } }).catch(reason => { if (active) setError(String(reason)); }).finally(() => { if (active) setResolvedRead(query); });
    } else if (requestedOrderId && params.getAll("orderId").length === 1) {
      api<BillingQueueItem>(`/invoicing/order-record/${encodeURIComponent(requestedOrderId)}`).then(value => {
        if (!active) return;
        setWorkQueue([value]);
        if (value.invoice_id) return api<Invoice>(`/invoicing/${encodeURIComponent(value.invoice_id)}`).then(result => { if (active) { setInvoice(result); setInvoiceBaseline(JSON.stringify(result)); } });
      }).catch(reason => { if (active) setError(String(reason)); }).finally(() => { if (active) setResolvedRead(query); });
    }
    const returnedMode = params.get("mode");
    if (returnedMode === "master" || returnedMode === "bulk") {
      setSelectedOrders(params.getAll("orderId"));
      setMode(returnedMode === "master" ? "master" : "individual");
    }
    if (params.get("pricesResolved") === "1") setMessage("Selling prices saved. Generate the invoice from the completed final pack.");
    }, 0);
    return () => { active = false; window.clearTimeout(startup); };
  }, [query]);

  async function changeTab(next: "create" | "invoices", group?: string) {
    if (invoice && JSON.stringify(invoice) !== invoiceBaseline && !await confirmDiscard()) return;
    const params = new URLSearchParams({ tab: next, view: next === "invoices" ? "all" : "pending" });
    if (group) params.set("invoice_group", group);
    router.push(`/billing?${params}`);
  }

  async function perform<T>(action: () => Promise<T>, recovery?: { mode: string; ids: string[] }): Promise<T | undefined> {
    setBusy(true); setError(""); setMessage("");
    try { return await action(); }
    catch (reason) {
      if (reason instanceof ApiError && reason.detail && typeof reason.detail === "object" && "code" in reason.detail &&
          ["STALE_REVISION", "REVISION_REQUIRED"].includes(String(reason.detail.code))) setRevisionConflict(true);
      if (reason instanceof ApiError && reason.detail && typeof reason.detail === "object" &&
          "code" in reason.detail && reason.detail.code === "MISSING_SELLING_PRICES" && "order_id" in reason.detail) {
        const recoveryMode = recovery?.mode || mode;
        const ids = recovery?.ids || (recoveryMode === "individual" ? [orderId] : selectedOrders);
        const params = new URLSearchParams({ mode: recoveryMode });
        for (const id of ids.length ? ids : [String(reason.detail.order_id)]) params.append("orderId", id);
        router.push(`/billing/resolve-prices?${params}`);
      } else setError(reason instanceof Error ? reason.message : "Financial action failed");
    }
    finally { setBusy(false); }
  }

  function showDrafts(created: Invoice[]) {
    setInvoice(created[0]); setInvoiceBaseline(JSON.stringify(created[0])); setRevisionConflict(false);
    const params = new URLSearchParams(window.location.search); params.set("invoiceId", created[0].invoice_id); params.delete("orderId"); params.set("tab", "invoices");
    window.history.replaceState(null, "", `/billing?${params}`);
  }

  async function createIndividualDraft() {
    if (!orderId) return;
    const result = await perform(() => api<Invoice>("/invoicing/generate-on-behalf", { method: "POST", body: JSON.stringify({ client_order_id: orderId }) }));
    if (!result) return;
    showDrafts([result]); setMessage(`Draft ${result.invoice_number} is ready for review.`); await refreshData();
  }

  async function createMasterDrafts(ids = selectedOrders) {
    if (ids.length < 2) return;
    const created = await perform(() => api<Invoice>("/invoicing/generate-master", { method: "POST", body: JSON.stringify({ client_order_ids: ids }) }), { mode: "master", ids });
    if (!created) return;
    showDrafts([created]); setSelectedOrders([]); setMessage(`Master invoice created for ${created.source_order_count} sub-orders.`); await refreshData();
  }

  async function createBulkDrafts(ids = selectedOrders) {
    const created = await perform(() => api<Invoice[]>("/invoicing/generate-bulk", {
      method: "POST", body: JSON.stringify({ order_ids: ids }),
    }), { mode: "bulk", ids });
    if (!created) return;
    setSelectedOrders([]); setSelectedInvoices(created.map(item => item.invoice_id));
    setBillingTask(null); setTab("invoices"); router.push("/billing?tab=invoices"); setMessage(`${created.length} separate customer invoices are ready for accounting review.`);
    await refreshData();
  }

  async function approveSelectedInvoices() {
    const approved = await perform(() => api<Invoice[]>("/invoicing/approve-bulk", {
      method: "POST", body: JSON.stringify({ invoice_ids: selectedInvoices,
        expected_revisions: Object.fromEntries(approvalRows.map(row => [String(row.invoice_id), row.revision])) }),
    }));
    if (!approved) return;
    setBillingTask(null); setSelectedInvoices([]); setMessage(`${approved.length} separate invoices approved and issued.`); await refreshData();
  }

  function updateCurrent(next: Invoice) {
    setInvoice(next); setInvoiceBaseline(JSON.stringify(next)); setRevisionConflict(false);
  }

  async function reloadConflictedInvoice() {
    if (!invoice) { setBillingTask(null); setSelectedInvoices([]); setRevisionConflict(false); setError(""); await refreshData(); return; }
    if (JSON.stringify(invoice) !== invoiceBaseline && !await confirmDiscard()) return;
    const latest = await perform(() => api<Invoice>(`/invoicing/${invoice.invoice_id}`));
    if (latest) { updateCurrent(latest); setMessage("Latest invoice loaded. Review it before saving or approving."); }
  }

  async function saveDraft() {
    if (!invoice || revisionConflict || invoice.status !== "INVOICE_DRAFT") return;
    const result = await perform(() => api<Invoice>(`/invoicing/${invoice.invoice_id}/draft`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: invoice.revision, internal_notes: invoice.internal_notes, items: invoice.items.map(item => ({ line_item_id: item.line_item_id, unit_price_eur: item.unit_price_eur })) }),
    }));
    if (result) { updateCurrent(result); setMessage(`Changes saved to ${result.invoice_number}.`); }
    return result;
  }

  async function approveDraft() {
    if (!invoice) return;
    const saved = await saveDraft();
    if (!saved) return;
    const result = await perform(() => api<Invoice>(`/invoicing/${saved.invoice_id}/finalize`, { method: "POST", body: JSON.stringify({ expected_revision: saved.revision }) }));
    if (!result) return;
    updateCurrent(result); setMessage(`${result.invoice_number} approved and finalized.`); await refreshData();
  }

  async function openDocument(path: string) {
    const blob = await perform(() => apiBlob(path)); if (!blob) return;
    const url = URL.createObjectURL(blob); window.open(url, "_blank", "noopener,noreferrer"); window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  const selectedQueueItem = workQueue.find(item => item.order_id === orderId);
  const selectedCustomerName = (selectedQueueItem?.customer_name || "Customer order")
    .replace(/^Sub-Customer\s+/i, "").replace(/_+/g, " ").replace(/\s+/g, " ").trim();
  const pricesResolved = new URLSearchParams(query).get("pricesResolved") === "1";
  const readyOrders = workQueue.filter(item => item.order_status === "HANDOFF_02_RETURNED" && !item.invoice_id);
  const editable = invoice?.status === "INVOICE_DRAFT";
  const invoiceSections = invoice ? Array.from(invoice.items.reduce((sections, item) => {
    const key = item.source_order_id || invoice.client_order_id;
    const section = sections.get(key) || { orderId: key, orderRef: item.source_order_ref, customerCode: item.source_customer_code, items: [] as InvoiceLine[] };
    section.items.push(item); sections.set(key, section); return sections;
  }, new Map<string, { orderId: string; orderRef: string; customerCode: string; items: InvoiceLine[] }>()).values()) : [];

  return (
    <div className="page billing-workspace">
      <header className="page-header billing-header">
        <div><h1>Billing desk</h1><p>Create and print final invoices for your team.</p></div>
        <div className="billing-tabs" role="tablist" aria-label="Billing sections">
          <button role="tab" aria-selected={tab === "create"} onClick={() => changeTab("create")}>Ready to invoice</button>
          {[["drafts", "Drafts"], ["final", "Final invoices"]].map(([group, label]) => <button key={group} role="tab" aria-selected={tab === "invoices" && searchParams.get("invoice_group") === group} onClick={() => changeTab("invoices", group)}>{label}</button>)}
          <button role="tab" aria-selected={tab === "invoices" && !searchParams.get("invoice_group")} onClick={() => changeTab("invoices")}>All invoices</button>
        </div>
      </header>
      <StatusMessage error={error} success={message} />
      {revisionConflict && <button type="button" disabled={busy} onClick={() => void reloadConflictedInvoice()}>Reload latest invoice for review</button>}
      {(invoice || orderId) && <WorkflowProgress current="billing" done />}
      {detailLoading && <TableLoadingSkeleton label="Loading invoice details…" columns={["Produce", "Pack", "Qty", "Weight", "Unit price", "Amount"]} />}
      {!detailLoading && invoice && <TaskPanel reference={invoice.invoice_number} title={statusLabel(invoice.status)}
        detail={editable ? "Review packed quantities, selling prices and notes. Approval locks the invoice amounts." : "Invoice creation is complete. Print or download it; your team handles the next steps outside this system."}
        primary={editable ? { label: "Review invoice details", href: "#invoice-details" } : { label: "Review final invoice", href: "#invoice-details" }} />}
      {!detailLoading && !invoice && !orderId && <BillingRegister invoices={tab === "invoices"} busy={busy} refreshKey={refreshKey}
        create={(ids, master) => { setSelectedOrders(ids); setMode(master ? "master" : "individual"); if (master) void createMasterDrafts(ids); else void createBulkDrafts(ids); }}
        approve={rows => { setApprovalRows(rows); setSelectedInvoices(rows.map(row => String(row.invoice_id))); setBillingTask("approve"); }} />}
      {!detailLoading && orderId && !invoice && <section className="panel billing-order-panel" aria-labelledby="billing-order-title">
        <div className="billing-order-topbar"><RegisterBackLink fallback="/billing" />
          {readyOrders.some(item => item.order_id === orderId) && <span className="billing-order-ready"><CircleCheckBig size={16} aria-hidden="true" />Packing completed</span>}
        </div>
        <div className="billing-order-content">
          <div className="billing-order-customer"><p className="billing-order-label">Invoice for</p>
            <h2 id="billing-order-title">{selectedCustomerName}</h2>
            <dl className="billing-order-reference"><dt>Order reference</dt><dd>{selectedQueueItem?.order_ref_id || orderId}</dd></dl>
          </div>
          <div className="billing-order-summary"><FileText size={24} aria-hidden="true" /><h3>Prepare invoice draft</h3>
            <p>Use the final packed quantities, then review the draft before approving it.</p>
            {pricesResolved && <span className="billing-order-prices"><CircleCheckBig size={16} aria-hidden="true" />Selling prices saved</span>}
          </div>
        </div>
        <footer className="billing-order-actions"><p>The draft stays editable until you approve it.</p>
          <button type="button" className="primary-button" disabled={busy || !readyOrders.some(item => item.order_id === orderId)} onClick={createIndividualDraft}>{busy ? "Generating draft…" : "Generate from final pack"}<ArrowRight size={17} aria-hidden="true" /></button>
        </footer>
      </section>}
      {!invoice && selectedOrders.length > 0 && pricesResolved && <section className="panel"><p>{selectedOrders.length} orders ready after price resolution.</p><button type="button" className="primary-button" disabled={busy} onClick={() => mode === "master" ? createMasterDrafts() : createBulkDrafts()}>Generate drafts</button></section>}
      {billingTask === "approve" && <TaskDialog title="Approve reviewed drafts" description="Approval locks prices and issues the selected invoices. Review each draft before approving." busy={busy} onClose={() => setBillingTask(null)}>
        <StatusMessage error={error} />{revisionConflict && <button type="button" disabled={busy} onClick={() => void reloadConflictedInvoice()}>Reload invoice selection for review</button>}
        <p>{selectedInvoices.length} drafts selected.</p><p>{Array.from(new Set(approvalRows.map(row => String(row.currency)))).map(currency => `${currency} ${approvalRows.filter(row => row.currency === currency).reduce((sum, row) => sum + Number(row.amount), 0).toFixed(2)}`).join(" / ")}</p>
        <button type="button" className="primary-button" disabled={busy || revisionConflict} onClick={approveSelectedInvoices}>Approve and issue {selectedInvoices.length} invoices</button>
      </TaskDialog>}

      {!detailLoading && invoice && <div><RegisterBackLink fallback="/billing" /><section className="invoice-modal invoice-record" aria-labelledby="invoice-preview-title" style={{ width: "100%", maxHeight: "none", marginTop: 16 }}>
        <header className="invoice-record-header"><div><p>{invoice.invoice_type === "MASTER_SUMMARY" ? `Master invoice · ${invoice.source_order_count} sub-orders · ${money(invoice.total_amount_eur, invoice.currency)}` : "Invoice details"}</p><h2 id="invoice-preview-title">{invoice.invoice_number}</h2></div><button type="button" className="invoice-secondary-button" aria-label="Back to invoice queue" onClick={async () => { if (invoice && JSON.stringify(invoice) !== invoiceBaseline && !await confirmDiscard()) return; const back = new URLSearchParams(window.location.search).get("returnTo"); router.push(back && (back === "/billing" || back.startsWith("/billing?")) ? back : "/billing?tab=invoices"); }}>Back to invoice queue</button></header>
        <div id="invoice-details" className="invoice-paper" onFocusCapture={() => setReviewedInvoice(invoice.invoice_id)}>
          <div className="invoice-paper-parties"><div><span>Seller</span><strong>{invoice.main_customer_seller}</strong></div><div><span>Bill to</span><strong>{invoice.sub_customer_buyer}</strong></div><div><span>Status</span><StatusChip status={invoice.status} /></div></div>
          <div className="invoice-line-table"><div className="invoice-line-head"><span>Produce</span><span>Pack</span><span>Qty</span><span>Weight</span><span>Unit price</span><span>Amount</span></div>{invoiceSections.map(section => <div className="invoice-order-section" key={section.orderId}>{invoice.invoice_type === "MASTER_SUMMARY" && <div className="invoice-order-heading"><strong>{section.customerCode}</strong><span>{section.orderRef}</span></div>}{section.items.map(item => <div className="invoice-line-row" key={item.line_item_id}><span><strong>{item.product_code}</strong><small>{item.product_name}</small></span><span>{item.pack_size}</span><span>{item.billed_packs}</span><span>{item.billed_weight_kg.toFixed(2)} kg</span><span>{editable ? <input aria-label={`Unit price for ${item.product_code}${invoice.invoice_type === "MASTER_SUMMARY" ? ` ${item.source_customer_code}` : ""}`} type="number" min="0.01" step="0.01" value={item.unit_price_eur} onChange={event => setInvoice(current => current ? { ...current, items: current.items.map(line => line.line_item_id === item.line_item_id ? { ...line, unit_price_eur: Number(event.target.value) } : line) } : current)} /> : money(item.unit_price_eur, invoice.currency)}</span><strong>{money(item.billed_packs * item.unit_price_eur, invoice.currency)}</strong></div>)}</div>)}</div>
          <div className="invoice-paper-bottom"><label>Internal notes<textarea rows={3} readOnly={!editable} value={invoice.internal_notes ?? ""} onChange={event => setInvoice(current => current ? { ...current, internal_notes: event.target.value } : current)} placeholder="Private accounting note" /></label><div><span>Invoice total</span><strong>{money(invoice.items.reduce((total, item) => total + item.billed_packs * item.unit_price_eur, 0), invoice.currency)}</strong></div></div>
        </div>
        {editable ? <footer className="invoice-modal-actions"><button type="button" className="invoice-secondary-button" data-guide={JSON.stringify(invoice) !== invoiceBaseline ? "Save the invoice changes" : undefined} data-guide-priority="10" disabled={busy || revisionConflict} onClick={saveDraft}>Save changes</button><button type="button" className="primary-button" data-guide={reviewedInvoice === invoice.invoice_id ? "Approve the reviewed invoice" : undefined} data-guide-detail="Check quantities, prices and notes first. Approval locks the invoice amounts." data-guide-priority="20" disabled={busy || revisionConflict} onClick={approveDraft}><CircleCheckBig size={18} /> Approve and create final invoice</button></footer> : <div className="invoice-modal-actions"><button type="button" className="invoice-secondary-button" data-guide={reviewedInvoice === invoice.invoice_id ? "Open the printable final invoice" : undefined} data-guide-priority="20" onClick={() => openDocument(`/invoicing/${invoice.invoice_id}/html`)}><FileText size={17} /> Open printable invoice</button></div>}
      </section></div>}
    </div>
  );
}

