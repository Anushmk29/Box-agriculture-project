import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.core.database import SessionLocal
from app.models import (
    AdjustmentType,
    Box,
    BoxCatalogue,
    BoxItem,
    ClientSkuMapping,
    Customer,
    CustomerSellingPrice,
    OrderStatus,
    Product,
    PurchasingItem,
    RawOrderFile,
    Receipt,
    User,
    UserRole,
)
from app.services.matrix_parser import MatrixOrderParser
from app.services.name_matcher import NameFirstMatcher
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService
from app.services.order_splitter import OrderSplitter
from app.services.packing_override_service import PackingOverrideService
from app.services.purchasing_aggregator import PurchasingAggregator


def run_test():
    print("=" * 80)
    print("COMPREHENSIVE PIPELINE TEST: CLIENT UPDATES INTEGRATION")
    print("=" * 80)

    db = SessionLocal()
    project_root = os.path.dirname(backend_dir)
    matrix_file = os.path.join(project_root, "documents", "Order Tonio 07.07.26 FULL.xlsx")

    try:
        # ----------------------------------------------------------------------
        # 1. TEST INTAKE & INSTANT SUB-CUSTOMER ACKNOWLEDGMENT
        # ----------------------------------------------------------------------
        print("\n[TEST 1] Testing Instant Intake & Acknowledgment...")
        parser = MatrixOrderParser(matrix_file)
        ack = parser.inspect_sub_customers()
        print(f"  ✓ Total Sub-Customers Detected: {ack['total_sub_customers']}")
        print(f"  ✓ Sheets Processed: {[s['sheet_name'] for s in ack['sheets_processed']]}")
        print(f"  ✓ Sample Sub-Customer Codes: {ack['sub_customer_codes'][:6]}")
        assert ack["total_sub_customers"] > 0, "Sub-customers should be detected!"
        assert ack["status"] == "ACKNOWLEDGED_PENDING_VERIFICATION"

        # ----------------------------------------------------------------------
        # 2. SETUP MAIN CUSTOMER & PRE-FED STANDARDIZED SKU DATABASE
        # ----------------------------------------------------------------------
        print("\n[TEST 2] Setting up Main Customer & Standardized Client SKU Database...")
        main_cust = db.query(Customer).filter(Customer.client_code == "TONIO-MAIN").first()
        if not main_cust:
            main_cust = Customer(
                client_code="TONIO-MAIN",
                company_name="Tonio Trade Europe BV",
                billing_email="tonio@trade.eu",
                route_group="TETE",
                destination_country="France",
                is_main_customer=True,
            )
            db.add(main_cust)
            db.commit()
            db.refresh(main_cust)
        print(f"  ✓ Main Customer: {main_cust.company_name} (ID: {main_cust.id})")

        # Find or create a master product (Morning Glory / T050)
        product = db.query(Product).filter(Product.t_code == "T050").first()
        if not product:
            product = db.query(Product).first()
        print(
            f"  ✓ Target Master Product: {product.t_code} ({product.name_vi} / {product.name_en})"
        )

        # Add pre-fed client SKU mapping (Eliminating spelling errors / typos)
        sku_code = "TONIO-MG-TYPO"
        raw_typo_name = "Rau Muong Typo X"
        existing_mapping = (
            db.query(ClientSkuMapping)
            .filter(
                ClientSkuMapping.main_customer_id == main_cust.id,
                ClientSkuMapping.client_raw_name == raw_typo_name,
            )
            .first()
        )
        if not existing_mapping:
            mapping = ClientSkuMapping(
                main_customer_id=main_cust.id,
                client_sku_code=sku_code,
                client_raw_name=raw_typo_name,
                product_id=product.id,
                standard_pack_size="200g",
                is_verified=True,
            )
            db.add(mapping)
            db.commit()
        print(f"  ✓ Pre-fed SKU Mapping added: '{raw_typo_name}' -> {product.t_code}")

        # Test Tier 0 lookup in NameFirstMatcher
        matcher = NameFirstMatcher(db)
        resolved_prod, tier, conf = matcher.resolve_product(
            raw_name=raw_typo_name, raw_code=sku_code, main_customer_id=main_cust.id
        )
        assert resolved_prod.id == product.id, "Should resolve to master product via pre-fed SKU!"
        assert tier == "PRE_FED_CLIENT_SKU", f"Tier should be PRE_FED_CLIENT_SKU, got {tier}"
        print(f"  ✓ NameFirstMatcher resolved typo using Tier 0: {tier} (Confidence: {conf})")

        # ----------------------------------------------------------------------
        # 3. TEST ORDER SPLITTER WITH HIERARCHY & DEDUPLICATION
        # ----------------------------------------------------------------------
        print("\n[TEST 3] Testing Order Splitting with Sub-Customer Hierarchy & Deduplication...")
        user = db.query(User).filter(User.role == UserRole.OWNER_ADMIN).first()
        raw_file = (
            db.query(RawOrderFile)
            .filter(RawOrderFile.file_name == "Order Tonio 07.07.26 FULL.xlsx")
            .first()
        )
        if not raw_file:
            raw_file = RawOrderFile(
                file_name="Order Tonio 07.07.26 FULL.xlsx",
                storage_url=matrix_file,
                shipment_target_date="2026-07-07",
                route_line="TETE",
                uploaded_by=user.id,
                main_customer_id=main_cust.id,
                detected_sub_customer_count=ack["total_sub_customers"],
                acknowledgment_summary=ack,
                status=OrderStatus.PARSED,
            )
            db.add(raw_file)
            db.commit()
            db.refresh(raw_file)
        else:
            raw_file.main_customer_id = main_cust.id

        parsed_data = parser.parse_all_clients()
        splitter = OrderSplitter(db)
        client_orders = splitter.split_raw_order(
            raw_file_id=raw_file.id, parsed_data=parsed_data, main_customer_id=main_cust.id
        )
        print(f"  ✓ Split into {len(client_orders)} Sub-Customer Orders")
        first_sub_order = client_orders[0]
        assert first_sub_order.main_customer_id == main_cust.id, (
            "Sub-order must link to main customer!"
        )
        assert first_sub_order.customer.parent_customer_id == main_cust.id, (
            "Sub-customer must have parent customer ID!"
        )
        print(
            f"  ✓ Verified Hierarchy: Sub-Customer '{first_sub_order.customer.client_code}' linked to Parent '{main_cust.client_code}'"
        )

        # ----------------------------------------------------------------------
        # 4. TEST MULTI-ORDER COMBINATION MERGE TOOL & BUFFER ADDITION
        # ----------------------------------------------------------------------
        print("\n[TEST 4] Testing Multi-Order Combination Merge Tool & Buffer Sticky Memory...")
        # Create a second mock raw order file for combination testing
        second_raw_file = (
            db.query(RawOrderFile).filter(RawOrderFile.file_name == "Order Paris Bulk.xlsx").first()
        )
        if not second_raw_file:
            second_raw_file = RawOrderFile(
                file_name="Order Paris Bulk.xlsx",
                storage_url="mock_paris_order.xlsx",
                shipment_target_date="2026-07-07",
                route_line="TETE",
                uploaded_by=user.id,
                main_customer_id=main_cust.id,
                status=OrderStatus.SPLIT_AND_VALIDATED,
            )
            db.add(second_raw_file)
            db.commit()
            db.refresh(second_raw_file)

        # Set a 12% default buffer on our test product
        product.default_buffer_percentage = 12.00
        db.commit()

        aggregator = PurchasingAggregator(db)
        mpl = aggregator.combine_and_consolidate(
            raw_file_ids=[raw_file.id, second_raw_file.id],
            batch_ref="PUR-20260707-MERGED-DEMO",
            shipment_date="2026-07-07",
            route_code="TETE",
        )
        print(f"  ✓ Master Purchase Order Generated: {mpl.batch_reference}")
        print(f"  ✓ Order Sources Linked: {len(mpl.sources)} files merged")
        assert len(mpl.sources) == 2, "Both files must be recorded in purchasing_order_sources!"

        # Check buffer calculation on purchasing items
        p_item = (
            db.query(PurchasingItem)
            .filter(
                PurchasingItem.purchasing_list_id == mpl.id, PurchasingItem.product_id == product.id
            )
            .first()
        )
        if p_item:
            print(f"  ✓ SKU: {product.t_code} ({product.name_en})")
            print(f"    - Base Ordered Weight: {float(p_item.ordered_net_weight_kg)} kg")
            print(f"    - Buffer Applied: {float(p_item.buffer_percentage)}%")
            print(f"    - Calculated Buffer Kg: {float(p_item.buffer_weight_kg)} kg")
            print(f"    - Final Procurement Target: {float(p_item.total_required_weight_kg)} kg")
            assert float(p_item.buffer_percentage) == 12.00
            assert round(float(p_item.ordered_net_weight_kg) * 1.12, 2) == float(
                p_item.total_required_weight_kg
            )

        # Test updating buffer with sticky memory
        aggregator.update_product_buffer(
            product_id=product.id,
            new_buffer_pct=15.00,
            purchasing_item_id=p_item.id if p_item else None,
            save_as_default=True,
        )
        db.refresh(product)
        assert float(product.default_buffer_percentage) == 15.00, (
            "Buffer percentage must persist in Product master!"
        )
        print(
            f"  ✓ Sticky Memory Verified: Product default_buffer_percentage updated to {float(product.default_buffer_percentage)}%"
        )

        # ----------------------------------------------------------------------
        # 5. TEST BOX MANAGEMENT: FLOOR OVERRIDES & TRANSPORTATION MANIFEST
        # ----------------------------------------------------------------------
        print("\n[TEST 5] Testing Floor Box Management & Transportation Manifest Sync...")
        box_cat = db.query(BoxCatalogue).first()
        box_service = PackingOverrideService(db)

        # Create or fetch a test box for the first sub-customer
        test_box = db.query(Box).filter(Box.client_order_id == first_sub_order.id).first()
        if not test_box:
            test_box = Box(
                client_order_id=first_sub_order.id,
                box_catalogue_id=box_cat.id,
                box_number=1,
                shipment_date="2026-07-07",
                route_code="TETE",
                is_mixed_box=False,
                qr_code_uid="BOX-TETE-20260707-001-TEST",
                actual_gross_weight_kg=5.80,
                actual_net_weight_kg=5.20,
                is_locked=False,
            )
            db.add(test_box)
            db.commit()
            db.refresh(test_box)

            # Add an item to the box
            box_item = BoxItem(
                box_id=test_box.id,
                product_id=product.id,
                pack_size="200g",
                final_packed_count=26,
                final_net_weight_kg=5.20,
                adjustment_type=AdjustmentType.AS_ORDERED,
            )
            db.add(box_item)
            db.commit()

        # Floor Override: Staff adjusts physical box dimensions to 50x40x30 cm
        overridden_box = box_service.override_box_size(
            box_id=test_box.id,
            custom_length_cm=50.0,
            custom_width_cm=40.0,
            custom_height_cm=30.0,
            override_reason="Warehouse ran out of standard Small Foam; used custom 50x40x30 carton",
        )
        print(f"  ✓ Box #{overridden_box.box_number} Overridden:")
        print(
            f"    - Dimensions: {overridden_box.custom_length_cm}x{overridden_box.custom_width_cm}x{overridden_box.custom_height_cm} cm"
        )
        print(
            f"    - Calculated Volumetric Weight: {float(overridden_box.volumetric_weight_kg)} kg"
        )
        print(f"    - Chargeable Weight: {float(overridden_box.chargeable_weight_kg)} kg")
        print(f"    - Reason: {overridden_box.override_reason}")
        assert overridden_box.is_manually_overridden is True
        assert float(overridden_box.volumetric_weight_kg) == 10.00  # (50*40*30)/6000 = 10.0

        # Floor Override: Add extra box to increase total box count
        extra_box = box_service.add_extra_box_to_order(
            client_order_id=first_sub_order.id,
            box_catalogue_id=box_cat.id,
            shipment_date="2026-07-07",
            route_code="TETE",
        )
        print(f"  ✓ Added Extra Box #{extra_box.box_number} (Total box count dynamically adjusted)")

        # Sync Transportation Manifest
        manifest = box_service.sync_transportation_manifest(
            route_code="TETE", shipment_date="2026-07-07", flight_carrier="Thai Airways / TG930"
        )
        print(f"  ✓ Transportation Manifest Generated: {manifest.manifest_reference}")
        print(f"    - Total Boxes: {manifest.total_boxes_count}")
        print(f"    - Total Gross Wt: {float(manifest.total_gross_weight_kg)} kg")
        print(f"    - Total Volumetric Wt: {float(manifest.total_volumetric_weight_kg)} kg")
        print(f"    - Chargeable Container Wt: {float(manifest.total_chargeable_weight_kg)} kg")
        print(f"    - Total CBM: {float(manifest.total_cbm)} m³")

        # ----------------------------------------------------------------------
        # 6. TEST ON-BEHALF INVOICING & PRIVATE INTERNAL NOTES
        # ----------------------------------------------------------------------
        print("\n[TEST 6] Testing On-Behalf Invoicing & Private Financial Notes...")
        # Configure Main Customer Selling Price for this sub-customer
        selling_price_entry = (
            db.query(CustomerSellingPrice)
            .filter(
                CustomerSellingPrice.main_customer_id == main_cust.id,
                CustomerSellingPrice.product_id == product.id,
                CustomerSellingPrice.pack_size == "200g",
            )
            .first()
        )
        if not selling_price_entry:
            selling_price_entry = CustomerSellingPrice(
                main_customer_id=main_cust.id,
                sub_customer_id=first_sub_order.customer_id,
                product_id=product.id,
                pack_size="200g",
                selling_price_eur=4.25,  # Selling price substituted for sub-customer
                valid_from="2026-01-01",
            )
            db.add(selling_price_entry)
            db.commit()
        else:
            selling_price_entry.selling_price_eur = 4.25
            db.commit()

        invoicing_service = OnBehalfInvoicingService(db)
        invoice = invoicing_service.generate_sub_customer_invoice(
            client_order_id=first_sub_order.id,
            internal_notes="Private memo: Client requested special export phytosanitary documentation",
        )
        print(f"  ✓ Sub-Customer Commercial Invoice Generated: {invoice.invoice_number}")
        print(f"    - Invoice Type: {invoice.invoice_type}")
        print(f"    - Issuer / Seller: {invoice.seller_customer.company_name} (Main Customer)")
        print(f"    - Recipient / Buyer: {invoice.customer.company_name} (Sub-Customer)")
        print(
            f"    - Line Item Unit Price: €{float(invoice.items[0].unit_price_eur)} (Substituted Main Customer Selling Price!)"
        )
        print(f"    - Subtotal: €{float(invoice.subtotal_eur)}")
        print(f"    - Internal Notes: {invoice.internal_notes}")
        assert float(invoice.items[0].unit_price_eur) == 4.25, "Must use designated selling price!"

        # ----------------------------------------------------------------------
        # 7. TEST PAYMENT APPROVAL, ROLLING BALANCE & RECEIPT TRIGGER
        # ----------------------------------------------------------------------
        print("\n[TEST 7] Testing Manual Payment Approval, Rolling Balances & Receipt Trigger...")
        total_due = float(invoice.total_amount_eur)
        partial_amount = round(total_due * 0.40, 2)  # Pay 40% first

        # Partial Payment
        res1 = invoicing_service.record_payment_and_issue_receipt(
            invoice_id=invoice.id,
            amount_paid=partial_amount,
            payment_date="2026-07-10",
            bank_name="Bangkok Bank (SWIFT)",
            bank_wire_reference="SWIFT-BKK-001928",
            internal_notes="First installment received. Remainder expected next Tuesday.",
            recorded_by_user_id=user.id,
        )
        print("  ✓ Partial Payment Recorded:")
        print(f"    - Paid: €{res1['amount_paid_this_tx']}")
        print(f"    - Rolling Balance Remaining: €{res1['rolling_balance_remaining_eur']}")
        print(f"    - Status: {res1['status']}")
        print(f"    - Receipt Issued: {res1['receipt_issued']}")
        assert res1["status"] == "PARTIALLY_PAID"
        assert res1["receipt_issued"] is False

        # Final Payment
        remainder_amount = res1["rolling_balance_remaining_eur"]
        res2 = invoicing_service.record_payment_and_issue_receipt(
            invoice_id=invoice.id,
            amount_paid=remainder_amount,
            payment_date="2026-07-15",
            bank_name="Bangkok Bank (SWIFT)",
            bank_wire_reference="SWIFT-BKK-002041",
            internal_notes="Final balance cleared in full via wire. Account settled.",
            recorded_by_user_id=user.id,
        )
        print("  ✓ Final Payment Recorded:")
        print(f"    - Paid: €{res2['amount_paid_this_tx']}")
        print(f"    - Rolling Balance Remaining: €{res2['rolling_balance_remaining_eur']}")
        print(f"    - Status: {res2['status']}")
        print(
            f"    - Official Receipt Issued: {res2['receipt_issued']} (Receipt No: {res2['receipt_number']})"
        )
        assert res2["status"] == "CONFIRMED_PAID"
        assert res2["receipt_issued"] is True
        assert res2["receipt_number"] is not None

        # Verify receipt has private internal notes
        receipt = db.query(Receipt).filter(Receipt.invoice_id == invoice.id).first()
        assert receipt.internal_notes is not None
        print(f"  ✓ Receipt Private Notes Verified: '{receipt.internal_notes}'")

        print("\n" + "=" * 80)
        print("ALL 7 CLIENT UPDATE FEATURES VERIFIED & PASSING WITH 100% SUCCESS!")
        print("=" * 80)

    finally:
        db.close()


if __name__ == "__main__":
    run_test()
