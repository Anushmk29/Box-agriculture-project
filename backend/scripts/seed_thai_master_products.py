"""
Seed Thai Master Products & Norway Client Data
Source: documents/new docs-mail/Product Codes/PRODUCT CODES.xlsx
Registers:
1. Customer NGA (Norway route) + Sub-customers NGA-917L and NGA-034L
2. 712 Official Thai & English Produce SKUs matching exact Sunny SKU codes (11-xxx, 22-xxx, EL-xxx)
3. EL vs COMBINABLE quarantine control status + Botanical families
4. 20 Customer-mandated Norway Product Codes & 13-digit EAN Barcodes in ClientSkuMapping & Product
"""

import os
import sys

import openpyxl

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)


from app.core.database import Base, SessionLocal, engine
from app.models.master_data import (
    BoxMaterial,
    ClientSkuMapping,
    ControlStatus,
    Customer,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)

# Botanical families for EL and quarantine produce
BOTANICAL_MAP = {
    "11-004": ("Lamiaceae", ProduceType.HERB, "HOLY BASIL"),
    "11-005": ("Lamiaceae", ProduceType.HERB, "SWEET BASIL"),
    "11-007": ("Brassicaceae", ProduceType.LEAFY, "KALE"),
    "11-008": ("Brassicaceae", ProduceType.LEAFY, "YOUNG KALE"),
    "11-010": ("Apiaceae", ProduceType.HERB, "CHINESE LEEK, CELERY"),
    "11-013": ("Solanaceae", ProduceType.LEAFY, "EGG PLANT"),
    "11-016": ("Cucurbitaceae", ProduceType.LEAFY, "BALSAM PEAR"),
    "11-022": ("Solanaceae", ProduceType.HERB, "CHILLI BIRD MIXED"),
    "11-023": ("Solanaceae", ProduceType.HERB, "RED CHILLI"),
    "11-024": ("Solanaceae", ProduceType.HERB, "GREEN CHILLI"),
    "11-025": ("Solanaceae", ProduceType.HERB, "CHILLI SPUR RED PEPPER"),
    "11-026": ("Solanaceae", ProduceType.HERB, "CHILLI SPUR GREEN PEPPER"),
    "11-027": ("Solanaceae", ProduceType.HERB, "CHILLI SPUR YELLOW PEPPER"),
    "11-036": ("Apiaceae", ProduceType.HERB, "CORIANDER"),
    "11-037": ("Apiaceae", ProduceType.HERB, "CULANTRO / SAWTOOTH CORIANDER"),
    "11-047": ("Fabaceae", ProduceType.LEAFY, "YARD LONG BEAN"),
    "11-400": ("Fabaceae", ProduceType.LEAFY, "YARD LONG BEAN 1000G"),
    "EL-450": ("Solanaceae", ProduceType.LEAFY, "EGG PLANT BOX"),
    "EL-451": ("Fabaceae", ProduceType.LEAFY, "YARD LONG BEAN BOX"),
    "EL-452": ("Solanaceae", ProduceType.LEAFY, "EGG PLANT BARE"),
    "EL-453": ("Apiaceae", ProduceType.HERB, "CORIANDER 50G"),
}

# 20 Norway items mapping from Column 7, 8, 9
NORWAY_MAPPINGS = [
    {"th": "ตะไคร้", "code": "11473", "barcode": "8859899114730", "target_sku": "11-001"},
    {"th": "ข่าอ่อน", "code": "11474", "barcode": "8859899114747", "target_sku": "11-002"},
    {"th": "ใบมะกรูดสด", "code": "11532", "barcode": "8859899115324", "target_sku": "11-003"},
    {"th": "ผักบุ้งจีน", "code": "11320", "barcode": "8859899113207", "target_sku": "11-006"},
    {"th": "กวางตุ้ง", "code": "11322", "barcode": "8859899113221", "target_sku": "11-009"},
    {"th": "มะเขือพวง", "code": "11475", "barcode": "8859899114754", "target_sku": "11-014"},
    {"th": "บวบเหลี่ยม", "code": "11015", "barcode": "8859899110152", "target_sku": "11-015"},
    {"th": "ผักแขยง", "code": "11476", "barcode": "8859899114761", "target_sku": "11-017"},
    {"th": "ผักแพร้ว", "code": "11477", "barcode": "8859899114778", "target_sku": "11-018"},
    {"th": "ผักปลัง", "code": "11324", "barcode": "8859899113245", "target_sku": "11-019"},
    {"th": "ผักหวานป่า", "code": "11478", "barcode": "8859899114785", "target_sku": "11-020"},
    {"th": "ก้านคูน", "code": "11323", "barcode": "8859899113238", "target_sku": "11-021"},
    {"th": "มะละกอดิบ", "code": "11028", "barcode": "8859899110282", "target_sku": "11-028"},
    {"th": "กะเพรา", "code": "11519", "barcode": "8859899115195", "target_sku": "11-004"},
    {"th": "โหระพา", "code": "11520", "barcode": "8859899115201", "target_sku": "11-005"},
    {"th": "ขึ้นฉ่าย", "code": "11521", "barcode": "8859899115218", "target_sku": "11-010"},
    {"th": "มะเขือเปราะ", "code": "11372", "barcode": "8859899113726", "target_sku": "11-013"},
    {"th": "ผักชีไทย", "code": "11531", "barcode": "8859899115317", "target_sku": "11-036"},
    {"th": "ผักชีฝรั่ง", "code": "11523", "barcode": "8859899115232", "target_sku": "11-037"},
    {"th": "ถั่วฝักยาว", "code": "11374", "barcode": "8859899113740", "target_sku": "11-047"},
]


