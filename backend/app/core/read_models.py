"""Bounded read contracts; never silently truncate a historical collection."""

from fastapi import HTTPException, Query, Response
from sqlalchemy import LargeBinary, Text, cast, func, select
from sqlalchemy.orm import joinedload, load_only, selectinload, undefer

from app.core.work_limits import WorkLimit
from app.models.invoice import Invoice, InvoiceLineItem
from app.models.order import ClientOrder, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingOrderSource

MAX_DETAIL_LINES = 20000
MAX_PAGE_LINES = 5000
MAX_SOURCE_HEADERS = 10000
MAX_DETAIL_NOTE_BYTES = 8 * 1024 * 1024
RAW_HEADERS = (RawOrderFile.id, RawOrderFile.file_name, RawOrderFile.main_customer_id,
    RawOrderFile.shipment_target_date, RawOrderFile.route_line, RawOrderFile.status,
    RawOrderFile.created_at, RawOrderFile.detected_sub_customer_count)


def raw_headers():
    return load_only(*RAW_HEADERS, raiseload=True)


def purchase_file_links():
    direct = select(MasterPurchasingList.id.label("batch_id"), MasterPurchasingList.raw_order_file_id.label("file_id")).where(MasterPurchasingList.raw_order_file_id.isnot(None))
    combined = select(PurchasingOrderSource.purchasing_list_id.label("batch_id"), PurchasingOrderSource.raw_order_file_id.label("file_id")).where(PurchasingOrderSource.raw_order_file_id.isnot(None))
    explicit = select(PurchasingOrderSource.purchasing_list_id.label("batch_id"), ClientOrder.raw_order_file_id.label("file_id")).join(ClientOrder, ClientOrder.id == PurchasingOrderSource.client_order_id)
    return direct.union(combined, explicit).subquery()


def scope_file_names(db, raw_file_id=None, batch_id=None):
    if not raw_file_id and not batch_id:
        return None
    query = db.query(RawOrderFile.file_name)
    if raw_file_id:
        query = query.filter(RawOrderFile.id == raw_file_id)
    else:
        links = purchase_file_links()
        query = query.filter(RawOrderFile.id.in_(select(links.c.file_id).where(links.c.batch_id == batch_id)))
    return ", ".join(name for name, in bounded_headers(query.distinct().order_by(RawOrderFile.file_name))) or None


def stored_bytes(db, column):
    value = cast(column, Text)
    return func.length(cast(value, LargeBinary)) if db.get_bind().dialect.name == "sqlite" else func.octet_length(value)


def bounded_raw(db, identity, *, lock=False):
    query = db.query(RawOrderFile, stored_bytes(db, RawOrderFile.acknowledgment_summary)).filter(RawOrderFile.id == identity)
    result = (query.with_for_update().populate_existing() if lock else query).first()
    if not result:
        return None
    raw, length = result
    if (length or 0) > 16 * 1024 * 1024:
        raise WorkLimit("Saved review data exceeds its read budget; use the original source and arrange a controlled review migration")
    return raw


class LegacyPage:
    """Small legacy responses keep their old shape; large reads require paging."""
    def __init__(self, offset: int | None = Query(None, ge=0, le=100000),
                 limit: int | None = Query(None, ge=1, le=100)):
        self.explicit = offset is not None or limit is not None
        self.offset, self.limit = offset or 0, limit or 100

    def rows(self, query, response: Response, *, replacement: str):
        entity = query.column_descriptions[0]["entity"]
        total = query.with_entities(entity.id).order_by(None).count()
        if not self.explicit and total > self.limit:
            raise HTTPException(409, detail={"code": "PAGINATION_REQUIRED",
                "message": "This legacy collection requires explicit offset/limit. Use the paginated register for browsing.",
                "total": total, "replacement": replacement})
        rows = query.offset(self.offset).limit(self.limit).all()
        response.headers.update({"X-Total-Count": str(total), "X-Page-Offset": str(self.offset),
            "X-Page-Limit": str(self.limit), "X-Has-More": str(self.offset + len(rows) < total).lower(),
            "X-Preferred-Register": replacement})
        return rows, total


def bounded_headers(query, maximum=MAX_SOURCE_HEADERS):
    rows = query.limit(maximum + 1).all()
    if len(rows) > maximum:
        raise HTTPException(409, detail={"code": "DETAIL_PAGE_TOO_WIDE",
            "message": "This page contains too many source headers. Reduce the page size or open an individual record."})
    return rows


def invoice_read_options():
    return (undefer(Invoice.internal_notes), joinedload(Invoice.customer), joinedload(Invoice.seller_customer),
        selectinload(Invoice.items).joinedload(InvoiceLineItem.product),
        selectinload(Invoice.items).joinedload(InvoiceLineItem.source_order)
            .joinedload(ClientOrder.customer),
        selectinload(Invoice.items).joinedload(InvoiceLineItem.source_order)
            .joinedload(ClientOrder.raw_file).load_only(*RAW_HEADERS))


def load_invoice_details(db, ids, *, page=False):
    if not ids:
        return {}
    notes = db.query(func.sum(stored_bytes(db, Invoice.internal_notes))).filter(Invoice.id.in_(ids)).scalar() or 0
    if notes > MAX_DETAIL_NOTE_BYTES:
        raise HTTPException(409, detail={"code": "INVOICE_DETAIL_TOO_LARGE",
            "message": "The saved notes exceed this detail selection's byte budget; reduce the selection or arrange a controlled notes review."})
    count = db.query(func.count(InvoiceLineItem.id)).filter(InvoiceLineItem.invoice_id.in_(ids)).scalar() or 0
    if count > (MAX_PAGE_LINES if page else MAX_DETAIL_LINES):
        raise HTTPException(409, detail={"code": "INVOICE_DETAIL_TOO_LARGE",
            "message": "Use the invoice register and paginated invoice-line API for this large detail selection."})
    return {invoice.id: invoice for invoice in db.query(Invoice).filter(Invoice.id.in_(ids))
            .options(*invoice_read_options()).populate_existing().all()}
