import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Box, ClientOrder, Customer, Invoice, Product


def test_all_apis():
    print("=" * 80)
    print("FASTAPI REST ENDPOINTS INTEGRATION TEST")
    print("=" * 80)

    client = TestClient(app)
    db = SessionLocal()

    try:
        # Health check
        res = client.get("/health")
        assert res.status_code == 200
        print("  ✓ GET /health -> 200 OK")

        # Master Data SKU Mappings
        product = db.query(Product).first()
        main_cust = db.query(Customer).filter(Customer.is_main_customer.is_(True)).first()
        sub_cust = db.query(Customer).filter(Customer.is_main_customer.is_(False)).first()

        res = client.post(
            "/api/v3/master-data/sku-mappings",
            json={
                "main_customer_id": main_cust.id if main_cust else None,
                "client_sku_code": "API-TEST-SKU",
                "client_raw_name": "API Test Produce Name",
                "product_id": product.id,
                "standard_pack_size": "200g",
            },
        )
        assert res.status_code == 200
        print(
            f"  ✓ POST /api/v3/master-data/sku-mappings -> 200 OK (Mapped {res.json()['client_sku_code']})"
        )

        # Set Selling Price
        if main_cust and sub_cust:
            res = client.post(
                "/api/v3/master-data/sku-mappings/selling-prices",
                json={
                    "main_customer_id": main_cust.id,
                    "sub_customer_id": sub_cust.id,
                    "product_id": product.id,
                    "pack_size": "200g",
                    "selling_price_eur": 5.50,
                },
            )
            assert res.status_code == 200
            print(
                f"  ✓ POST /api/v3/master-data/sku-mappings/selling-prices -> 200 OK (Price: €{res.json()['selling_price_eur']})"
            )

        # Packing List
        order = db.query(ClientOrder).first()
        if order:
            res = client.get(f"/api/v3/packing/orders/{order.id}/packing-list")
            assert res.status_code == 200
            print(
                f"  ✓ GET /api/v3/packing/orders/{order.id}/packing-list -> 200 OK ({res.json()['total_boxes_count']} boxes)"
            )

        # Box Override
        box = db.query(Box).first()
        if box:
            res = client.put(
                f"/api/v3/packing/boxes/{box.id}/override-size",
                json={
                    "custom_length_cm": 45.0,
                    "custom_width_cm": 35.0,
                    "custom_height_cm": 25.0,
                    "override_reason": "Floor adjustment via API",
                },
            )
            assert res.status_code == 200
            print(
                f"  ✓ PUT /api/v3/packing/boxes/{box.id}/override-size -> 200 OK (Vol Wt: {res.json()['volumetric_weight_kg']} kg)"
            )

        # Transportation Manifest Sync
        res = client.post(
            "/api/v3/packing/manifests/sync?route_code=TETE&shipment_date=2026-07-07&flight_carrier=Air%20France%20AF165"
        )
        assert res.status_code == 200
        print(
            f"  ✓ POST /api/v3/packing/manifests/sync -> 200 OK (Ref: {res.json()['manifest_reference']}, Chargeable: {res.json()['total_chargeable_weight_kg']} kg)"
        )

        # Invoicing Internal Notes Update
        inv = db.query(Invoice).first()
        if inv:
            res = client.put(
                f"/api/v3/invoicing/{inv.id}/internal-notes",
                json={"internal_notes": "Updated private accounting memo via REST API"},
            )
            assert res.status_code == 200
            print(f"  ✓ PUT /api/v3/invoicing/{inv.id}/internal-notes -> 200 OK")

            # Get Invoice Detail
            res = client.get(f"/api/v3/invoicing/{inv.id}")
            assert res.status_code == 200
            print(
                f"  ✓ GET /api/v3/invoicing/{inv.id} -> 200 OK (Invoice #{res.json()['invoice_number']})"
            )

        print("=" * 80)
        print("ALL FASTAPI REST ENDPOINTS TESTED & OPERATIONAL!")
        print("=" * 80)

    finally:
        db.close()


if __name__ == "__main__":
    test_all_apis()
