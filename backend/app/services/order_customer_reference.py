"""Reviewed billing-account references from the supplied mail documents.

Only explicit imports write accounts. Reading the order form never seeds data.
Missing country/email values stay empty rather than inventing billing details.
"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.master_data import Customer


def reference_data() -> dict:
    return json.loads((Path(__file__).resolve().parents[1] / "assets" / "order_customer_reference.json").read_text(encoding="utf-8"))


def order_routes(db: Session) -> list[str]:
    existing = db.query(Customer.route_group).filter(Customer.is_main_customer.is_(True)).distinct()
    return sorted(set(reference_data()["routes"]) | {
        route.strip().upper() for (route,) in existing if route and route.strip()
    })


def import_reference_customers(db: Session) -> list[dict]:
    """Add missing billing accounts without changing existing names or routes.

    Resolve exact account codes/names only; shop rows are never promoted to
    billing accounts. Reject ambiguous matches before making any changes.
    The caller owns commit/rollback, so preview uses the same code as apply.
    """
    customers = db.query(Customer).all()
    plan = []
    for row in reference_data()["customers"]:
        aliases = {value.casefold() for value in [row["code"], row["name"], *row["aliases"]]}
        matches = [customer for customer in customers if customer.is_main_customer and (
            customer.client_code.casefold() in aliases or customer.company_name.casefold() in aliases
        )]
        if len(matches) > 1:
            raise ValueError(f"Multiple billing accounts match {row['code']}; resolve the duplicate accounts first.")
        existing = matches[0] if matches else None
        if not existing and any(customer.client_code.casefold() == row["code"].casefold() for customer in customers):
            raise ValueError(f"{row['code']} is already used by a shop; choose a distinct billing account code.")
        plan.append((row, existing))

    result = []
    for row, existing in plan:
        if existing is None:
            existing = Customer(client_code=row["code"], company_name=row["name"],
                                route_group=row["route"], destination_country="", billing_email="",
                                is_main_customer=True)
            db.add(existing)
            action = "created"
        else:
            action = "kept"
        result.append({"action": action, "code": existing.client_code,
                       "name": existing.company_name, "route": existing.route_group})
    db.flush()
    return result
