"""Order acknowledgment reports available within the platform."""

import csv
import io
import uuid

from app.core.document_limits import validate_drafts
from app.core.file_storage import source_path
from app.core.parser_process import parse_document
from app.modules.orders.intake import OrderIntake
from app.services.canonical_schema import CanonicalOrderDraft


def order_drafts(raw_file):
    saved = (raw_file.acknowledgment_summary or {}).get("drafts")
    if saved is not None:
        return validate_drafts([CanonicalOrderDraft.model_validate(draft) for draft in saved])
    with source_path(raw_file) as path:
        return [CanonicalOrderDraft.model_validate(value) for value in parse_document(str(path), mode="matrix")["drafts"]]


def prepare_acknowledgment(raw_file, drafts=None):
    drafts = drafts if drafts is not None else order_drafts(raw_file)
    customers = {draft.client_code: draft for draft in drafts}
    details = []
    groups = {}
    existing = {order.customer.client_code: order for order in raw_file.client_orders}
    for number, (code, draft) in enumerate(sorted(customers.items()), 1):
        ref = OrderIntake._order_ref(raw_file, code)
        order = existing.get(code)
        group = draft.route_line or raw_file.route_line
        groups[group] = groups.get(group, 0) + 1
        details.append({"serial_number": number, "client_code": code,
                        "customer_name": draft.client_name or code, "route_group": group,
                        "order_ref_id": order.order_ref_id if order else ref,
                        "order_id": order.id if order else str(uuid.uuid5(uuid.NAMESPACE_URL, f"{raw_file.id}/{ref}"))})
    raw_file.detected_sub_customer_count = len(details)
    summary = {key: value for key, value in (raw_file.acknowledgment_summary or {}).items() if key != "delivery"}
    raw_file.acknowledgment_summary = {**summary,
        "total_sub_customers": len(details), "sub_customer_codes": sorted(customers),
        "sub_customer_details": details, "group_summary": groups}


def acknowledgment_csv(raw_file):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Serial number", "Sub-customer name", "Customer code", "Order ID", "Order reference", "Total sub-customers"])
    summary = raw_file.acknowledgment_summary or {}
    for row in summary.get("sub_customer_details", []):
        values = [row["serial_number"], row["customer_name"], row["client_code"], row["order_id"], row["order_ref_id"], summary["total_sub_customers"]]
        writer.writerow(["'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in values])
    return output.getvalue()