def seed_thai_master_products():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    project_root = os.path.dirname(backend_dir)
    excel_path = os.path.join(
        project_root, "documents", "new docs-mail", "Product Codes", "PRODUCT CODES.xlsx"
    )

    if not os.path.exists(excel_path):
        print(f"Error: Master file not found at {excel_path}")
        return

    print("=" * 80)
    print("INGESTING THAI MASTER PRODUCT CODES & NORWAY MAPPINGS")
    print("=" * 80)

    # 1. Setup Norway Main Customer & Sub-Customers
    print("\n[1/3] Setting up Norway Customer Profile (Route NGA)...")
    nga_main = db.query(Customer).filter(Customer.client_code == "NGA-MAIN").first()
    if not nga_main:
        nga_main = Customer(
            client_code="NGA-MAIN",
            company_name="Norway Fresh Produce AS",
            billing_email="orders@norwayfresh.no",
            route_group="NGA",
            destination_country="Norway",
            is_main_customer=True,
        )
        db.add(nga_main)
        db.flush()
        print("  ✓ Created NGA-MAIN corporate record.")
    else:
        nga_main.route_group = "NGA"
        nga_main.destination_country = "Norway"
        print("  ✓ Existing NGA-MAIN record verified.")

    # Sub-customers from shipment files (NGA-917L, NGA-034L)
    for sub_code in ["NGA-917L", "NGA-034L"]:
        sub_cust = db.query(Customer).filter(Customer.client_code == sub_code).first()
        if not sub_cust:
            sub_cust = Customer(
                client_code=sub_code,
                company_name=f"Norway Store {sub_code}",
                billing_email=f"{sub_code.lower()}@norwayfresh.no",
                route_group="NGA",
                destination_country="Norway",
                is_main_customer=False,
                parent_customer_id=nga_main.id,
            )
            db.add(sub_cust)
            print(f"  ✓ Created Sub-Customer {sub_code}.")

    db.commit()

    # 2. Ingest 712 Product Codes from Excel
    print("\n[2/3] Reading PRODUCT CODES.xlsx and ingesting SKUs...")
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb["รายงานทะเบียนสินค้า"]

    created_count = 0
    updated_count = 0
    el_count = 0
    seen_skus = set()

    for r in range(8, ws.max_row + 1):
        raw_sku = ws.cell(r, 3).value
        raw_name_th = ws.cell(r, 4).value
        raw_name_en = ws.cell(r, 5).value
        raw_is_el = ws.cell(r, 6).value

        if not raw_sku and not raw_name_th and not raw_name_en:
            continue

        sku_str = str(raw_sku).strip() if raw_sku else f"AUTO-{r}"
        name_th = str(raw_name_th).strip() if raw_name_th else sku_str
        name_en = str(raw_name_en).strip() if raw_name_en else name_th

        # Handle duplicates in Excel source
        if sku_str in seen_skus:
            sku_str = f"{sku_str}-{r}"
        seen_skus.add(sku_str)

        numeric_clean = sku_str.replace("-", "").replace(" ", "").upper()

        # Determine EL status and Botanical info
        is_el_rule = (raw_is_el == "EL") or sku_str.startswith("EL") or (sku_str in BOTANICAL_MAP)
        control_status = ControlStatus.EL if is_el_rule else ControlStatus.COMBINABLE
        if is_el_rule:
            el_count += 1

        bot_family, prod_type, _ = BOTANICAL_MAP.get(sku_str, (None, None, None))
        if not prod_type:
            # Heuristic category mapping
            if sku_str.startswith("22") or any(
                f in name_th
                for f in ["มะละกอ", "ทุเรียน", "มังคุด", "แก้วมังกร", "เงาะ", "ลำไย", "กล้วย", "มะม่วง"]
            ):
                prod_type = ProduceType.FRUIT
                weight_class = WeightClass.HEAVY
            elif sku_str.startswith("12") or "เห็ด" in name_th:
                prod_type = ProduceType.HERB
                weight_class = WeightClass.LIGHT
            elif any(r_word in name_th for r_word in ["ตะไคร้", "ข่า", "ขิง", "กระชาย", "ราก"]):
                prod_type = ProduceType.ROOT
                weight_class = WeightClass.LIGHT
            elif any(
                h_word in name_th
                for h_word in ["ผัก", "ใบ", "ยอด", "กะเพรา", "โหระพา", "สะระแหน่", "ผักชี"]
            ):
                prod_type = (
                    ProduceType.HERB
                    if any(h in name_en.lower() for h in ["basil", "mint", "coriander", "celery"])
                    else ProduceType.LEAFY
                )
                weight_class = WeightClass.LIGHT
            else:
                prod_type = ProduceType.LEAFY
                weight_class = WeightClass.LIGHT
        else:
            weight_class = WeightClass.LIGHT

        box_material = (
            BoxMaterial.FOAM
            if (
                prod_type in [ProduceType.HERB, ProduceType.LEAFY]
                or "เห็ด" in name_th
                or sku_str.startswith("22")
            )
            else BoxMaterial.CARDBOARD
        )

        # Default pack size
        pack_size = "100g"
        for s in [
            "1000 g",
            "1000g",
            "1 kg",
            "1kg",
            "500 g",
            "500g",
            "200 g",
            "200g",
            "100 g",
            "100g",
        ]:
            if s in name_th.lower() or s in name_en.lower():
                pack_size = s.replace(" ", "")
                break

        # Match strictly by t_code to preserve official Sunny SKU codes
        existing = db.query(Product).filter(Product.t_code == sku_str).first()

        if not existing:
            prod = Product(
                t_code=sku_str,
                numeric_code=numeric_clean,
                name_vi=name_en or name_th,
                name_en=name_en,
                name_th=name_th,
                box_material=box_material,
                temperature=TemperatureZone.COLD,
                control_status=control_status,
                produce_type=prod_type,
                weight_class=weight_class,
                botanical_family=bot_family,
                default_pack_sizes=[pack_size, "100g", "200g", "500g", "1kg"],
                default_unit_volume_cm3=1000.0,
                default_buffer_percentage=12.0 if is_el_rule else 8.0,
                is_active=True,
            )
            db.add(prod)
            created_count += 1
        else:
            existing.numeric_code = numeric_clean
            existing.name_th = name_th
            if name_en:
                existing.name_en = name_en
            existing.control_status = control_status
            if bot_family:
                existing.botanical_family = bot_family
            updated_count += 1

    db.commit()
    total_prods = db.query(Product).count()
    print(f"  ✓ Added {created_count} new SKUs, updated {updated_count} existing.")
    print(f"  ✓ Total SKUs in Database: {total_prods} (EL Controlled: {el_count})")

    # 3. Apply Norway SKU Mappings & Barcodes
    print("\n[3/3] Linking 20 Norway Customer Codes & Barcodes to NGA Customer Profile...")
    mapped_count = 0
    for nw in NORWAY_MAPPINGS:
        target_sku = nw["target_sku"]
        nw_code = nw["code"]
        nw_barcode = nw["barcode"]
        nw_name = nw["th"]

        prod = db.query(Product).filter(Product.t_code == target_sku).first()
        if not prod:
            # Fallback by exact Thai name
            prod = db.query(Product).filter(Product.name_th == nw_name).first()

        if prod:
            prod.norway_code = nw_code
            prod.barcode = nw_barcode

            # Add to ClientSkuMapping for NGA
            mapping = (
                db.query(ClientSkuMapping)
                .filter(
                    ClientSkuMapping.main_customer_id == nga_main.id,
                    ClientSkuMapping.client_sku_code == nw_code,
                )
                .first()
            )

            if not mapping:
                mapping = ClientSkuMapping(
                    main_customer_id=nga_main.id,
                    client_sku_code=nw_code,
                    client_raw_name=nw_name,
                    product_id=prod.id,
                    standard_pack_size="100g",
                    is_verified=True,
                )
                db.add(mapping)
            else:
                mapping.product_id = prod.id
                mapping.client_raw_name = nw_name

            mapped_count += 1
            print(
                f"  ✓ Linked Norway Code {nw_code} (Barcode {nw_barcode}) -> {prod.t_code} ({prod.name_th} / {prod.name_en})"
            )
        else:
            print(f"  ⚠ Warning: Could not locate product for Norway item {nw_name} ({target_sku})")

    db.commit()
    print(f"\n✓ Successfully registered {mapped_count} Norway customer SKU mappings.")
    print("=" * 80)
    print("MASTER PRODUCT INGESTION COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    seed_thai_master_products()
