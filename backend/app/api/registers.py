"""Summary-only registers. Detail and write APIs retain their existing contracts."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.policy import Action, permissions
from app.core.read_models import (
    RAW_HEADERS,
    bounded_headers,
    bounded_raw,
    purchase_file_links,
    raw_headers,
    scope_file_names,
)
from app.core.registers import Page, RegisterQuery
from app.models.audit import AuditLog
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.master_data import Customer
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.packing import Box
from app.models.procurement import MasterPurchasingList, PurchasingItem, PurchasingOrderSource
from app.models.user import User

router = APIRouter(tags=["Operational registers"])
PROCUREMENT_PENDING = {OrderStatus.PURCHASING_CONSOLIDATED, OrderStatus.PROCUREMENT_CONFIRMED}
PACKING_PENDING = {OrderStatus.HANDOFF_01_SENT, OrderStatus.PACKING_DESIGNED,
                   OrderStatus.FLOOR_PACKING, OrderStatus.FINAL_PACK_LOCKED}
BILLING_STATUSES = {OrderStatus.HANDOFF_02_RETURNED, OrderStatus.INVOICES_DRAFTED,
                    OrderStatus.INVOICES_SENT, OrderStatus.CONFIRMED_PAID,
                    OrderStatus.RECEIPTS_ISSUED, OrderStatus.ORDER_CLOSED}


def status_view(query, params, column, pending):
    if params.status and params.status not in column.type.enums:
        raise HTTPException(422, "Unsupported status")
    if params.view == "pending":
        query = query.filter(column.in_(pending))
    elif params.view == "completed":
        query = query.filter(~column.in_(pending))
    if params.status:
        query = query.filter(column == params.status)
    return query


@router.get("/orders/files", response_model=Page[dict], dependencies=[Depends(require_action(Action.VALIDATE_ORDERS))])
def files(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function")):
    query = db.query(RawOrderFile, RawOrderFile.acknowledgment_summary["validation"]["unresolved_product_rows"].as_integer(),
        RawOrderFile.acknowledgment_summary["validation"]["can_split"].as_boolean()).options(raw_headers()).outerjoin(Customer, Customer.id == RawOrderFile.main_customer_id)
    query = status_view(query, params, RawOrderFile.status, {OrderStatus.UPLOADED, OrderStatus.PARSED})
    query = params.search(query, [RawOrderFile.file_name, Customer.company_name, Customer.client_code,
                                  RawOrderFile.route_line, RawOrderFile.id])
    query = params.search(query, [Customer.company_name, Customer.client_code], params.customer)
    if params.route:
        query = query.filter(RawOrderFile.route_line == params.route)
    query = params.dates(query, RawOrderFile.shipment_target_date).options(selectinload(RawOrderFile.main_customer))
    rows, total = params.rows(query, {"created_at": RawOrderFile.created_at, "name": RawOrderFile.file_name,
        "customer": Customer.company_name, "shipment_date": RawOrderFile.shipment_target_date,
        "route": RawOrderFile.route_line, "status": RawOrderFile.status}, RawOrderFile.id)
    return params.page([file_summary(raw, unresolved, can_split) for raw, unresolved, can_split in rows], total)


def file_summary(raw, unresolved=0, can_split=False):
    return {"raw_file_id": raw.id, "file_name": raw.file_name,
            "customer": raw.main_customer.company_name if raw.main_customer else "General",
            "shipment_date": raw.shipment_target_date, "route": raw.route_line,
            "status": raw.status.value, "created_at": raw.created_at,
            "customer_count": raw.detected_sub_customer_count or 0,
            "unresolved_rows": unresolved or 0,
            "can_split": bool(can_split)}


@router.get("/orders/files/{file_id}", dependencies=[Depends(require_action(Action.VALIDATE_ORDERS))])
def file_detail(file_id: str, db: Session = Depends(get_db, scope="function")):
    from app.api.orders import acknowledgment_response
    raw = bounded_raw(db, file_id)
    if not raw:
        raise HTTPException(404, "Order file not found")
    return acknowledgment_response(raw)


@router.get("/orders/files/{file_id}/orders", response_model=Page[dict], dependencies=[Depends(require_action(Action.VALIDATE_ORDERS))])
def file_orders(file_id: str, params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function")):
    if not db.get(RawOrderFile, file_id):
        raise HTTPException(404, "Order file not found")
    query = order_query(db, params, set(OrderStatus), set(OrderStatus)).filter(ClientOrder.raw_order_file_id == file_id)
    rows, total = params.rows(query, ORDER_SORTS, ClientOrder.id)
    return params.page([order_summary(order) for order in rows], total)


def order_query(db, params, eligible, pending):
    query = db.query(ClientOrder).join(RawOrderFile).outerjoin(Customer, Customer.id == ClientOrder.customer_id)
    query = query.filter(ClientOrder.status.in_(eligible))
    query = status_view(query, params, ClientOrder.status, pending)
    query = params.search(query, [ClientOrder.order_ref_id, Customer.client_code, Customer.company_name, RawOrderFile.file_name])
    query = params.search(query, [Customer.client_code, Customer.company_name], params.customer)
    if params.route:
        query = query.filter(RawOrderFile.route_line == params.route)
    query = params.dates(query, RawOrderFile.shipment_target_date)
    return query.options(selectinload(ClientOrder.customer), selectinload(ClientOrder.raw_file).load_only(*RAW_HEADERS))


ORDER_SORTS = {"created_at": ClientOrder.created_at, "reference": ClientOrder.order_ref_id, "name": RawOrderFile.file_name,
               "customer": Customer.client_code, "shipment_date": RawOrderFile.shipment_target_date,
               "route": RawOrderFile.route_line, "status": ClientOrder.status}


def order_summary(order):
    return {"order_id": order.id, "order_ref_id": order.order_ref_id,
            "raw_file_id": order.raw_order_file_id, "file_name": order.raw_file.file_name,
            "customer_code": order.customer.client_code if order.customer else "General",
            "customer_name": order.customer.company_name if order.customer else "General",
            "shipment_date": order.raw_file.shipment_target_date, "route": order.raw_file.route_line,
            "status": order.status.value, "created_at": order.created_at}


@router.get("/packing/orders", response_model=Page[dict], dependencies=[Depends(require_action(Action.FLOOR_PACKING))])
def packing_orders(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function"), batch_id: str | None = None, raw_file_id: str | None = None, approval: Literal["pending"] | None = None):
    eligible = PACKING_PENDING | BILLING_STATUSES
    query = order_query(db, params, eligible, PACKING_PENDING)
    query = packing_scope(db, query, batch_id, raw_file_id)
    if approval == "pending":
        query = query.filter(ClientOrder.packing_approval_status == "PENDING", ClientOrder.status == OrderStatus.FLOOR_PACKING)
    stats = db.query(Box.client_order_id.label("order_id"), func.count(Box.id).label("boxes"),
        func.sum(case((Box.is_locked.is_(False), 1), else_=0)).label("missing")
    ).group_by(Box.client_order_id).subquery()
    query = query.outerjoin(stats, stats.c.order_id == ClientOrder.id).add_columns(stats.c.boxes, stats.c.missing)
    rows, total = params.rows(query, ORDER_SORTS | {"boxes": stats.c.boxes, "outstanding": stats.c.missing}, ClientOrder.id)
    return params.page([{**order_summary(order), "boxes": boxes or 0, "outstanding": missing or 0,
                         "weight_kg": float(order.total_estimated_net_weight_kg or 0)} for order, boxes, missing in rows], total,
                       scope_label=scope_file_names(db, raw_file_id, batch_id))


def packing_scope(db, query, batch_id=None, raw_file_id=None):
    if raw_file_id:
        if not db.get(RawOrderFile, raw_file_id):
            raise HTTPException(404, "Order file no longer exists. Choose another packing file.")
        query = query.filter(ClientOrder.raw_order_file_id == raw_file_id)
    if batch_id:
        batch = db.get(MasterPurchasingList, batch_id)
        if not batch:
            raise HTTPException(404, "Purchase batch no longer exists. Return to Purchasing to find the current batch.")
        source_files = select(PurchasingOrderSource.raw_order_file_id).where(
            PurchasingOrderSource.purchasing_list_id == batch.id,
            PurchasingOrderSource.raw_order_file_id.isnot(None))
        source_orders = select(PurchasingOrderSource.client_order_id).where(
            PurchasingOrderSource.purchasing_list_id == batch.id,
            PurchasingOrderSource.client_order_id.isnot(None))
        query = query.filter(or_(ClientOrder.raw_order_file_id.in_(source_files),
                                 ClientOrder.id.in_(source_orders),
                                 ClientOrder.raw_order_file_id == batch.raw_order_file_id
                                 if batch.raw_order_file_id else False))
    return query


@router.get("/packing/files", response_model=Page[dict], dependencies=[Depends(require_action(Action.FLOOR_PACKING))])
def packing_files(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function"),
                  batch_id: str | None = None, raw_file_id: str | None = None):
    eligible = packing_scope(db, db.query(ClientOrder).filter(
        ClientOrder.status.in_(PACKING_PENDING | BILLING_STATUSES)), batch_id, raw_file_id).with_entities(
            ClientOrder.id.label("order_id"), ClientOrder.raw_order_file_id.label("file_id"),
            ClientOrder.customer_id, ClientOrder.order_ref_id, ClientOrder.status).subquery()
    stats = db.query(eligible.c.file_id, func.count().label("orders"),
        func.sum(case((eligible.c.status.in_(PACKING_PENDING), 1), else_=0)).label("pending"),
        func.sum(case((eligible.c.status.in_({OrderStatus.PACKING_DESIGNED, OrderStatus.FLOOR_PACKING,
                                            OrderStatus.FINAL_PACK_LOCKED}), 1), else_=0)).label("started")
    ).group_by(eligible.c.file_id).subquery()
    query = db.query(RawOrderFile, stats.c.orders, stats.c.pending, stats.c.started).join(
        stats, stats.c.file_id == RawOrderFile.id).outerjoin(Customer, Customer.id == RawOrderFile.main_customer_id).options(
            raw_headers(), selectinload(RawOrderFile.main_customer))
    if params.view == "pending":
        query = query.filter(stats.c.pending > 0)
    elif params.view == "completed":
        query = query.filter(stats.c.pending == 0)
    children = db.query(eligible.c.file_id).outerjoin(Customer, Customer.id == eligible.c.customer_id)
    if params.q:
        matching_files = params.search(db.query(RawOrderFile.id), [RawOrderFile.file_name])
        matching_children = params.search(children, [Customer.company_name, Customer.client_code, eligible.c.order_ref_id])
        query = query.filter(or_(RawOrderFile.id.in_(matching_files.statement), RawOrderFile.id.in_(matching_children.statement)))
    if params.customer:
        matching_children = params.search(children, [Customer.company_name, Customer.client_code], params.customer)
        matching_clients = params.search(db.query(Customer.id), [Customer.company_name, Customer.client_code], params.customer)
        query = query.filter(or_(RawOrderFile.id.in_(matching_children.statement), RawOrderFile.main_customer_id.in_(matching_clients.statement)))
    if params.status:
        if params.status not in ClientOrder.status.type.enums:
            raise HTTPException(422, "Unsupported status")
        query = query.filter(RawOrderFile.id.in_(select(eligible.c.file_id).where(eligible.c.status == params.status)))
    if params.route:
        query = query.filter(RawOrderFile.route_line == params.route)
    query = params.dates(query, RawOrderFile.shipment_target_date)
    rows, total = params.rows(query, {"created_at": RawOrderFile.created_at, "name": RawOrderFile.file_name,
        "customer": Customer.company_name, "shipment_date": RawOrderFile.shipment_target_date,
        "route": RawOrderFile.route_line, "orders": stats.c.orders, "outstanding": stats.c.pending}, RawOrderFile.id)
    # A short preview per file, bounded independently of the number of suborders.
    ranked = db.query(eligible.c.file_id, Customer.client_code,
        func.row_number().over(partition_by=eligible.c.file_id,
                               order_by=(Customer.client_code, eligible.c.order_id)).label("position")
    ).outerjoin(Customer, Customer.id == eligible.c.customer_id).filter(
        eligible.c.file_id.in_([raw.id for raw, *_ in rows])).subquery()
    previews = {}
    for file_id, code in db.query(ranked.c.file_id, ranked.c.client_code).filter(ranked.c.position <= 4).order_by(ranked.c.file_id, ranked.c.position):
        previews.setdefault(file_id, []).append(code or "General")
    return params.page([{ "raw_file_id": raw.id, "file_name": raw.file_name,
        "customer": raw.main_customer.company_name if raw.main_customer else "General",
        "shipment_date": raw.shipment_target_date, "route": raw.route_line, "created_at": raw.created_at,
        "order_count": count, "pending_count": pending, "completed_count": count - pending,
        "subcustomers": previews.get(raw.id, []),
        "status": "HANDOFF_02_RETURNED" if not pending else "FLOOR_PACKING" if started else "HANDOFF_01_SENT",
    } for raw, count, pending, started in rows], total, scope_label=scope_file_names(db, raw_file_id, batch_id))


def invoice_memberships(db):
    # Match the write services: existing direct and master invoices block duplicate generation.
    direct = select(Invoice.client_order_id.label("order_id"), Invoice.id.label("invoice_id"))
    sources = select(InvoiceLineItem.source_order_id.label("order_id"), Invoice.id.label("invoice_id")).join(
        Invoice, Invoice.id == InvoiceLineItem.invoice_id).where(InvoiceLineItem.source_order_id.isnot(None))
    return direct.union(sources).subquery()


@router.get("/invoicing/orders", response_model=Page[dict], dependencies=[Depends(require_action(Action.MANAGE_BILLING))])
def billing_orders(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function")):
    members = invoice_memberships(db)
    has_invoice = select(members.c.order_id).where(members.c.order_id == ClientOrder.id).exists()
    query = order_query(db, params, BILLING_STATUSES, BILLING_STATUSES)
    if params.view == "pending":
        query = query.filter(ClientOrder.status == OrderStatus.HANDOFF_02_RETURNED, ~has_invoice)
    elif params.view == "completed":
        # order_query's generic status view must not exclude these statuses.
        params.view = "all"
        query = order_query(db, params, BILLING_STATUSES, BILLING_STATUSES).filter(has_invoice)
    rows, total = params.rows(query.add_columns(has_invoice.label("has_invoice")), ORDER_SORTS, ClientOrder.id)
    return params.page([{**order_summary(order), "can_invoice": order.status == OrderStatus.HANDOFF_02_RETURNED and not exists} for order, exists in rows], total)


@router.get("/invoicing/order-record/{order_id}", dependencies=[Depends(require_action(Action.MANAGE_BILLING))])
def billing_order_record(order_id: str, db: Session = Depends(get_db, scope="function")):
    order = db.get(ClientOrder, order_id)
    if not order or order.status not in BILLING_STATUSES:
        raise HTTPException(404, "Billing order not found")
    memberships = invoice_memberships(db)
    invoice = db.query(Invoice).filter(Invoice.id.in_(select(memberships.c.invoice_id).where(memberships.c.order_id == order_id))).order_by(Invoice.created_at.desc(), Invoice.id).first()
    return {**order_summary(order), "order_status": order.status.value,
            "invoice_id": invoice.id if invoice else None,
            "invoice_number": invoice.invoice_number if invoice else None,
            "invoice_status": invoice.status.value if invoice else None}


@router.get("/invoicing/records", response_model=Page[dict], dependencies=[Depends(require_action(Action.MANAGE_BILLING))])
def invoice_records(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function"),
                    invoice_group: Literal["drafts", "final", "unpaid", "paid"] | None = None):
    query = db.query(Invoice).join(ClientOrder, ClientOrder.id == Invoice.client_order_id).join(RawOrderFile).outerjoin(Customer, Customer.id == Invoice.customer_id)
    if invoice_group:
        groups = {"drafts": {InvoiceStatus.INVOICE_DRAFT}, "final": {InvoiceStatus.INVOICE_SENT, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.CONFIRMED_PAID}, "unpaid": {InvoiceStatus.INVOICE_SENT, InvoiceStatus.PARTIALLY_PAID}, "paid": {InvoiceStatus.CONFIRMED_PAID}}
        query = query.filter(Invoice.status.in_(groups[invoice_group]))
    query = status_view(query, params, Invoice.status, {InvoiceStatus.INVOICE_DRAFT, InvoiceStatus.INVOICE_SENT, InvoiceStatus.PARTIALLY_PAID})
    source_query = db.query(InvoiceLineItem.invoice_id).join(ClientOrder, ClientOrder.id == InvoiceLineItem.source_order_id).join(RawOrderFile).outerjoin(Customer, Customer.id == ClientOrder.customer_id)
    for term, columns in [(params.q, [Invoice.invoice_number, Customer.company_name, Customer.client_code, ClientOrder.order_ref_id, RawOrderFile.file_name]),
                          (params.customer, [Customer.company_name, Customer.client_code])]:
        if term:
            pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            sources = params.search(source_query, [Customer.company_name, Customer.client_code, ClientOrder.order_ref_id, RawOrderFile.file_name], term)
            query = query.filter(or_(*(column.ilike(pattern, escape="\\") for column in columns),
                Invoice.seller_customer.has(or_(Customer.company_name.ilike(pattern, escape="\\"), Customer.client_code.ilike(pattern, escape="\\"))),
                Invoice.id.in_(sources.statement)))
    if params.route:
        query = query.filter(or_(RawOrderFile.route_line == params.route,
            Invoice.id.in_(source_query.filter(RawOrderFile.route_line == params.route).statement)))
    if params.date_from or params.date_to:
        dates = []
        if params.date_from:
            dates.append(RawOrderFile.shipment_target_date >= params.date_from)
        if params.date_to:
            dates.append(RawOrderFile.shipment_target_date <= params.date_to)
        query = query.filter(or_(and_(*dates), Invoice.id.in_(params.dates(source_query, RawOrderFile.shipment_target_date).statement)))
    rows, total = params.rows(query.add_columns(Customer.company_name, ClientOrder.order_ref_id,
        RawOrderFile.shipment_target_date, RawOrderFile.route_line, RawOrderFile.file_name), {
        "created_at": Invoice.created_at, "reference": Invoice.invoice_number, "customer": Customer.company_name,
        "shipment_date": RawOrderFile.shipment_target_date, "route": RawOrderFile.route_line,
        "status": Invoice.status, "amount": Invoice.total_amount_eur}, Invoice.id)
    # Fetch distinct source headers only for this page; never load invoice lines or amounts per source.
    source_query = db.query(InvoiceLineItem.invoice_id, Customer.company_name, ClientOrder.order_ref_id,
        RawOrderFile.shipment_target_date, RawOrderFile.route_line, RawOrderFile.file_name).join(ClientOrder, ClientOrder.id == InvoiceLineItem.source_order_id).join(RawOrderFile).outerjoin(Customer, Customer.id == ClientOrder.customer_id).filter(
        InvoiceLineItem.invoice_id.in_([invoice.id for invoice, *_ in rows])).distinct()
    source_headers = bounded_headers(source_query) if rows else []
    headers = {}
    for invoice_id, customer, ref, day, route, filename in source_headers:
        headers.setdefault(invoice_id, []).append((customer, ref, day, route, filename))
    items = []
    for invoice, customer, ref, day, route, filename in rows:
        sources = headers.get(invoice.id, [(customer, ref, day, route, filename)])
        clients = sorted({header[0] for header in sources if header[0]})
        days = sorted({header[2] for header in sources if header[2]})
        routes = sorted({header[3] for header in sources if header[3]})
        items.append({"invoice_id": invoice.id, "invoice_number": invoice.invoice_number,
            "revision": invoice.revision,
            "customer": ", ".join(clients[:2]) + (f" +{len(clients) - 2} clients" if len(clients) > 2 else "") or "General",
            "order_ref_id": ref if len(sources) == 1 else f"{len(sources)} orders",
            "file_name": ", ".join(sorted({header[4] for header in sources if header[4]})),
            "shipment_date": ", ".join(days), "route": ", ".join(routes),
            "status": invoice.status.value, "created_at": invoice.created_at, "currency": invoice.currency,
            "amount": float(invoice.total_amount_eur), "paid": float(invoice.amount_paid_eur or 0)})
    return params.page(items, total)


@router.get("/master-data/customer-register", response_model=Page[dict], dependencies=[Depends(require_action(Action.VALIDATE_ORDERS))])
def customers(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function")):
    query = db.query(Customer).filter(Customer.is_main_customer.is_(True))
    query = params.search(query, [Customer.company_name, Customer.client_code])
    if params.route:
        query = query.filter(Customer.route_group == params.route)
    rows, total = params.rows(query, {"created_at": Customer.client_code, "name": Customer.company_name}, Customer.id)
    return params.page([{"id": row.id, "client_code": row.client_code, "company_name": row.company_name,
                         "route_group": row.route_group, "is_main_customer": row.is_main_customer} for row in rows], total)


def procurement_batches(db, params, raw_file_id=None):
    source_files = db.query(RawOrderFile.id).outerjoin(Customer, Customer.id == RawOrderFile.main_customer_id)
    source_files = params.search(source_files, [RawOrderFile.file_name, RawOrderFile.shipment_target_date,
        RawOrderFile.route_line, Customer.company_name, Customer.client_code])
    source_files = params.search(source_files, [Customer.company_name, Customer.client_code], params.customer)
    if params.route:
        source_files = source_files.filter(RawOrderFile.route_line == params.route)
    source_files = params.dates(source_files, RawOrderFile.shipment_target_date)
    matching_ids = source_files.with_entities(RawOrderFile.id).statement
    links = purchase_file_links()
    def batches_for_files(ids):
        return MasterPurchasingList.id.in_(select(links.c.batch_id).where(links.c.file_id.in_(ids)))
    source_match = batches_for_files(matching_ids)
    metadata = db.query(links.c.batch_id, func.min(Customer.company_name).label("customer"),
        func.min(RawOrderFile.file_name).label("filename"),
        func.min(RawOrderFile.shipment_target_date).label("day"), func.min(RawOrderFile.route_line).label("route")
    ).join(RawOrderFile, RawOrderFile.id == links.c.file_id).outerjoin(Customer, Customer.id == RawOrderFile.main_customer_id).group_by(links.c.batch_id).subquery()
    query = status_view(db.query(MasterPurchasingList).outerjoin(metadata, metadata.c.batch_id == MasterPurchasingList.id), params, MasterPurchasingList.status, PROCUREMENT_PENDING)
    if raw_file_id:
        if not db.get(RawOrderFile, raw_file_id):
            raise HTTPException(404, "Order file no longer exists. Return to Orders to choose another file.")
        query = query.filter(MasterPurchasingList.id.in_(select(links.c.batch_id).where(links.c.file_id == raw_file_id)))
    if params.q:
        pattern = "%" + params.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(or_(MasterPurchasingList.batch_reference.ilike(pattern, escape="\\"),
            MasterPurchasingList.shipment_date.ilike(pattern, escape="\\"), MasterPurchasingList.route_code.ilike(pattern, escape="\\"), source_match))
    if params.customer:
        # Customer/file filters must constrain reference matches too.
        ids = db.query(RawOrderFile.id).outerjoin(Customer, Customer.id == RawOrderFile.main_customer_id)
        ids = params.search(ids, [Customer.company_name, Customer.client_code], params.customer)
        query = query.filter(batches_for_files(ids.statement))
    if params.route:
        query = query.filter(func.coalesce(MasterPurchasingList.route_code, metadata.c.route) == params.route)
    query = params.dates(query, func.coalesce(MasterPurchasingList.shipment_date, metadata.c.day))
    missing = db.query(PurchasingItem.purchasing_list_id.label("batch_id"), func.sum(case((or_(
        PurchasingItem.confirmed_purchased_pack_count.is_(None), PurchasingItem.confirmed_purchased_weight_kg.is_(None)), 1), else_=0)).label("outstanding")
    ).group_by(PurchasingItem.purchasing_list_id).subquery()
    query = query.outerjoin(missing, missing.c.batch_id == MasterPurchasingList.id).add_columns(missing.c.outstanding)
    rows, total = params.rows(query, {"created_at": MasterPurchasingList.created_at,
        "reference": MasterPurchasingList.batch_reference, "name": metadata.c.filename, "customer": metadata.c.customer,
        "shipment_date": func.coalesce(MasterPurchasingList.shipment_date, metadata.c.day),
        "route": func.coalesce(MasterPurchasingList.route_code, metadata.c.route), "status": MasterPurchasingList.status,
        "products": MasterPurchasingList.total_skus_count, "outstanding": missing.c.outstanding}, MasterPurchasingList.id)
    source_rows = bounded_headers(db.query(links.c.batch_id, RawOrderFile).join(RawOrderFile, RawOrderFile.id == links.c.file_id)
        .filter(links.c.batch_id.in_([batch.id for batch, _ in rows])).options(raw_headers(), joinedload(RawOrderFile.main_customer))) if rows else []
    files = {}
    for batch_id, raw in source_rows:
        files.setdefault(batch_id, []).append(raw)
    items = []
    for batch, outstanding in rows:
        sources = files.get(batch.id, [])
        customers = sorted({raw.main_customer.company_name for raw in sources if raw.main_customer})
        day = batch.shipment_date or ", ".join(sorted({raw.shipment_target_date for raw in sources}))
        route = batch.route_code or ", ".join(sorted({raw.route_line for raw in sources}))
        from datetime import date
        try:
            display_day = date.fromisoformat(day).strftime("%d %b %Y")
        except (ValueError, TypeError):
            display_day = day or "No shipment date"
        label = ", ".join(customers[:2]) or "General"
        if len(customers) > 2:
            label += f" +{len(customers) - 2} clients"
        items.append({"batch_id": batch.id, "batch_reference": batch.batch_reference,
            "display_name": f"{label} · {display_day} · {route or 'No route'}", "status": batch.status.value,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "shipment_date": day, "route_code": route, "total_skus": batch.total_skus_count,
            "outstanding_items": outstanding or 0, "customers": customers, "files": sorted({raw.file_name for raw in sources}),
            "file_name": ", ".join(sorted({raw.file_name for raw in sources})),
            "next_action": "Review and confirm purchases" if batch.status == OrderStatus.PURCHASING_CONSOLIDATED else "Send purchases to packing" if batch.status == OrderStatus.PROCUREMENT_CONFIRMED else "Purchases sent to packing"})
    return params.page(items, total, scope_label=scope_file_names(db, raw_file_id))


@router.get("/admin/users/register", response_model=Page[dict], dependencies=[Depends(require_action(Action.MANAGE_USERS))])
def users(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function")):
    query = db.query(User).filter(User.is_service_principal.is_(False))
    query = params.search(query, [User.full_name, User.email])
    if params.status:
        if params.status not in {"active", "inactive"}:
            raise HTTPException(422, "Unsupported account status")
        query = query.filter(User.is_active.is_(params.status == "active"))
    if params.customer:
        if params.customer not in User.role.type.enums:
            raise HTTPException(422, "Unsupported access level")
        query = query.filter(User.role == params.customer)
    rows, total = params.rows(query, {"created_at": User.full_name, "name": User.full_name, "email": User.email}, User.id)
    return params.page([{"id": row.id, "email": row.email, "full_name": row.full_name,
        "role": row.role.value, "is_active": row.is_active, "must_change_password": row.must_change_password,
        "permissions": permissions(row)} for row in rows], total)


@router.get("/packing/scan/{qr_code}/history", response_model=Page[dict], dependencies=[Depends(require_action(Action.FLOOR_PACKING))])
def scan_history(qr_code: str, params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function")):
    box = db.query(Box).filter(Box.qr_code_uid == qr_code).first()
    if not box:
        raise HTTPException(404, "Box QR code was not found")
    query = db.query(AuditLog).filter(or_((AuditLog.target_table == "boxes") & (AuditLog.target_id == box.id),
        (AuditLog.target_table == "client_orders") & (AuditLog.target_id == box.client_order_id)))
    rows, total = params.rows(query, {"created_at": AuditLog.created_at}, AuditLog.id)
    actors = {user.id: user.full_name for user in db.query(User).filter(User.id.in_({row.actor_id for row in rows})).all()}
    return params.page([{"event_type": row.event_type, "actor_name": actors.get(row.actor_id, row.actor_role),
        "actor_role": row.actor_role, "changed_at": row.created_at, "before": row.before_state, "after": row.after_state} for row in rows], total)
