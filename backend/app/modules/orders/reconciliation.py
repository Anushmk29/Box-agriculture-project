"""Merge future SKU demand while preserving confirmed packing and invoice history."""

from app.core.policy import Action, require
from app.models.audit import AuditLog
from app.models.master_data import ClientSkuMapping, CustomerSellingPrice, PriceList, Product
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus
from app.models.procurement import MasterPurchasingList, PurchasingItem
from app.modules.procurement.batch import PurchasingBatchService


def merge_products(db, *, source_id, target_id, actor):
    require(actor, Action.MERGE_PRODUCTS)
    if source_id == target_id:
        raise ValueError("Choose two different master products")
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_([source_id, target_id])).with_for_update().all()}
    if len(products) != 2 or any(not p.is_active for p in products.values()):
        raise ValueError("Select two active master products")
    source = products[source_id]

    # Do not silently override approved customer prices.
    for model, party_fields in [(PriceList, ("customer_id",)), (CustomerSellingPrice, ("main_customer_id", "sub_customer_id"))]:
        source_prices = db.query(model).filter_by(product_id=source_id).all()
        target_prices = db.query(model).filter_by(product_id=target_id).all()
        price_field = "unit_price_eur" if model is PriceList else "selling_price_eur"
        for left in source_prices:
            for right in target_prices:
                same_party = all(getattr(left, field) == getattr(right, field) for field in party_fields)
                overlaps = left.valid_from <= (right.valid_to or "9999-12-31") and right.valid_from <= (left.valid_to or "9999-12-31")
                if same_party and left.pack_size == right.pack_size and overlaps and getattr(left, price_field) != getattr(right, price_field):
                    raise ValueError("Approved prices conflict; reconcile prices before merging these SKUs")
        for price in source_prices:
            price.product_id = target_id

    orders = db.query(ClientOrder).join(ClientOrderItem).filter(
        ClientOrderItem.product_id == source_id,
        ClientOrder.status.in_([OrderStatus.SPLIT_AND_VALIDATED, OrderStatus.PURCHASING_CONSOLIDATED]),
    ).with_for_update().all()
    for order in orders:
        for line in list(order.items):
            if line.product_id != source_id:
                continue
            existing = next((other for other in order.items if other.product_id == target_id and other.pack_size == line.pack_size), None)
            if existing:
                existing.ordered_pack_count += line.ordered_pack_count
                existing.estimated_net_weight_kg = round(float(existing.estimated_net_weight_kg) + float(line.estimated_net_weight_kg), 2)
                order.items.remove(line)
            else:
                line.product_id = target_id
        order.total_ordered_items_count = sum(line.ordered_pack_count for line in order.items)
        order.total_estimated_net_weight_kg = round(sum(float(line.estimated_net_weight_kg) for line in order.items), 2)

    for mapping in db.query(ClientSkuMapping).filter_by(product_id=source_id).all():
        mapping.product_id = target_id
    for name in {source.t_code, source.name_en, source.name_vi, source.name_th}:
        if not name:
            continue
        mapping = db.query(ClientSkuMapping).filter_by(main_customer_id=None, client_raw_name=name).first()
        if mapping:
            if mapping.product_id not in {source_id, target_id}:
                raise ValueError("An approved alias maps to another product; reconcile that mapping first")
            mapping.product_id = target_id
        else:
            db.add(ClientSkuMapping(client_raw_name=name, client_sku_code=source.t_code if name == source.t_code else None,
                product_id=target_id, standard_pack_size=None, is_verified=True))
    source.is_active = False
    db.flush()

    batches = db.query(MasterPurchasingList).join(PurchasingItem).filter(
        PurchasingItem.product_id == source_id,
        MasterPurchasingList.status == OrderStatus.PURCHASING_CONSOLIDATED,
    ).with_for_update().all()
    service = PurchasingBatchService(db)
    for batch in batches:
        sources = list(batch.sources)
        for item in batch.items:
            if item.product_id == source_id:
                item.product_id = target_id
        db.flush()
        service.consolidate(raw_file_ids=[s.raw_order_file_id for s in sources if s.raw_order_file_id],
            client_order_ids=[s.client_order_id for s in sources if s.client_order_id],
            actor=actor, batch_ref=batch.batch_reference, shipment_date=batch.shipment_date, route_code=batch.route_code)
        db.expire(batch, ["items", "sources"])
    db.add(AuditLog(event_type="MASTER_SKUS_MERGED", actor_id=actor.id, actor_role=actor.role.value,
        target_table="products", target_id=target_id,
        before_state={"source_id": source_id, "source_code": source.t_code},
        after_state={"target_id": target_id, "affected_orders": len(orders), "rebuilt_batches": len(batches)}))
    db.flush()
    return {"message": "Master SKUs merged; confirmed history retained", "affected_orders": len(orders), "rebuilt_batches": len(batches)}
