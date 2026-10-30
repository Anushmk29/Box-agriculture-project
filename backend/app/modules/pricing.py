"""Price catalogue: dated selling prices, written and read in one place.

Lookup order for an invoice line: main customer's price for that sub-customer,
then the main customer's general price, then the PriceList (customer, then global).
New prices remain effective until explicitly replaced by human admin/finance.
Historical effective intervals remain readable; new expiry dates are forbidden.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.dates import canonical_day
from app.core.packs import pack_key
from app.core.policy import Action, require
from app.core.values import positive_money, required_text
from app.models.audit import AuditLog
from app.models.master_data import Customer, CustomerSellingPrice, PriceList, Product
from app.models.user import User


class PriceCatalogueReviewRequired(ValueError):
    """Stored catalogue ambiguity must be resolved before a price can be used."""


def _money(value) -> Decimal:
    try:
        return positive_money(value)
    except ValueError as exc:
        raise PriceCatalogueReviewRequired("Stored selling price is invalid; review the catalogue") from exc


def _day_before(iso_day: str) -> str:
    return (date.fromisoformat(iso_day) - timedelta(days=1)).isoformat()


def _pack_size_candidates(pack_size: str) -> list[str]:
    canonical = _price_pack_key(pack_size)
    match = re.fullmatch(r"(~?\d+(?:\.\d+)?g)\s+/?\s*(bag|tray|pack|pkt|box|carton|bunch)", canonical)
    # Preserve the existing generic-weight fallback, but never fall across an
    # explicitly priced bag/tray boundary. Exact packaging always wins.
    return [canonical, match[1]] if match else [canonical]


def _price_pack_key(value: str) -> str:
    wrapped = re.fullmatch(r"\s*(?:kg|g)\s*\(([^)]+)\)\s*", value, re.IGNORECASE)
    return pack_key(wrapped[1] if wrapped else value)


def _select_price(rows, pack_size, price_attribute):
    for candidate in _pack_size_candidates(pack_size):
        matches = [row for row in rows if _price_pack_key(row.pack_size) == candidate]
        if not matches:
            continue
        # Existing histories can contain several rows of the same label. Select
        # its latest date, then detect contradictory equivalent legacy labels.
        by_label = {}
        for row in matches:
            current = by_label.get(row.pack_size)
            if current is None or canonical_day(row.valid_from) > canonical_day(current.valid_from):
                by_label[row.pack_size] = row
            elif canonical_day(row.valid_from) == canonical_day(current.valid_from) and (
                _money(getattr(row, price_attribute)) != _money(getattr(current, price_attribute))
            ):
                raise PriceCatalogueReviewRequired("Conflicting prices for equivalent pack sizes; review the catalogue")
        if len({_money(getattr(row, price_attribute)) for row in by_label.values()}) > 1:
            raise PriceCatalogueReviewRequired("Conflicting prices for equivalent pack sizes; review the catalogue")
        row = max(by_label.values(), key=lambda row: (canonical_day(row.valid_from), row.id))
        return _money(getattr(row, price_attribute))
    return None


class PriceCatalogue:
    def __init__(self, db: Session):
        self.db = db

    def price_for(
        self,
        *,
        main_customer_id: str | None,
        sub_customer_id: str,
        product_id: str,
        pack_size: str,
        as_of: str,
    ) -> Decimal | None:
        as_of = canonical_day(as_of)
        if main_customer_id:
            for sub in (sub_customer_id, None):
                query = self._matching_packs(self._selling_scope(main_customer_id, sub, product_id),
                                             CustomerSellingPrice, pack_size)
                rows = self._active_rows(query, CustomerSellingPrice, as_of)
                price = _select_price(rows, pack_size, "selling_price_eur")
                if price is not None:
                    return price

        for customer_id in (sub_customer_id, None):
            query = self.db.query(PriceList).filter(
                PriceList.product_id == product_id,
                PriceList.customer_id == customer_id if customer_id else PriceList.customer_id.is_(None),
            )
            query = self._matching_packs(query, PriceList, pack_size)
            price = _select_price(self._active_rows(query, PriceList, as_of), pack_size, "unit_price_eur")
            if price is not None:
                return price
        return None

    @staticmethod
    def _matching_packs(query, model, pack_size, *, fallback=True):
        # Fetch labels, not years of ORM price rows. Preserve physical aliases and
        # detect ambiguous prices rather than guessing or normalizing history.
        labels = query.with_entities(model.pack_size).order_by(None).distinct().limit(501).all()
        if len(labels) > 500:
            raise PriceCatalogueReviewRequired("Too many pack labels in this price scope; review the catalogue")
        candidates = _pack_size_candidates(pack_size) if fallback else [_price_pack_key(pack_size)]
        matching = [label for (label,) in labels if _price_pack_key(label) in candidates]
        return query.filter(model.pack_size.in_(matching))

    @staticmethod
    def _active_rows(query, model, as_of):
        compact = as_of.replace("-", "")
        rows = query.filter(
            func.replace(func.trim(model.valid_from), "-", "") <= compact,
            or_(model.valid_to.is_(None), func.replace(func.trim(model.valid_to), "-", "") >= compact),
        ).order_by(model.valid_from.desc(), model.id).limit(501).all()
        if len(rows) > 500:
            raise PriceCatalogueReviewRequired("Too many overlapping price rows; review the catalogue")
        # Validate legacy date strings too; malformed calendar dates must not win
        # a lexical comparison and silently price a shipment.
        try:
            for row in rows:
                canonical_day(row.valid_from)
                if row.valid_to is not None:
                    canonical_day(row.valid_to)
        except ValueError as exc:
            raise PriceCatalogueReviewRequired("Invalid stored price date; review the catalogue") from exc
        return rows

    def set_price(
        self,
        *,
        actor: User,
        main_customer_id: str,
        sub_customer_id: str | None,
        product_id: str,
        pack_size: str,
        price: float,
        valid_from: str,
        valid_to: str | None = None,
    ) -> CustomerSellingPrice:
        """Record a permanent approved price effective from `valid_from`.

        Same effective date replaces that row; a later date adds a new row and closes
        the open-ended row it supersedes, so invoices for earlier dates keep their price.
        """
        require(actor, Action.MANAGE_BILLING)
        if valid_to is not None:
            raise ValueError("Temporary prices and expiry dates are not supported; prices remain until admin or finance changes them")
        valid_from = canonical_day(valid_from)
        pack_size = required_text(_price_pack_key(pack_size), limit=64)
        price = positive_money(price)
        if not self.db.query(Customer).filter_by(id=main_customer_id, is_main_customer=True).with_for_update().populate_existing().first():
            raise LookupError("Main customer not found")
        if not self.db.get(Product, product_id):
            raise LookupError("Product not found")
        if sub_customer_id and not (
            self.db.query(Customer)
            .filter_by(id=sub_customer_id, parent_customer_id=main_customer_id)
            .first()
        ):
            raise ValueError("Sub-customer does not belong to the selected main customer")

        model = CustomerSellingPrice
        scope = self._matching_packs(self._selling_scope(main_customer_id, sub_customer_id, product_id),
                                     model, pack_size, fallback=False).populate_existing()
        start = func.replace(func.trim(model.valid_from), "-", "")
        end = func.replace(func.trim(model.valid_to), "-", "")
        compact = valid_from.replace("-", "")
        same_date = scope.filter(start == compact).limit(2).all()
        if len(same_date) > 1:
            raise PriceCatalogueReviewRequired("Conflicting price rows for equivalent pack sizes; review the catalogue")
        row = same_date[0] if same_date else None
        # An existing later approved change still supersedes a backdated entry.
        # This is a historical boundary derived from that change, not an expiry.
        later = scope.filter(start > compact).order_by(start, model.id).first()
        valid_to = _day_before(canonical_day(later.valid_from)) if later else None
        before = None
        if row:
            before = {"selling_price_eur": float(row.selling_price_eur), "valid_to": row.valid_to}
            row.pack_size = pack_size
            row.valid_from = valid_from
            row.selling_price_eur = price
            row.valid_to = valid_to
        else:
            rows = scope.filter(start < compact, or_(model.valid_to.is_(None), end >= compact)).limit(501).all()
            if len(rows) > 500:
                raise PriceCatalogueReviewRequired("Too many overlapping price rows; review the catalogue")
            for superseded in rows:
                if canonical_day(superseded.valid_from) < valid_from and (
                    superseded.valid_to is None or canonical_day(superseded.valid_to) >= valid_from
                ):
                    superseded.valid_to = _day_before(valid_from)
            row = CustomerSellingPrice(
                main_customer_id=main_customer_id,
                sub_customer_id=sub_customer_id,
                product_id=product_id,
                pack_size=pack_size,
                selling_price_eur=price,
                valid_from=valid_from,
                valid_to=valid_to,
            )
            self.db.add(row)
        self.db.flush()
        self.db.add(
            AuditLog(
                event_type="SELLING_PRICE_SET",
                actor_id=actor.id,
                actor_role=actor.role.value,
                target_table="customer_selling_prices",
                target_id=row.id,
                before_state=before,
                after_state={
                    "main_customer_id": main_customer_id,
                    "sub_customer_id": sub_customer_id,
                    "product_id": product_id,
                    "pack_size": pack_size,
                    "selling_price_eur": float(price),
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                },
            )
        )
        self.db.flush()
        return row

    def _selling_scope(self, main_id, sub_id, product_id):
        sub_filter = (
            CustomerSellingPrice.sub_customer_id.is_(None)
            if sub_id is None
            else CustomerSellingPrice.sub_customer_id == sub_id
        )
        return self.db.query(CustomerSellingPrice).filter(
            CustomerSellingPrice.main_customer_id == main_id,
            sub_filter,
            CustomerSellingPrice.product_id == product_id,
        )
