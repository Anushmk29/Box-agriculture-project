"""Opt-in browser fixture API. Disposable SQLite only; never import in production.

Start from backend: .venv/Scripts/python.exe -m uvicorn
fixtures.resilience_browser_api:app --app-dir tests --host 127.0.0.1 --port 8106
"""

# ruff: noqa: E402 -- dummy configuration must be set before importing application modules

import os
from pathlib import Path
from uuid import uuid4

scratch = Path(__file__).resolve().parents[3] / ".codex-artifact" / ("resilience-browser-" + uuid4().hex)
scratch.mkdir(parents=True)
os.environ.update(DATABASE_URL="sqlite:///" + (scratch / "disposable.sqlite").as_posix(),
                  SUPABASE_DATABASE_URL="sqlite:///" + (scratch / "disposable.sqlite").as_posix(),
                  ENVIRONMENT="test", SECRET_KEY="browser-qa-only-secret-not-a-production-credential",
                  PACKING_PROFILES_ENABLED="false", EXCEL_TEMPLATE_PROFILES_ENABLED="true", CORS_ORIGINS='["http://127.0.0.1:3106", "http://127.0.0.1:3000"]',
                  FILE_STORAGE_PROVIDER="local", LOCAL_STORAGE_ROOT=str(scratch / "uploads"), LEGACY_UPLOAD_ROOT=str(scratch),
                  INVOICE_BANK_NAME="", INVOICE_BANK_ACCOUNT_NAME="", INVOICE_BANK_SWIFT="")

from openpyxl import Workbook
from sqlalchemy import create_engine
from test_invoicing_invariant import build_order_with_box
from test_packing_lifecycle import setup_order

from app.core.database import Base, SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import Customer, OrderStatus, PriceList, RawOrderFile, User, UserRole
from app.models.excel_template import ExcelTemplate
from app.modules.orders.acknowledgment import prepare_acknowledgment
from app.services.canonical_schema import CanonicalDraftItem, CanonicalOrderDraft, IngestionChannel

# Production engine options target PostgreSQL. Bind this opt-in fixture explicitly
# to SQLite so browser tests cannot connect to the configured hosted database.
engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
SessionLocal.configure(bind=engine)
Base.metadata.create_all(engine)
with SessionLocal() as db:
    finance_order = build_order_with_box(db, locked=True, suffix="FIN")
    _, packer, owner, packing_order = setup_order(db)
    owner.role = UserRole.OWNER_ADMIN
    owner.email = "owner@browser-qa.invalid"
    owner.password_hash = hash_password("Browser-QA-Only-2026!")
    packer.email = "floor@browser-qa.invalid"
    packer.password_hash = hash_password("Browser-QA-Only-2026!")
    product = packing_order.items[0].product
    product.default_unit_volume_cm3 = 1000
    db.add(PriceList(product_id=product.id, pack_size="1kg", unit_price_eur=2.5, valid_from="2026-01-01"))
    main_customer_id = finance_order.main_customer_id
    codes = ["CT-QA-A", "CT-QA-B", "CT-QA-UPLOAD"]
    for code in codes:
        db.add(Customer(client_code=code, company_name=code, billing_email=f"{code.lower()}@browser-qa.invalid",
                        route_group="CT", parent_customer_id=finance_order.main_customer_id,
                        tax_vat_id="QA-TAX-001", destination_country="France"))
    db.add(ExcelTemplate(account_key=main_customer_id, template_key="QA-JOURNEY", definition={
        "key": "QA-JOURNEY", "sample": "QA-JOURNEY.xlsx", "main_customer_code": "TONIO",
        "client_code": "CT-QA-UPLOAD", "kind": "table", "identity_tokens": ["QA-JOURNEY"],
        "default_unit": "pack", "numeric_pack_unit": None,
        "sheets": [{"reference_sheet": "QA", "anchors": {"1": "QA product code", "2": "QA product name"},
                    "fields": {"code": 1, "name": 2, "pack": 3, "quantity": 4, "unit": 5}, "customers": None}],
    }))
    drafts = [CanonicalOrderDraft(source_file="QA-pending.xlsx", channel=IngestionChannel.EXCEL_MATRIX,
        client_code=code, client_name=code, route_line="CT", items=[CanonicalDraftItem(raw_text="Herb",
        product_code="T001", product_name="Herb", pack_size="1kg", quantity=2, unit="pack")]) for code in codes[:2]]
    raw = RawOrderFile(file_name="QA-pending.xlsx", storage_url=str(scratch / "unused.xlsx"),
        shipment_target_date="2026-10-26", route_line="CT", main_customer_id=finance_order.main_customer_id,
        uploaded_by=owner.id, status=OrderStatus.UPLOADED,
        acknowledgment_summary={"drafts": [draft.model_dump(mode="json") for draft in drafts]})
    db.add(raw)
    db.flush()
    prepare_acknowledgment(raw, drafts)
    db.commit()
    raw_id = raw.id
    owner_id = owner.id

workbook = Workbook()
sheet = workbook.active
sheet.title = "QA"
sheet.append(["QA-JOURNEY"])
sheet.append(["QA product code", "QA product name", "Packing", "Amount", "Unit"])
sheet.append(["T001", "Herb", "1kg", 2, "pack"])
upload_path = scratch / "QA-JOURNEY.xlsx"
workbook.save(upload_path)
pending = Workbook()
pending.active.append(["Code", "VIET DESCRIPTION", "ENGLISH DESCRIPTION", "Packing", "CT-QA-A", "CT-QA-B"])
pending.active.append(["T001", "Herb", "Herb", "1kg", 2, 2])
pending_path = scratch / "QA-pending.xlsx"
pending.save(pending_path)
with SessionLocal() as db:
    db.get(RawOrderFile, raw_id).storage_url = str(pending_path)
    db.commit()


@app.get("/qa-fixtures")
def fixtures():
    return {"raw_file_id": raw_id, "disposable_database": True,
            "main_customer_id": main_customer_id, "upload_path": str(upload_path)}


@app.post("/qa-fixtures/missing-customer-details")
def missing_customer_details():
    from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService

    with SessionLocal() as db:
        order = build_order_with_box(db, locked=True, suffix="CONTACT-" + uuid4().hex[:8],
                                     seller=db.get(Customer, main_customer_id))
        invoice = OnBehalfInvoicingService(db).generate_sub_customer_invoice(order.id, actor=db.get(User, owner_id))
        order.customer.billing_email = order.customer.destination_country = ""
        db.commit()
        return {"invoice_id": invoice.id, "customer_code": order.customer.client_code, "disposable_database": True}
