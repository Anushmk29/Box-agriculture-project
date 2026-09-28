"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronRight,
  ClipboardCheck,
  PackageOpen,
  ReceiptText,
  RefreshCw,
  Scale,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import { StatusMessage } from "@/components/status-message";

type OperationsSummary = {
  active_orders: number;
  closed_orders: number;
  awaiting_procurement: number;
  awaiting_packing: number;
  awaiting_billing?: number;
  boxes_missing_weights: number;
  open_balance_eur?: number;
  orders_by_status?: Record<string, number>;
};

export default function OperationsPage() {
  const [summary, setSummary] = useState<OperationsSummary | null>(null);
  const [error, setError] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    let isMounted = true;
    api<OperationsSummary>("/operations/summary")
      .then(data => {
        if (isMounted) {
          setSummary(data);
          setError("");
        }
      })
      .catch(reason => {
        if (isMounted) {
          setError(reason instanceof Error ? reason.message : "Dashboard could not be loaded");
        }
      });

    const interval = setInterval(() => {
      api<OperationsSummary>("/operations/summary")
        .then(data => {
          if (isMounted) {
            setSummary(data);
            setError("");
          }
        })
        .catch(() => {});
    }, 20000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const handleManualRefresh = () => {
    setIsRefreshing(true);
    api<OperationsSummary>("/operations/summary")
      .then(data => {
        setSummary(data);
        setError("");
      })
      .catch(reason => {
        setError(reason instanceof Error ? reason.message : "Dashboard could not be loaded");
      })
      .finally(() => {
        setTimeout(() => setIsRefreshing(false), 400);
      });
  };

  const stageCounts = [
    summary?.active_orders ?? 0,
    summary?.awaiting_procurement ?? 0,
    summary?.awaiting_packing ?? 0,
    summary?.awaiting_billing ?? 0,
  ];
  const boxesToWeigh = summary?.boxes_missing_weights ?? 0;
  const loading = summary === null;
  const fmtEur = (value: number) =>
    new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" }).format(value);

  const stepsData = [
    { step: "01", title: "Order intake", team: "Intake desk", detail: "Upload customer orders, resolve SKUs against the master catalogue, and split group orders.", href: "/orders" },
    { step: "02", title: "Procurement", team: "Buyers", detail: "Confirm supplier purchases and send Handoff 01 to the packing floor.", href: "/procurement" },
    { step: "03", title: "Packing floor", team: "Packing floor", detail: "Pack cartons, record net weights, seal boxes, and return Handoff 02.", href: "/packing" },
    { step: "04", title: "Billing", team: "Accounts", detail: "Issue and approve invoices, record payments, and issue receipts.", href: "/billing" },
  ].filter(step => step.href !== "/billing" || summary?.awaiting_billing !== undefined).map((step, index) => {
    const count = stageCounts[index];
    const attention = index === 2 && boxesToWeigh > 0;
    return {
      ...step,
      chip: attention ? `${boxesToWeigh} to weigh` : count === 0 ? "Clear" : `${count} waiting`,
      chipClass: attention ? "rose" : count === 0 ? "emerald" : "amber",
      assignee: step.team,
      countText: `${count} in queue`,
    };
  });

  return (
    <div className="page">
      <section className="page-header split-header">
        <div>
          <h1>Operations</h1>
          <p>Where every order sits right now, from intake to payment. Refreshes every 20 seconds.</p>
        </div>
        <button
          type="button"
          className="action-secondary-btn"
          onClick={handleManualRefresh}
          aria-label="Refresh dashboard"
        >
          <RefreshCw size={15} className={isRefreshing ? "spin" : undefined} /> Refresh
        </button>
      </section>

      <StatusMessage error={error} />

      {boxesToWeigh > 0 && (
        <section className="action-priority-banner" aria-label="Boxes waiting for weights">
          <div className="action-banner-left">
            <div className="action-banner-icon">
              <AlertTriangle size={22} />
            </div>
            <div className="action-banner-content">
              <h3>
                <span className="action-tag-urgent">Blocking billing</span>
                <span>{boxesToWeigh} {boxesToWeigh === 1 ? "box needs" : "boxes need"} a weight</span>
              </h3>
              <p>Invoices can only be issued once every box in the order is weighed and sealed.</p>
            </div>
          </div>
          <div className="action-banner-actions">
            <Link href="/packing" className="action-primary-btn">
              <Scale size={15} />
              <span>Weigh boxes</span>
            </Link>
          </div>
        </section>
      )}

      <section className="metric-grid-v2" aria-label="Live operational metrics" aria-busy={loading}>
        <Link href="/orders" className="metric-card-v2">
          <div className="metric-card-top">
            <div className="metric-icon-box teal"><PackageOpen size={20} /></div>
          </div>
          <div className="metric-number-row">
            <span className="metric-number">{loading ? "–" : stageCounts[0]}</span>
          </div>
          <div className="metric-title">Active orders</div>
          <div className="metric-footer-stats">
            <span>Closed</span>
            <strong>{summary?.closed_orders ?? "–"}</strong>
          </div>
        </Link>

        <Link href="/procurement" className="metric-card-v2">
          <div className="metric-card-top">
            <div className="metric-icon-box amber"><ClipboardCheck size={20} /></div>
          </div>
          <div className="metric-number-row">
            <span className="metric-number">{loading ? "–" : stageCounts[1]}</span>
          </div>
          <div className="metric-title">Waiting for procurement</div>
          <div className="metric-footer-stats">
            <span>Waiting for packing</span>
            <strong>{loading ? "–" : stageCounts[2]}</strong>
          </div>
        </Link>

        <Link href="/packing" className="metric-card-v2">
          <div className="metric-card-top">
            <div className="metric-icon-box blue"><Scale size={20} /></div>
            {boxesToWeigh > 0 && <span className="metric-trend-pill warning">Needs weight</span>}
          </div>
          <div className="metric-number-row">
            <span className={boxesToWeigh > 0 ? "metric-number amber" : "metric-number"}>{loading ? "–" : boxesToWeigh}</span>
          </div>
          <div className="metric-title">Boxes without a weight</div>
          <div className="metric-footer-stats">
            <span>Packed boxes must be weighed before invoicing</span>
          </div>
        </Link>

        {summary?.awaiting_billing !== undefined ? <Link href="/billing" className="metric-card-v2">
          <div className="metric-card-top">
            <div className="metric-icon-box emerald"><ReceiptText size={20} /></div>
          </div>
          <div className="metric-number-row">
            <span className="metric-number">{loading ? "–" : fmtEur(summary?.open_balance_eur ?? 0)}</span>
          </div>
          <div className="metric-title">Unpaid invoice balance</div>
          <div className="metric-footer-stats">
            <span>Orders waiting for billing</span>
            <strong>{loading ? "–" : stageCounts[3]}</strong>
          </div>
        </Link> : null}
      </section>

      <section className="workflow-panel-v2" aria-label="Order stages">
        <div className="workflow-panel-header">
          <h2 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 700 }}>Order stages</h2>
        </div>
        <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {stepsData.map(step => (
            <li className="accordion-row-container" key={step.step}>
              <Link href={step.href} className="accordion-trigger">
                <div className="accordion-left">
                  <div className="step-avatar-box">{step.step}</div>
                  <div>
                    <div className="step-content-heading">
                      <h4>{step.title}</h4>
                      {!loading && <span className={`status-chip ${step.chipClass}`}>{step.chip}</span>}
                      <span className="step-assignee-text">{step.assignee}</span>
                    </div>
                    <p className="step-desc-text">{step.detail}</p>
                  </div>
                </div>
                <div className="accordion-right">
                  <ChevronRight size={20} aria-hidden />
                </div>
              </Link>
            </li>
          ))}
        </ol>
      </section>

      <aside className="invariant-note-v2" aria-label="Billing rule">
        <ShieldCheck size={22} style={{ color: "var(--leaf)", flexShrink: 0 }} />
        <div>
          <strong>Final packing is the financial source of truth.</strong>
          <p>
            An order can only be invoiced after every box has a recorded net weight, is sealed, and Handoff 02 is
            signed by the packing floor.
          </p>
        </div>
      </aside>
    </div>
  );
}
