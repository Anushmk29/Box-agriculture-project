"""Purchasing batch: consolidate order demand, apply buffers, until the buyer confirms.

Buffers always apply to the base demand of the batch's source orders, so editing a
buffer twice never compounds, and they are frozen once the batch is confirmed.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.policy import Action, require
from app.core.values import INT32_MAX, WEIGHT_STEP, buffer_percentage, purchase_weight
from app.core.work_limits import MAX_DEMANDS, MAX_ITEMS, WorkLimit
from app.models.audit import AuditLog
from app.models.master_data import Product
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingItem, PurchasingOrderSource
from app.models.user import User
from app.modules.workflow.service import WorkflowService

DemandKey = tuple[str, str]  # (product_id, pack_size)


def load_batch_orders(db: Session, batch: MasterPurchasingList) -> set[ClientOrder]:
    """Load one shipment's orders and packing identities without per-shop reads."""
    raw_ids = {source.raw_order_file_id for source in batch.sources if source.raw_order_file_id}
    order_ids = {source.client_order_id for source in batch.sources if source.client_order_id}
    if not raw_ids and not order_ids:
        return set()
    return set(db.query(ClientOrder).filter(
        or_(ClientOrder.raw_order_file_id.in_(raw_ids), ClientOrder.id.in_(order_ids)),
        ~ClientOrder.status.in_({OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE}),
    ).options(
        selectinload(ClientOrder.items).selectinload(ClientOrderItem.product),
        selectinload(ClientOrder.customer),
    ).all())


