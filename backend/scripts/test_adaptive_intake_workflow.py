"""
Adaptive Multi-Modal Intake & Reconciliation Workflow Test
Verifies end-to-end processing across all 7 input archetypes from:
  documents/new input format/
"""

import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.core.database import SessionLocal
from app.services.adaptive_order_orchestrator import AdaptiveOrderOrchestrator


def run_adaptive_intake_test():
    print("=" * 85)
    print("ADAPTIVE MULTI-MODAL ORDER INTAKE & RECONCILIATION TEST")
    print("Folder: documents/new input format/")
    print("=" * 85)

    db = SessionLocal()
    orchestrator = AdaptiveOrderOrchestrator(db)
    docs_folder = os.path.join(os.path.dirname(backend_dir), "documents", "new input format")

    test_matrix = [
        # (Archetype description, filename, shipment_date)
        ("1. Official Vector PDF (Paris Store)", "ALINDAR.pdf", "2026-09-07"),
        ("2. Official Vector PDF (Italy MXP)", "Order ITM ETD 06-09-2026.pdf", "2026-09-06"),
        ("3. Legacy Binary XLS (Switzerland Vevey)", "VEVEY_Ly-Thanh-Sarl-Order-Vegetable-for-07.09.2026.xls", "2026-09-07"),
        ("4. Modern Branch XLSX (Switzerland Morges)", "Ly-Thanh-Sarl Order Vegetable for Monday 07-09-2026.xlsx", "2026-09-07"),
        ("5. Unstructured Single-Col Excel Text", "20T.xlsx", "2026-09-07"),
        ("6. Sub-Shop Detail Form (Norway H-route)", "66H.xlsx", "2026-09-07"),
        ("7. Single-Client Tabular Order (Japan JCT)", "Order (JCT) CHAOTHAI CO.,LTD 29-08-2026 .xlsx", "2026-08-29"),
        ("8. LINE Chat Screenshot (Japan Sopit)", "S__132292630_0.jpg", "2026-08-29"),
        ("9. WhatsApp Mixed Chat Screenshot (P'Pui)", "ออเดอร์ดิบ พี่ปุ้ย.png", "2026-08-07"),
        ("10. Email Screenshot & Cancellations (Norway)", "68H.png", "2026-09-07"),
    ]

    total_files_tested = 0
    passed_files = 0
    total_items = 0
    total_matched = 0

    for label, fname, ship_date in test_matrix:
        fpath = os.path.join(docs_folder, fname)
        print(f"\n[{label}] Ingesting: {fname}...")
        try:
            res = orchestrator.ingest_file(fpath, shipment_date=ship_date)
            total_files_tested += 1
            passed_files += 1
            total_items += res["total_items_processed"]
            total_matched += res["matched_items_count"]

            print(f"  ✓ Channel Detected: {res['channel']}")
            print(f"  ✓ Sub-Customers Detected: {res['detected_sub_customers']}")
            print(f"  ✓ Orders Created: {res['created_orders']}")
            print(
                f"  ✓ Items Processed: {res['total_items_processed']} (Matched: {res['matched_items_count']} | Rate: {res['match_rate_percentage']}%)"
            )
            print(f"  ✓ Total Net Weight: {res['total_net_weight_kg']:.2f} kg")
            print(f"  ✓ Consolidated Farmer Procurement SKUs: {res['procurement_consolidated_skus']}")
        except Exception as e:
            print(f"  ✗ FAILED to ingest {fname}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 85)
    print("FINAL SUMMARY REPORT:")
    print(f"  Files Successfully Ingested: {passed_files} / {len(test_matrix)}")
    print(f"  Total Line Items Processed: {total_items}")
    print(f"  Total SKUs Matched: {total_matched} (Overall Match Rate: {total_matched / total_items * 100:.1f}%)")
    print("=" * 85)


if __name__ == "__main__":
    run_adaptive_intake_test()

