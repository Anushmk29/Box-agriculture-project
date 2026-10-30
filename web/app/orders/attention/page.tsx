"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { beginSubmission, pendingSubmission, finishSubmission } from "@/lib/submission";
import { useSession } from "@/lib/session-context";
import { RecordRegister, recordLink, type RegisterRow } from "@/components/record-register";
import { orderColumns } from "@/lib/register-columns";
import { TaskDialog } from "@/components/task-dialog";
import { StatusMessage } from "@/components/status-message";
import { OrdersNavigation } from "@/components/orders-navigation";
import { safeDestination } from "@/lib/workflow";

export default function OrdersNeedingAttention() { return <Suspense fallback={<p>Loading orders needing attention…</p>}><Exceptions /></Suspense>; }

function Exceptions() {
  const params = useSearchParams(); const orderId = params.get("orderId");
  const router = useRouter(); const session = useSession();
  const returnTo = safeDestination(params.get("returnTo"));
  const listParams = new URLSearchParams(params.toString());
  listParams.delete("orderId"); listParams.delete("returnTo");
  const listHref = returnTo === "/orders/attention" || returnTo.startsWith("/orders/attention?")
    ? returnTo : `/orders/attention${listParams.size ? `?${listParams}` : ""}`;
  const [record, setRecord] = useState<RegisterRow>();
  const [reason, setReason] = useState(""); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""); const [message, setMessage] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState(false);
  const path = orderId ? `/order-exceptions/${encodeURIComponent(orderId)}/resolve` : "";
  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      setRecord(undefined); setReason(""); setError(""); setPending(false);
      if (!orderId) return;
      try { setPending(Boolean(pendingSubmission(window.sessionStorage, session?.user_id, path))); }
      catch { setError("Saved submission could not be read. Review this order before submitting again."); return; }
      api<RegisterRow>(`/order-exceptions/${encodeURIComponent(orderId)}`).then(value => {
        if (active) setRecord(value);
      }).catch(value => { if (active) setError(String(value)); });
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [orderId, path, session?.user_id, refresh]);

  async function submit(outcome?: string, manual = false) {
    if (busy || !record || !session?.user_id) return;
    setBusy(true); setError("");
    let intent;
    try {
      intent = pendingSubmission(window.sessionStorage, session.user_id, path) ?? beginSubmission(window.sessionStorage,
        session.user_id, path, JSON.stringify({ outcome, reason: reason.trim(), expected_revision: record.revision,
          manual_resolution_confirmed: manual }));
      setPending(true);
      await api(intent.path, { method: "POST", headers: { "Idempotency-Key": intent.key }, body: intent.body });
      finishSubmission(window.sessionStorage, session.user_id, intent); setPending(false);
      setMessage("Order resolved. Its original records are preserved; no new order was created.");
      setRefresh(value => value + 1); router.push("/orders/attention?view=completed");
    } catch (value) {
      // Unknown outcomes keep their original key/body for an explicit retry.
      const code = value instanceof ApiError && value.detail && typeof value.detail === "object" && "code" in value.detail ? String(value.detail.code) : "";
      if (intent && value instanceof ApiError && ([400, 403, 404, 422, 428].includes(value.status)
          || value.status === 409 && !["REQUEST_IN_PROGRESS", "REQUEST_RESULT_UNAVAILABLE"].includes(code))) {
        finishSubmission(window.sessionStorage, session.user_id, intent); setPending(false);
      }
      setError(value instanceof Error ? value.message : String(value));
    } finally { setBusy(false); }
  }

  return <div className="page"><OrdersNavigation current="attention" />
    <StatusMessage error={orderId ? "" : error} success={message} />
    <RecordRegister mobileCards title="Orders needing attention" endpoint="/order-exceptions" identity="order_id" actionLabel={() => "Review order"}
      description="Resolve full shortages and cancellations. Use All to find an order to cancel, or Completed to review past resolutions."
      emptyMessage="No orders need shortage resolution. Use All to find an order to cancel."
      columns={orderColumns} refreshKey={refresh} statuses={["SPLIT_AND_VALIDATED", "PURCHASING_CONSOLIDATED", "HANDOFF_01_SENT", "CANCELLED", "UNAVAILABLE"]}
      open={row => recordLink("/orders/attention", "orderId", row.order_id)} />
    {orderId && <TaskDialog title={String(record?.order_ref_id || "Order needing attention")} busy={busy}
      onClose={() => router.push(listHref)} description="A resolution preserves demand and audit history. Payments and later invoice matters are handled by your team outside this system.">
      <StatusMessage error={error} />
      {!record ? <p>Loading order…</p> : <>
        <p>{String(record.customer_name)} · {String(record.status)}</p>
        {record.resolution_reason && <p>Resolution: {String(record.resolution_reason)}</p>}
        {pending ? <><p>A submission is pending. Retry its original details to confirm the result.</p><button disabled={busy} onClick={() => void submit()}>Retry pending resolution</button></> : <>
          {(record.can_close_unavailable || record.can_cancel || record.can_record_manual_cancellation) ? <>
            <label>Reason / manual resolution details<textarea data-guide={reason.trim().length < (record.can_cancel || record.can_close_unavailable ? 3 : 10) ? "Explain how this order should be resolved" : undefined} data-guide-detail="Record the reason and any manual handling before choosing the resolution." rows={4} maxLength={512} value={reason} onChange={event => setReason(event.target.value)} /></label>
            {record.can_close_unavailable && <button data-guide="Record the unavailable order" data-guide-detail="Check the reason before closing the order as unavailable." disabled={busy || reason.trim().length < 3} onClick={() => void submit("UNAVAILABLE")}>Close as unavailable</button>}
            {record.can_cancel && <button data-guide="Record the reviewed cancellation" data-guide-detail="Check the order and reason before cancelling it." disabled={busy || reason.trim().length < 3} onClick={() => void submit("CANCELLED")}>Cancel before buying</button>}
            {!record.can_cancel && record.can_record_manual_cancellation && <><p>Confirm that you have manually resolved the purchased/packed goods. This action does not move stock, refund money or change shared purchase history.</p><button disabled={busy || reason.trim().length < 10} onClick={() => void submit("CANCELLED", true)}>Confirm manual handling and cancel</button></>}
          </> : <p>This order is resolved or already invoiced. It cannot be changed here.</p>}
        </>}
      </>}
    </TaskDialog>}
  </div>;
}
