"use client";

import { LoadingSkeleton } from "@/components/loading-skeleton";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { StatusMessage } from "@/components/status-message";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";

type MissingItem = { product_id: string; product_code: string; product_name: string; pack_size: string };
type PriceIssue = { order_id: string; order_ref: string; customer_name: string; shipment_date: string; items: MissingItem[] };
const priceKey = (orderId: string, item: MissingItem) => JSON.stringify([orderId, item.product_id, item.pack_size]);

export default function ResolvePricesPage() {
  const router = useRouter();
  const [issues, setIssues] = useState<PriceIssue[]>([]);
  const [prices, setPrices] = useState<Record<string, string>>({});
  useUnsavedChanges(Object.values(prices).some(value => value.trim() !== ""));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const missingCount = issues.reduce((sum, issue) => sum + issue.items.filter(item => !(Number(prices[priceKey(issue.order_id, item)]) > 0)).length, 0);
  const [returnPath, setReturnPath] = useState("/billing");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ids = [...new Set(params.getAll("orderId"))];
    const back = new URLSearchParams({ mode: params.get("mode") || "individual" });
    ids.forEach(id => back.append("orderId", id));
    const request = ids.length ? api<{ issues: PriceIssue[] }>("/invoicing/price-check", {
      method: "POST", body: JSON.stringify({ order_ids: ids }),
    }, { background: true }) : Promise.reject(new Error("Choose an order in Billing to resolve its selling prices."));
    request.then(result => { setIssues(result.issues); setReturnPath(`/billing?${back}`); })
      .catch(reason => setError(reason instanceof Error ? reason.message : "Could not check prices"))
      .finally(() => setLoading(false));
  }, []);

  async function savePrices(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const items = issues.flatMap(issue => issue.items.map(item => ({
        order_id: issue.order_id, product_id: item.product_id, pack_size: item.pack_size,
        selling_price: Number(prices[priceKey(issue.order_id, item)]),
      })));
      if (items.some(item => !Number.isFinite(item.selling_price) || item.selling_price <= 0)) {
        throw new Error("Enter a selling price greater than zero for every listed pack.");
      }
      await api("/invoicing/resolve-prices", { method: "POST", body: JSON.stringify({ items }) });
      router.push(`${returnPath}&pricesResolved=1`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not save prices"); }
    finally { setBusy(false); }
  }

  return <div className="page billing-workspace">
    <header className="page-header"><div><p className="eyebrow">Billing · Resolve missing prices</p>
      <h1>Complete selling prices</h1><p>Packing is complete. Enter the selling price per pack for the products below to continue invoicing.</p></div>
      <Link data-guide={error ? "Choose an order in Billing" : undefined} data-guide-priority="1" data-guide-detail="Open the order whose selling prices need completing." href={returnPath}>Back to billing</Link>
    </header>
    <StatusMessage error={error} />
    {loading ? <LoadingSkeleton label="Checking prices for the completed final pack…" compact /> :
      issues.length ? <form onSubmit={savePrices} className="price-resolution-form">
        <p role="status">{missingCount} prices still need a positive amount.</p><p className="task-note">Prices apply from the shipment date shown and remain until an admin or finance user changes them. Prices do not expire automatically. Currency: THB.</p>
        {issues.map(issue => <section className="panel price-resolution-order" key={issue.order_id}>
          <h2>{issue.customer_name}</h2><p>{issue.order_ref}</p>
          <p><strong>Effective shipment date: {issue.shipment_date}</strong> · {issue.items.length} missing prices</p>
          <div className="price-resolution-items">{issue.items.map(item => <label className="price-resolution-row" key={priceKey(issue.order_id, item)}>
            <span><strong>{item.product_code} · {item.product_name}</strong><small>Pack size: {item.pack_size}</small></span>
            <span>Selling price / pack (THB)<input aria-label={`Selling price for ${item.product_code} ${item.pack_size} ${issue.customer_name}`}
              data-guide={!(Number(prices[priceKey(issue.order_id, item)]) > 0) ? `Enter the selling price for ${item.product_code}` : undefined} data-guide-detail="Enter the price per pack in THB. Each missing price must be greater than zero."
              type="number" inputMode="decimal" min="0.01" step="0.01" required disabled={busy}
              value={prices[priceKey(issue.order_id, item)] ?? ""}
              onChange={event => setPrices(current => ({ ...current, [priceKey(issue.order_id, item)]: event.target.value }))} />{prices[priceKey(issue.order_id, item)] !== undefined && !(Number(prices[priceKey(issue.order_id, item)]) > 0) && <small className="field-error">Enter an amount greater than zero.</small>}</span>
          </label>)}</div>
        </section>)}
        <button type="submit" className="primary-button" disabled={busy}>{busy ? "Saving prices…" : "Save prices and return to billing"}</button>
      </form> : !error && <section className="panel price-resolution-order"><CheckCircle2 aria-hidden="true" /><h2>All selling prices are available</h2>
        <p>You can continue with the completed packing.</p><Link className="primary-button" href={returnPath}>Continue to billing</Link></section>}
  </div>;
}
