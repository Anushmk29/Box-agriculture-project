"""Read-only market lists: customer demand stays separate from shared buying."""

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from io import BytesIO

from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.core.read_models import RAW_HEADERS
from app.core.work_limits import MAX_DEMANDS, MAX_ITEMS, MAX_PAGES, MAX_TEXT_BYTES, WorkLimit
from app.models.audit import AuditLog
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingItem


def market_list(db, batch_ids):
    ids = list(dict.fromkeys(batch_ids))
    batches = db.query(MasterPurchasingList).filter(MasterPurchasingList.id.in_(ids)).options(
        selectinload(MasterPurchasingList.sources)).all()
    if len(batches) != len(ids):
        raise LookupError("A selected purchase no longer exists. Reload the purchase list.")
    if any(batch.status == OrderStatus.CANCELLED for batch in batches):
        raise ValueError("Remove cancelled purchases from the selection.")
    source_count = sum(len(batch.sources) for batch in batches)
    if source_count > MAX_DEMANDS:
        raise WorkLimit("Select fewer purchases; this market list exceeds its source limit.")
    scope = {}
    for batch in batches:
        raw_ids = {source.raw_order_file_id for source in batch.sources if source.raw_order_file_id}
        order_ids = {source.client_order_id for source in batch.sources if source.client_order_id}
        if not raw_ids and not order_ids and batch.raw_order_file_id:
            raw_ids.add(batch.raw_order_file_id)
        scope[batch.id] = (raw_ids, order_ids)
    raw_ids = set().union(*(raw for raw, _ in scope.values()))
    order_ids = set().union(*(orders for _, orders in scope.values()))
    source_filter = or_(ClientOrder.raw_order_file_id.in_(raw_ids), ClientOrder.id.in_(order_ids))
    line_count = db.query(ClientOrderItem).join(ClientOrder).filter(source_filter).count()
    purchase_count = db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id.in_(ids)).count()
    if line_count + purchase_count > MAX_ITEMS:
        raise WorkLimit("Select fewer purchases; this market list exceeds its line limit.")
    orders = db.query(ClientOrder).filter(source_filter).options(
        selectinload(ClientOrder.items), joinedload(ClientOrder.customer),
        joinedload(ClientOrder.main_customer),
        joinedload(ClientOrder.raw_file).load_only(*RAW_HEADERS),
        joinedload(ClientOrder.raw_file).joinedload(RawOrderFile.main_customer),
    ).limit(MAX_DEMANDS + 1).all()
    if len(orders) > MAX_DEMANDS:
        raise WorkLimit("Select fewer purchases; this market list exceeds its order limit.")
    late_cancelled = set(db.scalars(select(AuditLog.target_id).where(
        AuditLog.target_table == "client_orders", AuditLog.event_type == "ORDER_EXCEPTION_RESOLVED",
        AuditLog.target_id.in_([order.id for order in orders]),
        ~AuditLog.before_state["status"].as_string().in_(
            ["SPLIT_AND_VALIDATED", "PURCHASING_CONSOLIDATED"]))))
    items = db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id.in_(ids)).options(
        joinedload(PurchasingItem.product), joinedload(PurchasingItem.replacement_product)).all()
    by_batch = defaultdict(list)
    for item in items:
        by_batch[item.purchasing_list_id].append(item)
    sections = {}
    shared = []
    seen_orders = set()
    total_ordered = total_buffer = total_target = Decimal(0)
    for batch in sorted(batches, key=lambda batch: batch.batch_reference):
        raw_ids, order_ids = scope[batch.id]
        historical = batch.status != OrderStatus.PURCHASING_CONSOLIDATED
        selected = [order for order in orders
                    if (order.raw_order_file_id in raw_ids or order.id in order_ids)
                    and (order.status != OrderStatus.CANCELLED or historical and order.id in late_cancelled)
                    and (historical or order.status != OrderStatus.UNAVAILABLE)]
        selected_ids = {order.id for order in selected}
        if selected_ids & seen_orders:
            raise ValueError("Selected purchases contain overlapping customer orders. Select each source once.")
        seen_orders.update(selected_ids)
        demand = defaultdict(dict)
        for order in selected:
            customer = order.main_customer or order.raw_file.main_customer or order.customer
            if not customer:
                raise ValueError("A source order has no customer. Review it before downloading.")
            section = sections.setdefault(customer.id, {
                "customer_id": customer.id, "customer_code": customer.client_code,
                "customer_name": customer.company_name, "lines": [],
            })
            for line in order.items:
                key = (line.product_id, line.pack_size)
                entry = demand[key].setdefault(customer.id, {
                    "section": section, "packs": 0, "kg": Decimal(0), "pending": False,
                    "files": set(), "dates": set(),
                })
                entry["packs"] += line.ordered_pack_count
                entry["kg"] += line.estimated_net_weight_kg
                entry["pending"] |= line.ordered_pack_count == 0 and line.estimated_net_weight_kg > 0
                entry["files"].add(order.raw_file.file_name)
                entry["dates"].add(order.raw_file.shipment_target_date)
        if not demand or set(demand) != {(item.product_id, item.pack_size) for item in by_batch[batch.id]}:
            raise ValueError("Purchase sources no longer match saved demand. Review the purchase before downloading.")
        for item in by_batch[batch.id]:
            entries = demand[(item.product_id, item.pack_size)]
            base_kg = sum((entry["kg"] for entry in entries.values()), Decimal(0))
            base_packs = sum(entry["packs"] for entry in entries.values())
            if base_kg != item.ordered_net_weight_kg:
                raise ValueError("Customer weights no longer match the saved purchase. Review its sources.")
            product = item.product
            if not product:
                raise ValueError("A purchase product is missing. Review the purchase before downloading.")
            common = {
                "batch_reference": batch.batch_reference, "status": batch.status.value,
                "product_code": product.t_code,
                "product_name": " / ".join(dict.fromkeys(filter(None,
                    [product.name_en, product.name_th, product.name_vi]))),
                "pack_size": item.pack_size,
            }
            target = item.total_required_weight_kg
            buffer = target - item.ordered_net_weight_kg
            if buffer < 0 or item.total_required_pack_count < base_packs:
                raise ValueError("Saved purchase targets are below customer demand. Review the purchase.")
            single_customer = len(entries) == 1
            for entry in entries.values():
                entry["section"]["lines"].append({**common,
                    "source_files": sorted(entry["files"]), "shipment_dates": sorted(entry["dates"]),
                    "ordered_packs": entry["packs"], "pack_count_pending": entry["pending"],
                    "ordered_kg": float(entry["kg"]),
                    "buffer_kg": float(buffer) if single_customer else 0,
                    "target_packs": item.total_required_pack_count if single_customer else entry["packs"],
                    "target_kg": float(target if single_customer else entry["kg"]),
                    "actual_packs": item.confirmed_purchased_pack_count if single_customer else None,
                    "actual_kg": float(item.confirmed_purchased_weight_kg)
                        if single_customer and item.confirmed_purchased_weight_kg is not None else None,
                    "notes": _notes(item) if single_customer else "Shared buffer and actuals listed separately.",
                })
            extra_packs = item.total_required_pack_count - base_packs
            if not single_customer and (buffer or extra_packs or item.confirmed_purchased_weight_kg is not None or _notes(item)):
                shared.append({**common, "source_files": [], "shipment_dates": [batch.shipment_date] if batch.shipment_date else [],
                    "ordered_packs": 0, "pack_count_pending": any(entry["pending"] for entry in entries.values()),
                    "ordered_kg": 0, "buffer_kg": float(buffer), "target_packs": extra_packs,
                    "target_kg": float(buffer), "actual_packs": item.confirmed_purchased_pack_count,
                    "actual_kg": float(item.confirmed_purchased_weight_kg)
                        if item.confirmed_purchased_weight_kg is not None else None,
                    "notes": "Batch actuals cover all customers; buffer is counted once. " + _notes(item),
                })
            total_ordered += item.ordered_net_weight_kg
            total_buffer += buffer
            total_target += target
    result = sorted(sections.values(), key=lambda section: (section["customer_code"], section["customer_id"]))
    if shared:
        result.append({"customer_id": None, "customer_code": "Shared", "customer_name": "Shared buffers and purchase results", "lines": shared})
    for section in result:
        section["lines"].sort(key=lambda line: (line["batch_reference"], line["product_code"], line["pack_size"]))
        section["total_target_kg"] = round(sum(line["target_kg"] for line in section["lines"]), 4)
    report = {"generated_at": datetime.now(UTC).isoformat(), "batch_ids": ids,
        "batch_references": sorted(batch.batch_reference for batch in batches), "sections": result,
        "total_ordered_kg": float(total_ordered), "total_buffer_kg": float(total_buffer),
        "total_target_kg": float(total_target)}
    if len(str(report).encode("utf-8")) > MAX_TEXT_BYTES:
        raise WorkLimit("Select fewer purchases; this market list exceeds its text limit.")
    return report


