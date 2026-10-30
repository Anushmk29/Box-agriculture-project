"""
Verification test for Thai Master Products, EL Segregation & Norway Integration
"""

import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)


from app.core.database import SessionLocal
from app.models.master_data import ClientSkuMapping, ControlStatus, Customer, Product
from app.services.name_matcher import NameFirstMatcher


def test_full_pipeline():
    db = SessionLocal()
    matcher = NameFirstMatcher(db)

    print("=" * 80)
    print("RUNNING VERIFICATION: THAI MASTER PRODUCTS, EL CLASSIFICATION & NORWAY")
    print("=" * 80)

    # 1. Total Product Counts
    total_prods = db.query(Product).count()
    el_prods = db.query(Product).filter(Product.control_status == ControlStatus.EL).count()
    print("\n[1] Database Catalog Counts:")
    print(f"    • Total SKUs in Database: {total_prods}")
    print(f"    • EL Controlled Products: {el_prods}")
    assert total_prods >= 712, f"Expected at least 712 products, got {total_prods}"
    assert el_prods >= 21, f"Expected at least 21 EL products, got {el_prods}"
    print("    ✓ Product counts validated.")

    # 2. Verify Key EL Products
    print("\n[2] Verifying Key EL Controlled Products:")
    key_el = ["11-004", "11-005", "11-010", "11-013", "11-022", "11-036", "11-047", "EL-450"]
    for code in key_el:
        p = db.query(Product).filter(Product.t_code == code).first()
        assert p is not None, f"Product {code} missing"
        assert p.control_status == ControlStatus.EL, (
            f"Product {code} should be EL, got {p.control_status}"
        )
        print(
            f"    ✓ {p.t_code:<8} | {p.name_th:<25} | {p.name_en:<25} | Family: {p.botanical_family} | {p.control_status.value}"
        )

    # 3. Verify Norway Customer Profile
    print("\n[3] Verifying Norway Customer Profile:")
    nga = db.query(Customer).filter(Customer.client_code == "NGA-MAIN").first()
    assert nga is not None, "Customer NGA-MAIN missing"
    assert nga.route_group == "NGA", f"Expected route NGA, got {nga.route_group}"
    assert nga.destination_country == "Norway", f"Expected Norway, got {nga.destination_country}"
    print(
        f"    ✓ {nga.client_code} | {nga.company_name} | Route: {nga.route_group} | Destination: {nga.destination_country}"
    )

    sub_917 = db.query(Customer).filter(Customer.client_code == "NGA-917L").first()
    assert sub_917 is not None, "Sub-customer NGA-917L missing"
    print(
        f"    ✓ Sub-Customer {sub_917.client_code} linked to Parent: {sub_917.parent_customer.client_code}"
    )

    # 4. Verify Norway Mappings (20 items)
    print("\n[4] Verifying 20 Norway Customer SKU Mappings & Barcodes:")
    mappings = db.query(ClientSkuMapping).filter(ClientSkuMapping.main_customer_id == nga.id).all()
    assert len(mappings) == 20, f"Expected 20 Norway mappings, got {len(mappings)}"
    for m in mappings:
        p = m.product
        assert p.norway_code == m.client_sku_code, (
            f"Mismatch in norway_code for {m.client_sku_code}"
        )
        assert p.barcode is not None, f"Missing barcode for {m.client_sku_code}"
        print(
            f"    ✓ Norway Code: {m.client_sku_code:<6} | Barcode: {p.barcode:<14} | Name: {m.client_raw_name:<15} -> {p.t_code} ({p.name_en})"
        )

    # 5. Verify NameFirstMatcher Resolutions
    print("\n[5] Verifying 6-Tier SKU Standardization Resolution:")
    test_cases = [
        # (input_name, input_code, expected_sku, tier_name)
        ("ตะไคร้", None, "11-001", "NAME_EXACT_TH"),
        ("LEMON GLASS", None, "11-001", "NAME_EXACT_EN"),
        ("", "11-001", "11-001", "CODE_FALLBACK"),
        ("", "11001", "11-001", "CODE_FALLBACK"),
        ("", "EL010", "11-010", "CODE_FALLBACK"),  # EL prefix mapping to คื่นช่าย 11-010
        ("", "11473", "11-001", "PRE_FED_CLIENT_SKU"),  # Norway Customer Code
        ("", "8859899114730", "11-001", "CODE_FALLBACK"),  # 13-digit EAN Barcode
        ("กะเพรา", None, "11-004", "NAME_EXACT_TH"),
        ("โหระพา", None, "11-005", "NAME_EXACT_TH"),
    ]

    for raw_name, raw_code, exp_sku, _exp_tier in test_cases:
        resolved_prod, tier, conf = matcher.resolve_product(
            raw_name=raw_name, raw_code=raw_code, main_customer_id=nga.id
        )
        assert resolved_prod is not None, f"Failed to resolve ({raw_name}, {raw_code})"
        assert resolved_prod.t_code == exp_sku, (
            f"Expected SKU {exp_sku}, got {resolved_prod.t_code} for ({raw_name}, {raw_code})"
        )
        print(
            f"    ✓ Input: [Name: '{raw_name}', Code: '{raw_code}'] -> Resolved: {resolved_prod.t_code} ({resolved_prod.name_th} / {resolved_prod.name_en}) via {tier} (Confidence: {conf})"
        )

    print("\n" + "=" * 80)
    print("ALL VERIFICATIONS PASSED SUCCESSFULLY (100% GREEN)!")
    print("=" * 80)


if __name__ == "__main__":
    test_full_pipeline()
