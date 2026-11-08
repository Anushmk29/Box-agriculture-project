"""
End-to-End Pipeline Verification Script
Demonstrates the full 4-phase Order-to-Cash cycle from clean data:
  1. Intake & Order Splitting (Phase 1)
  2. Procurement Consolidation & Buyer Confirmation (Phase 2 -> Handoff 01)
  3. Final Packing & Scale Lock (Phase 3 -> Handoff 02)
  4. Invoicing, Accounting Approval, Payment in THB, and Receipt Settlement (Phase 4)
"""

import os
import sys
from decimal import Decimal

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from dotenv import load_dotenv

load_dotenv(os.path.join(backend_dir, ".env"))

from app.services.packing_override_service import PackingOverrideService
from app.services.purchasing_aggregator import PurchasingAggregatorService

from app.core.database import SessionLocal
from app.models.master_data import Customer, Product
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.user import User, UserRole
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService


def run_pipeline():
    db = SessionLocal()
    try:
        print("=" * 80)
        print("AGRICULTURE IS LIFE CO., LTD. - END-TO-END WORKFLOW TEST")
        print("=" * 80)

        # 0. Load Actor Users
        admin = db.query(User).filter_by(role=UserRole.OWNER_ADMIN).first()
        buyer = db.query(User).filter_by(role=UserRole.GENERAL).first()
        packer = db.query(User).filter_by(role=UserRole.GENERAL).first()
        accounting = db.query(User).filter_by(role=UserRole.GENERAL_ACCOUNTING).first()

        main_customer = db.query(Customer).filter_by(client_code="CHAOTHAI").first()
        sub_customer = db.query(Customer).filter_by(client_code="CHAOTHAI-JCT").first()
        product = db.query(Product).filter_by(t_code="11-001").first()  # Lemon Grass

        print(f"Actor Loaded: Admin ({admin.email}), Buyer ({buyer.email}), Accounting ({accounting.email})")
        print(f"Customer: {main_customer.company_name} -> {sub_customer.company_name}")
        print(f"Test SKU: {product.name_en} ({product.t_code})")

        # -------------------------------------------------------------
        # PHASE 1: ORDER INTAKE & SPLITTING
        # -------------------------------------------------------------
        print("\n" + "-" * 40)
        print("PHASE 1: ORDER INTAKE & VALIDATION")
        print("-" * 40)
        raw_file = RawOrderFile(
            file_name="Order_CHAOTHAI_E2E_Test.xlsx",
            storage_url="Order_CHAOTHAI_E2E_Test.xlsx",
            shipment_target_date="2026-08-29",
            route_line="JCT",
            uploaded_by=admin.id,
            main_customer_id=main_customer.id,
            detected_sub_customer_count=1,
            status=OrderStatus.PARSED,
        )
        db.add(raw_file)
        db.flush()

        order_ref = "ORD-20260829-CHAOTHAI-JCT-E2E"
        # Clean existing test if rerun
        existing_order = db.query(ClientOrder).filter_by(order_ref_id=order_ref).first()
        if existing_order:
            db.delete(existing_order)
            db.flush()

        order = ClientOrder(
            raw_order_file_id=raw_file.id,
            customer_id=sub_customer.id,
            main_customer_id=main_customer.id,
            order_ref_id=order_ref,
            status=OrderStatus.SPLIT_AND_VALIDATED,
            total_ordered_items_count=70,
            total_estimated_net_weight_kg=Decimal("70.00"),
        )
        db.add(order)
        db.flush()

        order_item = ClientOrderItem(
            client_order_id=order.id,
            product_id=product.id,
            raw_produce_text="11001 ตะไคร้ 500g 70 packs",
            pack_size="500g",
            ordered_pack_count=70,
            confirmed_pack_count=70,
            estimated_net_weight_kg=Decimal("70.00"),
            confirmed_net_weight_kg=Decimal("70.00"),
        )
        db.add(order_item)
        db.commit()
        print(f"  ✓ Created Order {order.order_ref_id} (Status: {order.status.value})")
        print(f"  ✓ Ordered: 70 packs of {product.name_en} (500g)")

        # -------------------------------------------------------------
        # PHASE 2: PROCUREMENT AGGREGATION & CONFIRMATION
        # -------------------------------------------------------------
        print("\n" + "-" * 40)
        print("PHASE 2: PROCUREMENT CONSOLIDATION")
        print("-" * 40)
        proc_service = PurchasingAggregatorService(db)
        mpl = proc_service.generate_purchasing_list(raw_file.id, actor=admin)
        print(f"  ✓ Master Purchasing List Generated: {mpl.id[:8]}...")
        print(f"  ✓ Aggregated Demand with Safety Buffer: {mpl.total_buffer_kg} kg")

        # Buyer confirms procurement
        confirmed_mpl = proc_service.confirm_purchasing(
            mpl.id,
            actual_purchases=[{"product_id": product.id, "actual_purchased_kg": 75.0, "actual_cost_thb": 1500.0}],
            actor=buyer,
        )
        print(f"  ✓ Buyer Confirmed: 75 kg purchased at Talad Thai (Status: {confirmed_mpl.status.value})")

        # -------------------------------------------------------------
        # PHASE 3: PACKING FLOOR & SCALE VERIFICATION
        # -------------------------------------------------------------
        print("\n" + "-" * 40)
        print("PHASE 3: PACKING FLOOR & SCALE VERIFICATION")
        print("-" * 40)
        pack_service = PackingOverrideService(db)
        pack_res = pack_service.fast_track_template_pack_order(order.id, actor=packer)
        print(f"  ✓ Box Packed & Scale Locked: {pack_res['boxes_count']} box (Gross: {pack_res['total_gross_weight_kg']} kg)")
        print(f"  ✓ Order Status Progressed to: {pack_res['status']}")

        # -------------------------------------------------------------
        # PHASE 4: ACCOUNTING SETTLEMENT (INVOICING & PAYMENT IN THB)
        # -------------------------------------------------------------
        print("\n" + "-" * 40)
        print("PHASE 4: ORDER-TO-CASH & BILLING SETTLEMENT")
        print("-" * 40)
        inv_service = OnBehalfInvoicingService(db)

        # 1. AI Agent 1 generates draft from locked final packing
        invoice = inv_service.generate_sub_customer_invoice(order.id, actor=admin)
        print(f"  ✓ 1. Draft Invoice Generated: {invoice.invoice_number}")
        print(f"       Total Amount: THB ฿{float(invoice.total_amount_eur):.2f}")
        print(f"       Status: {invoice.status.value}")

        # 2. Accounting review and dispatch
        dispatched_inv = inv_service.approve_and_dispatch(invoice_id=invoice.id, actor=accounting)
        print(f"  ✓ 2. Accounting Approved & Dispatched: {dispatched_inv.invoice_number} (Status: {dispatched_inv.status.value})")

        # 3. Customer payment cleared in full (Thai Baht)
        payment_res = inv_service.record_payment_and_issue_receipt(
            invoice_id=invoice.id,
            amount_paid=float(invoice.total_amount_eur),
            payment_date="2026-08-30",
            bank_name="Bangkok Bank (BBL)",
            bank_wire_reference="WIRE-BBL-20260830-01",
            payment_mode="BANK_TRANSFER",
            actor=accounting,
        )
        print(f"  ✓ 3. Payment Recorded: THB ฿{payment_res['total_amount_paid_eur']:.2f}")
        print(f"       Remaining Balance: THB ฿{payment_res['rolling_balance_remaining_eur']:.2f}")
        print(f"       Official Receipt Issued: {payment_res['receipt_number']}")

        # 4. Close Order
        closed_order = inv_service.close_paid_order(invoice_id=invoice.id, actor=accounting)
        print(f"  ✓ 4. Order Reconciliation Complete: Status = {closed_order.status.value}")

        print("\n" + "=" * 80)
        print("ALL 4 PHASES TESTED SUCCESSFULLY FROM CLEAN DATA IN THAI BAHT (฿)!")
        print("=" * 80)

    finally:
        db.close()


if __name__ == "__main__":
    run_pipeline()
