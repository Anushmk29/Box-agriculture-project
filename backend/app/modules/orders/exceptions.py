"""Human operational closure; no payment, refund or inventory-accounting effects."""
from sqlalchemy import func, or_, select

from app.core.policy import Action, require
from app.core.revisions import require_revision
from app.core.values import required_text
from app.core.work_limits import WorkLimit
from app.models.audit import AuditLog
from app.models.invoice import Invoice, InvoiceLineItem
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus
from app.models.packing import Box
from app.models.procurement import MasterPurchasingList, PurchasingItem, PurchasingOrderSource
from app.modules.procurement.batch import PurchasingBatchService
from app.modules.workflow.service import WorkflowService

TERMINAL = {OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE}
BEFORE_BUYING = {OrderStatus.SPLIT_AND_VALIDATED, OrderStatus.PURCHASING_CONSOLIDATED}


def zero_allocation():
    return (ClientOrder.items.any(), ~ClientOrder.items.any(or_(
        ClientOrderItem.confirmed_pack_count.is_(None), ClientOrderItem.confirmed_pack_count != 0,
        ClientOrderItem.confirmed_net_weight_kg.is_(None), ClientOrderItem.confirmed_net_weight_kg != 0)))


def batches_for_order(db, order_id, raw_id):
    return db.query(MasterPurchasingList).filter(or_(MasterPurchasingList.raw_order_file_id == raw_id,
        MasterPurchasingList.sources.any(PurchasingOrderSource.raw_order_file_id == raw_id),
        MasterPurchasingList.sources.any(PurchasingOrderSource.client_order_id == order_id)))


def resolve_order(db, *, order_id, outcome, reason, expected_revision, manual, actor):
    require(actor, Action.RESOLVE_ORDERS)
    if manual:
        require(actor, Action.RESOLVE_LATE_CANCELLATION)
    reason = required_text(reason, limit=512)
    raw_id = db.query(ClientOrder.raw_order_file_id).filter(ClientOrder.id == order_id).scalar()
    if raw_id is None:
        raise LookupError("Order not found")
    # Serialize with purchase confirmation before changing shared batch demand.
    batches = batches_for_order(db, order_id, raw_id).order_by(MasterPurchasingList.id).limit(501).with_for_update().populate_existing().all()
    if len(batches) > 500:
        raise WorkLimit("This order has too many source batches; arrange a controlled review")
    order = db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().one()
    require_revision("order", order.id, order.packing_revision, expected_revision)
    if {batch.id for batch in batches} != {identity for (identity,) in batches_for_order(db, order_id, raw_id).with_entities(MasterPurchasingList.id).all()}:
        raise ValueError("The purchase selection changed; reload the order")
    invoiced = db.query(Invoice.id).filter(or_(Invoice.client_order_id == order_id,
        Invoice.id.in_(select(InvoiceLineItem.invoice_id).where(InvoiceLineItem.source_order_id == order_id)))).first()
    if invoiced:
        raise ValueError("An invoice already exists; your team handles changes outside this system")
    before_buying = order.status in BEFORE_BUYING and all(batch.status == OrderStatus.PURCHASING_CONSOLIDATED for batch in batches)
    if outcome == OrderStatus.CANCELLED:
        if not before_buying and not manual:
            raise ValueError("Buying or packing has started; Owner/Admin must record its manual resolution")
        if not before_buying:
            require(actor, Action.RESOLVE_LATE_CANCELLATION)
        if before_buying and db.query(Box.id).filter_by(client_order_id=order_id).first():
            raise ValueError("Packing evidence exists; Owner/Admin must review the inconsistent stage")
    elif outcome == OrderStatus.UNAVAILABLE:
        if manual:
            raise ValueError("Unavailable closure does not use manual cancellation")
        if not db.query(ClientOrder.id).filter(ClientOrder.id == order_id, *zero_allocation()).first():
            raise ValueError("Every line must have explicitly confirmed zero packs and zero weight")
        if db.query(Box.id).filter_by(client_order_id=order_id).first():
            raise ValueError("Boxes already exist; review packing before closing as unavailable")
    else:
        raise ValueError("Unsupported order outcome")
    workflow = WorkflowService(db)
    # Preflight *all* shared inputs before any mutation.
    for batch in batches if before_buying else []:
        size = db.query(func.count(PurchasingItem.id)).filter_by(purchasing_list_id=batch.id).scalar() or 0
        if size > 20000:
            raise WorkLimit("This purchase batch exceeds the correction budget; arrange a controlled review")
        if any(item.confirmed_purchased_pack_count is not None or item.confirmed_purchased_weight_kg is not None for item in batch.items):
            raise ValueError("Purchase evidence already exists; Owner/Admin must resolve it manually")
    workflow.terminate(target=order, to_status=outcome, actor=actor, target_table="client_orders", reason=reason)
    if before_buying:
        service = PurchasingBatchService(db)
        for batch in batches:
            before = {"ordered_weight_kg": sum(float(item.ordered_net_weight_kg or 0) for item in batch.items),
                "skus": batch.total_skus_count}
            demand = service.ordered_packs(batch)
            # Weight comes from the same source scope and excludes cancelled rows.
            sources = db.query(PurchasingOrderSource.raw_order_file_id, PurchasingOrderSource.client_order_id).filter_by(purchasing_list_id=batch.id).limit(10001).all()
            if len(sources) > 10000:
                raise WorkLimit("Too many purchase sources; arrange a controlled review")
            raw_ids = [key for key, _ in sources if key] + ([batch.raw_order_file_id] if batch.raw_order_file_id else [])
            order_ids = [key for _, key in sources if key]
            totals = service._demand(raw_ids, order_ids)
            if set(totals) - {(item.product_id, item.pack_size) for item in batch.items}:
                raise ValueError("Purchase demand is inconsistent; review this batch before cancelling")
            for item in list(batch.items):
                key = (item.product_id, item.pack_size)
                if key not in totals:
                    db.delete(item)
                else:
                    item.ordered_net_weight_kg = totals[key][1]
                    service._apply_buffer(item, item.buffer_percentage or 0, demand[key])
            batch.total_skus_count = len(totals)
            db.add(AuditLog(event_type="PURCHASE_DEMAND_RECALCULATED", actor_id=actor.id,
                actor_role=actor.role.value, target_table="master_purchasing_lists", target_id=batch.id,
                before_state=before, after_state={"cancelled_order_id": order.id, "reason": reason,
                    "ordered_weight_kg": sum(float(weight) for _, weight in totals.values()), "skus": len(totals)}))
            if not totals:
                workflow.terminate(target=batch, to_status=OrderStatus.CANCELLED, actor=actor,
                    target_table="master_purchasing_lists", reason="All source orders cancelled: " + reason)
            db.expire(batch, ["items"])
    db.flush()
    return {"order_id": order.id, "status": order.status.value, "revision": order.packing_revision,
            "reason": reason, "automatic_carry_forward": False}
