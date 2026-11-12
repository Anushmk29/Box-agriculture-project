"""Order intake: every parser's CanonicalOrderDraft becomes ClientOrders through here.

Parsers (matrix, unified Excel, PDF, image/chat, ad-hoc form) are adapters that emit
drafts; this module owns customer creation, product resolution, weights, de-duplication
and the re-upload rule. It flushes; the caller commits and advances the lifecycle.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.dates import canonical_day
from app.models.master_data import Customer, Product
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.modules.orders.quantities import derive_quantity
from app.services.canonical_schema import (
    ActionType,
    CanonicalDraftItem,
    CanonicalOrderDraft,
    IngestionChannel,
)
from app.services.name_matcher import NameFirstMatcher


def pack_weight_kg(pack_size: str | None) -> float:
    """Legacy fast-track fallback only; order intake uses reviewed quantity derivation."""
    spec = (pack_size or "").lower().replace(" ", "")
    if m := re.search(r"(\d+(?:\.\d+)?)kg", spec):
        return float(m.group(1))
    if m := re.search(r"(\d+(?:\.\d+)?)g", spec):
        return float(m.group(1)) / 1000
    if spec in {"kg", "1kg"}:
        return 1.0
    return 0.15


def matrix_to_drafts(parsed: dict[str, Any], source_file: str) -> list[CanonicalOrderDraft]:
    """Adapter: MatrixOrderParser.parse_all_clients() output -> drafts (quantities are packs)."""
    def available_names(item):
        return list(dict.fromkeys(name for key in ("name_vi", "name_en", "name_th")
                                  if (name := str(item.get(key) or "").strip())))

    groups = parsed.get("clients_groups", {})
    return [
        CanonicalOrderDraft(
            source_file=source_file,
            channel=IngestionChannel.EXCEL_MATRIX,
            client_code=client_code,
            route_line=groups.get(client_code),
            items=[
                CanonicalDraftItem(
                    raw_text=" - ".join(
                        filter(
                            None,
                            [
                                it["t_code"],
                                " / ".join(available_names(it)),
                            ],
                        )
                    ),
                    product_code=it["t_code"],
                    product_name=next(iter(available_names(it)), ""),
                    alt_names=available_names(it)[1:],
                    pack_size=it["pack_size"],
                    quantity=float(it["quantity"] or 0),
                    unit=it.get("unit", "pack"),
                    quantity_issues=it.get("quantity_issues", []),
                    source=it.get("source", {}),
                )
                for it in items
            ],
        )
        for client_code, items in parsed.get("clients_data", {}).items()
    ]


@dataclass
class IntakeResult:
    orders: list[ClientOrder] = field(default_factory=list)
    lines_total: int = 0
    lines_unmatched: int = 0

    @property
    def lines_matched(self) -> int:
        return self.lines_total - self.lines_unmatched


class OrderIntake:
    def __init__(self, db: Session, *, resolver: NameFirstMatcher | None = None):
        self.db = db
        self._resolver = resolver
        self._customers: dict[str, Customer | None] = {}
        self._main_customers: dict[str, Customer] = {}

    @property
    def resolver(self) -> NameFirstMatcher:
        if self._resolver is None:
            self._resolver = NameFirstMatcher(self.db)
        return self._resolver

    def resolve(self, item: CanonicalDraftItem, main_customer_id: str | None) -> Product | None:
        available_names = [name for name in [item.product_name, *item.alt_names] if name and name.strip()]
        product = self.resolver.reviewed_product(
            available_names or [item.raw_text],
            raw_code=item.product_code,
            main_customer_id=main_customer_id,
        )
        return product

    def materialize(
        self,
        *,
        raw_file: RawOrderFile,
        drafts: list[CanonicalOrderDraft],
    ) -> IntakeResult:
        """Create or replace one ClientOrder per draft client.

        Re-upload replaces an order's lines, but only while it is still SPLIT_AND_VALIDATED.
        Unmatched lines require a verified SKU mapping before materialization.
        """
        target_day = canonical_day(raw_file.shipment_target_date)
        self.preflight_context(raw_file, drafts)
        raw_file.shipment_target_date = target_day
        result = IntakeResult()
        refs = {self._order_ref(raw_file, draft.client_code) for draft in drafts}
        existing_orders = {order.order_ref_id: order for order in self.db.query(ClientOrder).filter(
            ClientOrder.order_ref_id.in_(refs),
        ).all()}
        for draft in drafts:
            lines = self._lines(draft, raw_file.main_customer_id, result)
            if not lines:
                continue
            ref = self._order_ref(raw_file, draft.client_code)
            order = existing_orders.get(ref)
            if order:
                if order.status != OrderStatus.SPLIT_AND_VALIDATED:
                    raise ValueError(
                        f"Order {ref} is already {order.status.value} and cannot be replaced by a re-upload"
                    )
                self.db.query(ClientOrderItem).filter_by(client_order_id=order.id).delete()
                order.raw_order_file_id = raw_file.id
                order.main_customer_id = raw_file.main_customer_id
                order.total_ordered_items_count = 0
                order.total_estimated_net_weight_kg = 0
            else:
                order = self._new_order(raw_file, draft, ref, flush=False)
                existing_orders[ref] = order
            self._add_lines(order, lines)
            result.orders.append(order)
        raw_file.detected_sub_customer_count = len(result.orders)
        self.db.flush()
        return result

    def append_adhoc(self, *, raw_file: RawOrderFile, draft: CanonicalOrderDraft) -> ClientOrder:
        """Add chat/WhatsApp lines to the client's -ADHOC order for this shipment."""
        raw_file = (self.db.query(RawOrderFile).filter_by(id=raw_file.id)
                    .with_for_update().populate_existing().one())
        target_day = canonical_day(raw_file.shipment_target_date)
        self.preflight_context(raw_file, [draft])
        raw_file.shipment_target_date = target_day
        lines = self._lines(draft, raw_file.main_customer_id, IntakeResult())
        if not lines:
            raise ValueError("Ad-hoc order has no positive quantities")
        ref = self._order_ref(raw_file, draft.client_code) + "-ADHOC"
        order = self.db.query(ClientOrder).filter_by(order_ref_id=ref).first()
        if order is None:
            order = self._new_order(raw_file, draft, ref)
        elif order.status != OrderStatus.SPLIT_AND_VALIDATED:
            raise ValueError(
                f"Order {ref} is already {order.status.value}; start a new ad-hoc order"
            )
        self._add_lines(order, lines)
        self.db.flush()
        return order

    def get_or_create_customer(
        self,
        client_code: str,
        *,
        route_group: str | None,
        parent_id: str | None,
        company_name: str | None = None,
        country: str | None = None,
        is_main: bool = False,
        flush: bool = True,
    ) -> Customer:
        if parent_id is not None:
            self.require_main_customer(parent_id)
        customer = self._customer_in_context(client_code, parent_id, is_main=is_main)
        if customer is None:
            group = route_group or "GENERAL"
            customer = Customer(
                id=str(uuid.uuid4()),
                client_code=client_code,
                company_name=company_name or client_code,
                billing_email="",
                route_group=group,
                destination_country=country or "",
                is_main_customer=is_main,
                parent_customer_id=parent_id,
            )
            self.db.add(customer)
            self._customers[client_code] = customer
            if flush:
                self.db.flush()
            return customer
        if route_group and not is_main:
            customer.route_group = route_group
        return customer

    def require_main_customer(self, customer_id: str) -> Customer:
        customer = self._main_customers.get(customer_id) or self.db.get(Customer, customer_id)
        if not customer or not customer.is_main_customer:
            raise ValueError("The selected main customer is invalid")
        self._main_customers[customer_id] = customer
        return customer

    def _customer_in_context(self, code: str, parent_id: str | None, *, is_main=False):
        if code not in self._customers:
            self._customers[code] = self.db.query(Customer).filter_by(client_code=code).first()
        customer = self._customers[code]
        if customer:
            if is_main and not customer.is_main_customer:
                raise ValueError(f"Customer {code} is not a main customer; review its identity")
            if not is_main and (customer.parent_customer_id != parent_id or customer.id == parent_id):
                raise ValueError(f"Customer {code} has a conflicting main customer/parent; review its identity")
        return customer

    def preflight_context(self, raw_file: RawOrderFile, drafts: list[CanonicalOrderDraft]) -> None:
        """Check dates and customers before replacing lines or creating any orders."""
        from app.models.excel_template import ExcelTemplate
        from app.modules.orders.templates import require_template_review
        require_template_review(drafts)
        target_day = canonical_day(raw_file.shipment_target_date)
        main = self.require_main_customer(raw_file.main_customer_id) if raw_file.main_customer_id else None
        codes = {draft.client_code for draft in drafts}
        customers = self.db.query(Customer).filter(Customer.client_code.in_(codes)).all()
        self._customers = {code: None for code in codes}
        self._customers.update({customer.client_code: customer for customer in customers})
        template_ids = {d.metadata.get("template", {}).get("id") for d in drafts}
        template_ids.discard(None)
        scoped_templates = {
            (row.id, row.template_key, row.version) for row in self.db.query(ExcelTemplate).filter(
                ExcelTemplate.id.in_(template_ids),
                ExcelTemplate.account_key == (raw_file.main_customer_id or "GENERAL"),
            ).all()
        } if template_ids else set()
        for draft in drafts:
            template = draft.metadata.get("template", {})
            scoped = (template.get("id"), template.get("key"), template.get("version")) in scoped_templates
            if main and draft.main_customer_code and main.client_code.casefold() != draft.main_customer_code.casefold() and not scoped:
                raise ValueError("Document main customer conflicts with the selected main customer")
            if draft.shipment_date and canonical_day(draft.shipment_date) != target_day:
                raise ValueError("Document shipment date conflicts with the selected shipment date")
            self._customer_in_context(draft.client_code, raw_file.main_customer_id)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _order_ref(raw_file: RawOrderFile, client_code: str) -> str:
        code = client_code.replace(" ", "_")[:30]
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"{raw_file.id}/{client_code}").hex[:16]
        return f"ORD-{raw_file.shipment_target_date.replace('-', '')}-{code}-{identity}"

    def _new_order(
        self, raw_file: RawOrderFile, draft: CanonicalOrderDraft, ref: str, *, flush: bool = True
    ) -> ClientOrder:
        customer = self.get_or_create_customer(
            draft.client_code,
            route_group=draft.route_line or raw_file.route_line,
            parent_id=raw_file.main_customer_id,
            company_name=draft.client_name,
            country=draft.destination_country,
            flush=flush,
        )
        order = ClientOrder(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{raw_file.id}/{ref}")),
            raw_order_file_id=raw_file.id,
            customer=customer,
            main_customer_id=raw_file.main_customer_id,
            order_ref_id=ref,
            status=OrderStatus.SPLIT_AND_VALIDATED,
            total_ordered_items_count=0,
            total_estimated_net_weight_kg=0,
        )
        self.db.add(order)
        if flush:
            self.db.flush()
        return order

    def _lines(
        self,
        draft: CanonicalOrderDraft,
        main_customer_id: str | None,
        result: IntakeResult,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Resolve and de-duplicate lines by (product, pack size)."""
        lines: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"raw_texts": [], "packs": 0, "kg": 0, "sources": []}
        )
        unresolved = []
        for item in draft.items:
            if item.action == ActionType.CANCEL or item.quantity <= 0:
                continue
            result.lines_total += 1
            product = self.resolve(item, main_customer_id)
            if product is None:
                result.lines_unmatched += 1
                label = f"{item.product_code or ''} {item.product_name or item.raw_text}".strip()
                unresolved.append(f"'{label}'")
                continue
            try:
                quantity = derive_quantity(item, product)
            except ValueError as exc:
                raise ValueError(f"{draft.client_code} {product.t_code}: {exc}") from exc
            line = lines[(product.id, quantity.pack_size)]
            line["packs"] += quantity.packs
            line["kg"] += quantity.net_kg
            line["sources"].append(
                {
                    **item.source,
                    "raw_text": item.raw_text,
                    "amount": item.quantity,
                    "unit": item.unit,
                    "packing": item.pack_size,
                    "net_weight_kg": item.net_weight_kg,
                    "normalized_packs": quantity.packs,
                    "weight_source": quantity.weight_source,
                }
            )
            if item.raw_text not in line["raw_texts"]:
                line["raw_texts"].append(item.raw_text)
        if unresolved:
            raise ValueError(
                f"Unresolved products for {draft.client_code}: {', '.join(unresolved)}. "
                "Create a verified SKU mapping first."
            )
        return lines

    def _add_lines(self, order: ClientOrder, lines: dict[tuple[str, str], dict[str, Any]]) -> None:
        for (product_id, pack_size), line in lines.items():
            self.db.add(
                ClientOrderItem(
                    client_order_id=order.id,
                    product_id=product_id,
                    raw_produce_text="; ".join(line["raw_texts"][:2]),
                    pack_size=pack_size,
                    ordered_pack_count=line["packs"],
                    estimated_net_weight_kg=line["kg"],
                    quantity_source={"lines": line["sources"]},
                )
            )
            order.total_ordered_items_count = (order.total_ordered_items_count or 0) + line["packs"]
            from decimal import Decimal

            order.total_estimated_net_weight_kg = (
                Decimal(str(order.total_estimated_net_weight_kg or 0)) + line["kg"]
            )
