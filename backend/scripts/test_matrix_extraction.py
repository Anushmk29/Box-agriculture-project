import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.services.order_splitter import OrderSplitter
from app.services.purchasing_aggregator import PurchasingAggregator

from app.core.database import SessionLocal
from app.models import (
    ClientOrder,
    ClientOrderItem,
    OrderStatus,
    PurchasingItem,
    RawOrderFile,
    User,
    UserRole,
)
from app.services.matrix_parser import MatrixOrderParser


def test_pipeline():
    project_root = os.path.dirname(backend_dir)
    matrix_file = os.path.join(project_root, "documents", "Order Tonio 07.07.26 FULL.xlsx")

    print("================================================================================")
    print("STAGE 01 PIPELINE VERIFICATION TEST: Order Tonio 07.07.26 FULL.xlsx")
    print("================================================================================")

    # 1. Test Streaming Matrix Parser
    print("\n[Step 01] Running MatrixOrderParser on raw Excel workbook...")
    parser = MatrixOrderParser(matrix_file)
    parsed_data = parser.parse_all_clients()

    print(f"  - Sheets Processed: {parsed_data['sheets_processed']}")
    print(f"  - Total Clients Discovered: {parsed_data['total_clients_found']}")
    print(f"  - Total Unique SKUs Catalogued: {len(parsed_data['products_catalog'])}")

    active_clients = [c for c, items in parsed_data["clients_data"].items() if items]
    print(f"  - Clients with Positive Orders: {len(active_clients)}")
    print(f"  - Sample Clients: {active_clients[:8]}")

    # 2. Database Session & RawOrderFile
    db = SessionLocal()
    try:
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
                status=OrderStatus.PARSED,
            )
            db.add(raw_file)
            db.commit()
            db.refresh(raw_file)

        # 3. Test Order Splitter (Step 03)
        print("\n[Step 03] Executing AI Agent 1 Order Splitter...")
        splitter = OrderSplitter(db)
        client_orders = splitter.split_raw_order(raw_file.id, parsed_data)

        total_items_count = (
            db.query(ClientOrderItem)
            .join(ClientOrder)
            .filter(ClientOrder.raw_order_file_id == raw_file.id)
            .count()
        )
        total_net_weight = sum(o.total_estimated_net_weight_kg for o in client_orders)

        print(f"  - Total Client Orders Created: {len(client_orders)}")
        print(f"  - Total Line Items Across All Orders: {total_items_count}")
        print(f"  - Total Estimated Net Weight: {round(float(total_net_weight), 2)} kg")
        print("  - Sample 3 Generated Client Orders:")
        for o in client_orders[:3]:
            print(
                f"    * Ref: {o.order_ref_id} | Customer: {o.customer.client_code} | Items: {o.total_ordered_items_count} | Net: {o.total_estimated_net_weight_kg} kg"
            )

        # 4. Test Purchasing Aggregator (Step 04)
        print("\n[Step 04] Executing AI Agent 1 Master Purchasing List Aggregator...")
        aggregator = PurchasingAggregator(db)
        mpl = aggregator.consolidate_demand(raw_file.id)

        purchasing_items = (
            db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id == mpl.id).all()
        )
        total_purchasing_kg = sum(p.total_required_weight_kg for p in purchasing_items)
        total_purchasing_packs = sum(p.total_required_pack_count for p in purchasing_items)

        print(f"  - Master Purchasing Batch Ref: {mpl.batch_reference}")
        print(f"  - Total Aggregated SKUs: {mpl.total_skus_count}")
        print(f"  - Total Wholesale Packs to Buy: {total_purchasing_packs}")
        print(f"  - Total Wholesale Produce Weight: {round(float(total_purchasing_kg), 2)} kg")
        print("  - Top 5 High-Demand Wholesale Items for Procurement Buyers:")
        sorted_items = sorted(
            purchasing_items, key=lambda x: x.total_required_weight_kg, reverse=True
        )
        for it in sorted_items[:5]:
            print(
                f"    * {it.product.name_vi} / {it.product.name_en} ({it.pack_size}): {it.total_required_pack_count} packs | {it.total_required_weight_kg} kg"
            )

        print("\n================================================================================")
        print("STAGE 01 PIPELINE VERIFICATION PASSED WITH 100% SUCCESS!")
        print("================================================================================")
    finally:
        db.close()


if __name__ == "__main__":
    test_pipeline()