def _notes(item):
    substitute = item.replacement_product
    return " ".join(filter(None, [item.shortage_reason,
        f"Substitute: {substitute.t_code} {substitute.name_en}" if substitute else None]))


def market_pdf(report):
    import pymupdf

    output = BytesIO()
    writer = pymupdf.DocumentWriter(output)
    page_sections = []
    css = "body {font-family: sans-serif; font-size: 9pt;} table {border-collapse: collapse; width:100%;} td {padding:7px 4px; border-bottom:1px solid #ccd8cf; vertical-align:top;} p {margin:3px 0;} small {font-size:8pt; color:#44584b;}"
    try:
        for section in report["sections"]:
            rows = []
            for line in section["lines"]:
                packs = "Pending" if line["pack_count_pending"] else str(line["ordered_packs"])
                target_packs = "Pending" if line["pack_count_pending"] else str(line["target_packs"])
                actual = "Not confirmed" if line["actual_kg"] is None else f'{line["actual_packs"]} packs / {_kg(line["actual_kg"])} kg'
                status = "Draft - awaiting purchases" if line["status"] == "PURCHASING_CONSOLIDATED" else "Purchases confirmed"
                cells = [
                    f'{escape(line["product_name"])}<br><small>{escape(line["product_code"])} / {escape(line["pack_size"])}<br>{escape(", ".join(line["source_files"]))}<br>{escape(", ".join(line["shipment_dates"]))}<br>{status}</small>',
                    f'{packs} packs<br>{_kg(line["ordered_kg"])} kg',
                    f'{_kg(line["buffer_kg"])} kg',
                    f'{target_packs} packs<br><b>{_kg(line["target_kg"])} kg</b>',
                    actual + '<br><br>[ ] Bought', escape(line["notes"]) + '<br><br>Notes: __________',
                ]
                rows.append("<tr>" + "".join(f'<td style="width:{width}%">{cell}</td>'
                    for width, cell in zip([33, 12, 10, 13, 14, 18], cells, strict=True)) + "</tr>")
            story = pymupdf.Story("<table>" + "".join(rows) + "</table>", user_css=css)
            more = True
            while more:
                if len(page_sections) >= MAX_PAGES:
                    raise WorkLimit("This market list exceeds 200 pages. Select fewer purchases.")
                device = writer.begin_page(pymupdf.paper_rect("a4"))
                more, filled = story.place(pymupdf.Rect(30, 177, 565, 780))
                if pymupdf.Rect(filled).height < 1:
                    raise ValueError("A purchase line is too long for the PDF. Review its notes or source names.")
                story.draw(device)
                writer.end_page()
                page_sections.append(section)
    finally:
        writer.close()
    with pymupdf.open(stream=output.getvalue(), filetype="pdf") as document:
        for index, (page, section) in enumerate(zip(document, page_sections, strict=True)):
            name = section["customer_name"]
            if name != section["customer_code"]:
                name += " - " + section["customer_code"]
            header = f'<h2>Master Purchase List</h2><p>Generated (UTC): {escape(report["generated_at"][:19])}</p><h3>{escape(name)}</h3><p>Section to buy: {_kg(section["total_target_kg"])} kg | Full list: {_kg(report["total_target_kg"])} kg</p><small>Full list: ordered {_kg(report["total_ordered_kg"])} kg + buffer {_kg(report["total_buffer_kg"])} kg.<br>Unconfirmed purchases are draft. Quantities use saved data. Shared amounts appear once.</small>'
            page.insert_htmlbox(pymupdf.Rect(30, 20, 565, 146), header, css=css)
            headings = '<table><tr>' + ''.join(f'<td style="width:{width}%"><b>{label}</b></td>'
                for width, label in zip([33, 12, 10, 13, 14, 18],
                    ["Product / source", "Ordered", "Buffer", "To buy", "Actual", "Market notes"], strict=True)) + '</tr></table>'
            page.insert_htmlbox(pymupdf.Rect(30, 146, 565, 177), headings, css=css)
            page.insert_text((30, 812), f"Page {index + 1} of {len(document)} | Purchase quantities are not confirmed by printing.", fontsize=8)
        return document.tobytes(garbage=4, deflate=True)


def _kg(value):
    return f"{value:.4f}".rstrip("0").rstrip(".")