class PurchasingBatchService:
    def __init__(self, db: Session):
        self.db = db

    # -- consolidation -----------------------------------------------------

    def consolidate_raw_file(self, raw_file_id: str, actor: User) -> MasterPurchasingList:
        raw_file = self.db.query(RawOrderFile).filter(RawOrderFile.id == raw_file_id).first()
        if not raw_file:
            raise ValueError(f"RawOrderFile {raw_file_id} not found")
        return self._build_batch(
            raw_file_ids=[raw_file.id],
            actor=actor,
            shipment_date=raw_file.shipment_target_date,
            batch_ref=f"PUR-{raw_file.shipment_target_date.replace('-', '')}-{raw_file.id}",
        )

    def rebuild_batch(self, batch_id: str, actor: User) -> MasterPurchasingList:
        """Recalculate an existing batch after catalogue corrections; keep its sources."""
        batch = self._get(batch_id)
        self._require_editable(batch)
        return self._build_batch(
            raw_file_ids=([source.raw_order_file_id for source in batch.sources if source.raw_order_file_id]
                          or ([batch.raw_order_file_id] if not batch.sources and batch.raw_order_file_id else [])),
            client_order_ids=[source.client_order_id for source in batch.sources if source.client_order_id],
            actor=actor, batch_ref=batch.batch_reference,
            shipment_date=batch.shipment_date, route_code=batch.route_code,
        )

    def _build_batch(
        self,
        raw_file_ids: list[str],
        actor: User,
        batch_ref: str,
        client_order_ids: list[str] | None = None,
        shipment_date: str | None = None,
        route_code: str | None = None,
    ) -> MasterPurchasingList:
        """Create one file's purchase batch or rebuild the same saved sources."""
        raw_file_ids = list(dict.fromkeys(raw_file_ids))
        client_order_ids = list(dict.fromkeys(client_order_ids or []))
        if len(raw_file_ids) > 500 or len(client_order_ids) > 500:
            raise WorkLimit("This batch exceeds the supported source limit")
        if not raw_file_ids and not client_order_ids:
            raise ValueError("At least one raw order file or client order is required")
        self._require_exist(RawOrderFile, raw_file_ids, "Raw order files")
        self._require_exist(ClientOrder, client_order_ids, "Client orders")
        selected_orders = self.db.query(ClientOrder).filter(
            (ClientOrder.raw_order_file_id.in_(raw_file_ids)) | (ClientOrder.id.in_(client_order_ids))
        ).filter(~ClientOrder.status.in_({OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE})).order_by(ClientOrder.id).limit(MAX_DEMANDS + 1).with_for_update().populate_existing().all()
        if set(client_order_ids) - {order.id for order in selected_orders}:
            raise ValueError("A selected order is already resolved")
        if len(selected_orders) > MAX_DEMANDS:
            raise WorkLimit("This batch exceeds the supported 1000-order limit")
        selected_ids = {order.id for order in selected_orders}
        if not selected_orders:
            raise ValueError("Selected sources have no validated customer orders")
        if any(order.status not in {OrderStatus.SPLIT_AND_VALIDATED, OrderStatus.PURCHASING_CONSOLIDATED} for order in selected_orders):
            raise ValueError("Confirmed orders cannot be rebuilt")
        dates = {order.raw_file.shipment_target_date for order in selected_orders}
        if len(dates) != 1 or (shipment_date and shipment_date not in dates):
            raise ValueError("Purchase batch orders must share one shipment date")
        shipment_date = shipment_date or next(iter(dates))

        mpl = self.db.query(MasterPurchasingList).filter_by(batch_reference=batch_ref).with_for_update().first()
        def source_order_ids(batch):
            if not batch.sources and batch.raw_order_file_id:
                return {order.id for order in selected_orders if order.raw_order_file_id == batch.raw_order_file_id}
            ids = set()
            for source in batch.sources:
                if source.raw_order_file:
                    ids.update(order.id for order in source.raw_order_file.client_orders if order.status not in {OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE})
                if source.client_order_id and source.client_order.status not in {OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE}:
                    ids.add(source.client_order_id)
            return ids
        if mpl and source_order_ids(mpl) != selected_ids:
            raise ValueError("Batch reference already belongs to different orders; use a new reference")
        if not mpl and (len(raw_file_ids) != 1 or client_order_ids):
            raise ValueError("New purchase batches must belong to one uploaded order file")
        inherited_buffers = defaultdict(lambda: [0.0, 0.0])
        selected_files = {order.raw_order_file_id for order in selected_orders}
        prior_batches = self.db.query(MasterPurchasingList).filter(
            or_(MasterPurchasingList.raw_order_file_id.in_(selected_files),
                MasterPurchasingList.sources.any(PurchasingOrderSource.raw_order_file_id.in_(selected_files)),
                MasterPurchasingList.sources.any(PurchasingOrderSource.client_order_id.in_(selected_ids)))
        ).with_for_update().all()
        for prior in prior_batches:
            if not mpl or prior.id != mpl.id:
                raise ValueError("Selected orders already belong to another purchase batch")
            for item in prior.items:
                values = inherited_buffers[(item.product_id, item.pack_size)]
                values[0] += float(item.ordered_net_weight_kg)
                values[1] += float(item.ordered_net_weight_kg) * float(item.buffer_percentage or 0) / 100
        self.db.flush()
        if mpl:
            if mpl.status != OrderStatus.PURCHASING_CONSOLIDATED:
                raise ValueError(
                    f"Batch {batch_ref} is already {mpl.status.value} and cannot be rebuilt"
                )
            self.db.query(PurchasingItem).filter_by(purchasing_list_id=mpl.id).delete()
            self.db.query(PurchasingOrderSource).filter_by(purchasing_list_id=mpl.id).delete()
        else:
            mpl = MasterPurchasingList(
                raw_order_file_id=raw_file_ids[0] if raw_file_ids else None,
                batch_reference=batch_ref,
                shipment_date=shipment_date,
                route_code=route_code,
                total_skus_count=0,
                status=OrderStatus.PURCHASING_CONSOLIDATED,
            )
            self.db.add(mpl)
            self.db.flush()

        for r_id in raw_file_ids:
            self.db.add(PurchasingOrderSource(purchasing_list_id=mpl.id, raw_order_file_id=r_id))
        for c_id in client_order_ids:
            self.db.add(PurchasingOrderSource(purchasing_list_id=mpl.id, client_order_id=c_id))

        demand = self._demand(raw_file_ids, client_order_ids)
        products = {
            p.id: p
            for p in self.db.query(Product).filter(Product.id.in_({k[0] for k in demand})).all()
        }
        for (product_id, pack_size), (packs, kg) in demand.items():
            product = products.get(product_id)
            item = PurchasingItem(
                purchasing_list_id=mpl.id,
                product_id=product_id,
                pack_size=pack_size,
                ordered_net_weight_kg=kg,
                total_required_pack_count=0,
                total_required_weight_kg=0,
            )
            default_pct = float(product.default_buffer_percentage or 0) if product else 0.0
            old_kg, old_buffer_kg = inherited_buffers[(product_id, pack_size)]
            if old_kg:
                buffer_pct = old_buffer_kg / old_kg * 100 if old_kg >= kg else (old_buffer_kg + (kg - old_kg) * default_pct / 100) / kg * 100
            else:
                buffer_pct = default_pct
            self._apply_buffer(item, round(buffer_pct, 2), packs)
            self.db.add(item)
        mpl.total_skus_count = len(demand)

        workflow = WorkflowService(self.db)
        for rf in self.db.query(RawOrderFile).filter(RawOrderFile.id.in_(raw_file_ids)).all():
            workflow.ensure(
                target=rf,
                to_status=OrderStatus.PURCHASING_CONSOLIDATED,
                actor=actor,
                target_table="raw_order_files",
            )
        for order in selected_orders:
            workflow.ensure(
                target=order,
                to_status=OrderStatus.PURCHASING_CONSOLIDATED,
                actor=actor,
                target_table="client_orders",
            )

        self.db.flush()
        self.db.expire(mpl, ["items", "sources"])
        return mpl

    # -- buffers -----------------------------------------------------------

    def set_item_buffer(
        self, item_id: str, buffer_pct: float, *, actor: User, save_as_default: bool = True
    ) -> PurchasingItem:
        require(actor, Action.EDIT_BUFFERS)
        item = self.db.query(PurchasingItem).filter_by(id=item_id).first()
        if not item:
            raise ValueError("PurchasingItem not found")
        self._require_editable(item.purchasing_list)
        base = self.ordered_packs(item.purchasing_list)
        self._set_buffer(item, buffer_pct, base, actor)
        if save_as_default and item.product:
            item.product.default_buffer_percentage = round(buffer_pct, 2)
        self.db.flush()
        return item

    def set_batch_buffer(self, batch_id: str, buffer_pct: float, *, actor: User) -> MasterPurchasingList:
        """Apply one buffer % to every line of the batch in a single transaction."""
        require(actor, Action.EDIT_BUFFERS)
        mpl = self._get(batch_id)
        self._require_editable(mpl)
        base = self.ordered_packs(mpl)
        for item in mpl.items:
            self._set_buffer(item, buffer_pct, base, actor)
        self.db.flush()
        return mpl

    # -- reads -------------------------------------------------------------

    def ordered_packs(self, mpl: MasterPurchasingList, *, pending_keys: set[DemandKey] | None = None) -> dict[DemandKey, int]:
        """Unbuffered pack demand per line, from the batch's source orders."""
        sources = self.db.query(PurchasingOrderSource.raw_order_file_id, PurchasingOrderSource.client_order_id).filter(
            PurchasingOrderSource.purchasing_list_id == mpl.id).limit(10001).all()
        if len(sources) > 10000:
            raise WorkLimit("This retained purchase list exceeds its source budget; arrange a controlled review")
        raw_ids = [file_id for file_id, _ in sources if file_id]
        order_ids = [order_id for _, order_id in sources if order_id]
        if mpl.raw_order_file_id:
            raw_ids.append(mpl.raw_order_file_id)
        if not raw_ids and not order_ids:
            return {}
        historical = mpl.status not in {OrderStatus.PURCHASING_CONSOLIDATED, OrderStatus.CANCELLED}
        return {key: packs for key, (packs, _) in self._demand(
            raw_ids, order_ids, historical=historical, pending_keys=pending_keys).items()}

    def get(self, batch_id_or_ref: str) -> MasterPurchasingList:
        return self._get(batch_id_or_ref)

    # -- internals ---------------------------------------------------------

    def _get(self, batch_id_or_ref: str) -> MasterPurchasingList:
        mpl = (
            self.db.query(MasterPurchasingList)
            .filter(
                (MasterPurchasingList.id == batch_id_or_ref)
                | (MasterPurchasingList.batch_reference == batch_id_or_ref)
            )
            .first()
        )
        if not mpl:
            raise LookupError("Procurement batch not found")
        return mpl

    @staticmethod
    def _require_editable(mpl: MasterPurchasingList) -> None:
        if mpl.status != OrderStatus.PURCHASING_CONSOLIDATED:
            raise ValueError(f"Batch is {mpl.status.value}; buffers are frozen after confirmation")

    def _require_exist(self, model, ids: list[str], label: str) -> None:
        if not ids:
            return
        found = {row[0] for row in self.db.query(model.id).filter(model.id.in_(ids)).all()}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError(f"{label} not found: {', '.join(missing)}")

    def _demand(
        self, raw_file_ids: list[str], client_order_ids: list[str], *, historical: bool = False,
        pending_keys: set[DemandKey] | None = None,
    ) -> dict[DemandKey, tuple[int, float]]:
        source = None
        if raw_file_ids:
            source = ClientOrder.raw_order_file_id.in_(raw_file_ids)
        if client_order_ids:
            by_order = ClientOrder.id.in_(client_order_ids)
            source = by_order if source is None else (source | by_order)
        eligible = ~ClientOrder.status.in_({OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE})
        if historical:
            # Keep demand frozen for buying that already happened. Only early
            # cancellations were removed from its unconfirmed purchase plan.
            late_cancellations = select(AuditLog.target_id).where(
                AuditLog.target_table == "client_orders", AuditLog.event_type == "ORDER_EXCEPTION_RESOLVED",
                ~AuditLog.before_state["status"].as_string().in_(["SPLIT_AND_VALIDATED", "PURCHASING_CONSOLIDATED"]))
            eligible = or_(ClientOrder.status != OrderStatus.CANCELLED, ClientOrder.id.in_(late_cancellations))
        rows = (
            self.db.query(
                ClientOrderItem.product_id,
                ClientOrderItem.pack_size,
                func.sum(ClientOrderItem.ordered_pack_count),
                func.sum(ClientOrderItem.estimated_net_weight_kg),
                func.max(case(((ClientOrderItem.ordered_pack_count == 0)
                               & (ClientOrderItem.estimated_net_weight_kg > 0), 1), else_=0)),
            )
            .join(ClientOrder, ClientOrderItem.client_order_id == ClientOrder.id)
            .filter(source)
            .filter(eligible)
            .group_by(ClientOrderItem.product_id, ClientOrderItem.pack_size)
            .limit(MAX_ITEMS + 1)
            .all()
        )
        if len(rows) > MAX_ITEMS:
            raise WorkLimit("Selected sources exceed 20000 distinct purchase lines; combine a smaller selection")
        if pending_keys is not None:
            pending_keys.update((p, s) for p, s, _, _, pending in rows if pending)
        return {(p, s): (int(packs or 0), round(float(kg or 0), 4)) for p, s, packs, kg, _ in rows}

    def _set_buffer(
        self, item: PurchasingItem, buffer_pct: float, base: dict[DemandKey, int], actor: User
    ) -> None:
        before = float(item.buffer_percentage or 0)
        self._apply_buffer(item, buffer_pct, base.get((item.product_id, item.pack_size), 0))
        self.db.add(
            AuditLog(
                event_type="PURCHASING_BUFFER_SET",
                actor_id=actor.id,
                actor_role=actor.role.value,
                target_table="purchasing_items",
                target_id=item.id,
                before_state={"buffer_percentage": before},
                after_state={"buffer_percentage": float(item.buffer_percentage)},
            )
        )

    @staticmethod
    def _apply_buffer(item: PurchasingItem, buffer_pct: float, ordered_packs: int) -> None:
        """Required = base x (1 + buffer); purchases await explicit buyer confirmation."""
        percentage = buffer_percentage(buffer_pct)
        ordered_kg = purchase_weight(item.ordered_net_weight_kg or 0)
        extra_kg = purchase_weight((ordered_kg * percentage / 100).quantize(WEIGHT_STEP, rounding=ROUND_HALF_UP))
        required_kg = purchase_weight(ordered_kg + extra_kg)
        required_packs = int((Decimal(ordered_packs) * (1 + percentage / 100)).to_integral_value(rounding=ROUND_CEILING))
        if not 0 <= required_packs <= INT32_MAX:
            raise ValueError("Buffered pack count exceeds the supported range")
        item.buffer_percentage = percentage
        item.buffer_weight_kg = extra_kg
        item.total_required_weight_kg = required_kg
        item.total_required_pack_count = required_packs
