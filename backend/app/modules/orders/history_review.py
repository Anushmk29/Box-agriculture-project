"""Read-only candidates for date, physical-pack and customer identity review."""

from collections import defaultdict

from app.core.dates import canonical_day
from app.core.packs import pack_key


def review_records(tables: dict[str, list[dict]]) -> dict:
    issues = []
    aliases = defaultdict(list)
    for table, rows in tables.items():
        for row in rows:
            for name in ("valid_from", "valid_to", "shipment_target_date"):
                if name not in row or row[name] is None:
                    continue
                try:
                    canonical = canonical_day(row[name])
                except ValueError:
                    issues.append({"kind": "invalid_date", "table": table, "id": row["id"], "field": name})
                else:
                    if canonical != row[name]:
                        issues.append({"kind": "noncanonical_date", "table": table, "id": row["id"],
                                       "field": name, "canonical_value": canonical})
            if "pack_size" in row:
                try:
                    key = pack_key(row["pack_size"])
                except ValueError:
                    issues.append({"kind": "invalid_pack", "table": table, "id": row["id"]})
                else:
                    scope = (table, row["product_id"], row.get("main_customer_id"),
                             row.get("sub_customer_id"), row.get("customer_id"), key)
                    aliases[scope].append(row)
            if table == "customer_order_context" and (
                row["main_customer_id"] != row["parent_customer_id"]
                or row["customer_id"] == row["main_customer_id"]
            ):
                issues.append({"kind": "customer_parent_review", "id": row["id"],
                               "customer_id": row["customer_id"]})
            if table == "invalid_main_accounts":
                issues.append({"kind": "invalid_selected_main", "id": row["id"]})
    for scope, rows in aliases.items():
        labels = sorted({row["pack_size"] for row in rows})
        if len(labels) > 1:
            issues.append({"kind": "equivalent_pack_aliases_require_dated_review", "table": scope[0],
                           "product_id": scope[1], "canonical_pack": scope[-1], "labels": labels,
                           "row_ids": [row["id"] for row in rows[:50]], "row_count": len(rows)})
    return {"issues": issues[:1000], "issues_truncated": len(issues) > 1000,
            "issue_count": len(issues), "automatic_corrections": False,
            "note": "Alias groups and parent mismatches are review candidates. Check dates, source documents and issued history before any correction."}
