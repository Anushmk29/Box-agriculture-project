"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { t, useLanguage } from "@/lib/language";
import { confirmAction } from "@/lib/confirm";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { TaskDialog } from "./task-dialog";
import { StatusMessage } from "./status-message";

type Contact = { customer_id: string; client_code: string; company_name: string; role: string; billing_email: string; destination_country: string };
type Details = { invoice_id: string; revision: number; customers: Contact[] };

export function BillingCustomerDetails({ invoiceId, onClose, onSaved }: {
  invoiceId: string; onClose: () => void; onSaved: (invoiceId: string, revision: number) => void;
}) {
  useLanguage();
  const [saved, setSaved] = useState<Details>();
  const [customers, setCustomers] = useState<Contact[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const dirty = Boolean(saved && JSON.stringify(customers) !== JSON.stringify(saved.customers));
  useUnsavedChanges(dirty);
  useEffect(() => {
    let active = true;
    api<Details>(`/invoicing/${encodeURIComponent(invoiceId)}/customer-details`).then(value => {
      if (active) { setSaved(value); setCustomers(value.customers); setError(""); }
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "Unable to load customer details"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [invoiceId, reload]);
  async function mayDiscard() {
    return !dirty || await confirmAction({ title: "Discard customer detail changes?", body: "Your customer detail changes have not been saved.", confirmLabel: "Discard changes", tone: "danger" });
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!saved || busy) return;
    setBusy(true); setError("");
    try {
      const updated = await api<Details>(`/invoicing/${encodeURIComponent(invoiceId)}/customer-details`, {
        method: "PUT", body: JSON.stringify({ expected_revision: saved.revision, customers: customers.map(customer => {
          const previous = saved.customers.find(item => item.customer_id === customer.customer_id)!;
          return { customer_id: customer.customer_id, expected: { billing_email: previous.billing_email, destination_country: previous.destination_country },
            billing_email: customer.billing_email.trim(), destination_country: customer.destination_country.trim() };
        }) }),
      });
      onSaved(updated.invoice_id, updated.revision);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save customer details"); }
    finally { setBusy(false); }
  }
  return <TaskDialog title={t("Customer details")} description={t("Complete the country and billing email before issuing the invoice. Saved details are reused for future invoices.")}
    busy={busy} guidance={false} onClose={() => { void mayDiscard().then(discard => { if (discard) onClose(); }); }}>
    <StatusMessage error={error} />
    {error && <button type="button" className="invoice-secondary-button" disabled={busy || loading} onClick={() => { void mayDiscard().then(discard => { if (discard) { setLoading(true); setSaved(undefined); setReload(value => value + 1); } }); }}>{t("Reload customer details")}</button>}
    {loading ? <p role="status">{t("Loading customer details…")}</p> : saved && <form onSubmit={save} className="customer-details-form">
      {customers.map(customer => <fieldset key={customer.customer_id}>
        <legend>{t(customer.role)} · {customer.company_name} ({customer.client_code})</legend>
        <label>{t("Country")}<input aria-label={t(`Country for ${customer.client_code}`)} value={customer.destination_country} maxLength={64} required disabled={busy}
          onChange={event => setCustomers(current => current.map(item => item.customer_id === customer.customer_id ? { ...item, destination_country: event.target.value } : item))} /></label>
        <label>{t("Billing email")}<input aria-label={t(`Billing email for ${customer.client_code}`)} type="email" value={customer.billing_email} maxLength={255} required disabled={busy}
          onChange={event => setCustomers(current => current.map(item => item.customer_id === customer.customer_id ? { ...item, billing_email: event.target.value } : item))} /></label>
      </fieldset>)}
      <button className="primary-button" disabled={busy}>{busy ? t("Saving…") : t("Save customer details")}</button>
    </form>}
  </TaskDialog>;
}
