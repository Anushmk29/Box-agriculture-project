"""
Production Master Data Seeding Script
Reads 'documents/new docs/Order Tonio 01.09.26 FULL.xlsx' and sets up:
1. Main Customer Profile: Tonio Trade Europe BV (TONIO-MAIN)
2. 104 Sub-Customer Profiles mapped to their Route Groups (GROUP CT, GROUP BE, GROUP PTT, GROUP TA, etc.)
3. 89 Fresh Produce SKUs with Vietnamese, English, and Thai descriptions + 12% default buffer
4. CustomerSellingPrice Table pre-seeded with Tonio agreed selling rates (€4.25/pack herbs)
"""

import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

db_file = os.path.join(backend_dir, "fresh_produce_dev.db").replace("\\", "/")
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"

from app.core.database import Base, SessionLocal, engine
from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    Customer,
    CustomerSellingPrice,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.services.matrix_parser import MatrixOrderParser


def seed_production_data():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    project_root = os.path.dirname(backend_dir)
    excel_path = os.path.join(
        project_root, "documents", "new docs", "Order Tonio 01.09.26 FULL.xlsx"
    )

    if not os.path.exists(excel_path):
        print(f"Error: Excel file not found at {excel_path}")
        return

    print("=" * 80)
    print("SEEDING PRODUCTION MASTER DATA FROM 'Order Tonio 01.09.26 FULL.xlsx'")
    print("=" * 80)

    # 1. Main Customer Setup
    print("\n[1/4] Setting up Main Customer: Tonio Trade Europe BV...")
    main_cust = db.query(Customer).filter(Customer.client_code == "TONIO-MAIN").first()
    if not main_cust:
        main_cust = Customer(
            client_code="TONIO-MAIN",
            company_name="Tonio Trade Europe BV",
            billing_email="orders@toniotrade.eu",
            route_group="TETE",
            destination_country="France",
            is_main_customer=True,
        )
        db.add(main_cust)
        db.flush()
        print("  ✓ Created TONIO-MAIN corporate record.")
    else:
        main_cust.is_main_customer = True
        print("  ✓ Existing TONIO-MAIN record verified.")

    # 2. Inspect Excel to discover all shops and their route groups
    print("\n[2/4] Discovering Sub-Customer Shops & Route Groups across Excel sheets...")
    parser = MatrixOrderParser(excel_path)
    ack_info = parser.inspect_sub_customers()
    parsed_full = parser.parse_all_clients()

    sub_details = ack_info.get("sub_customer_details", [])
    registered_shops = 0

    for det in sub_details:
        code = det["client_code"]
        grp = det["route_group"]

        cust = db.query(Customer).filter(Customer.client_code == code).first()
        clean_email = f"{code.lower().replace(' ', '_').replace('-', '_')}@clienttrade.eu"

        if not cust:
            cust = Customer(
                client_code=code,
                company_name=f"Sub-Customer {code}",
                billing_email=clean_email,
                route_group=grp,
                destination_country="France",
                is_main_customer=False,
                parent_customer_id=main_cust.id,
            )
            db.add(cust)
            registered_shops += 1
        else:
            cust.route_group = grp
            cust.parent_customer_id = main_cust.id

    db.commit()
    total_in_db = db.query(Customer).filter(Customer.is_main_customer.is_(False)).count()
    print(
        f"  ✓ Processed {len(sub_details)} shops across {len(ack_info.get('group_summary', {}))} groups."
    )
    print(f"  ✓ Total Sub-Customers in Database: {total_in_db}")

    # 3. Seed Produce SKUs
    print("\n[3/4] Registering Produce Master Products...")
    catalog = parsed_full.get("products_catalog", {})
    products_created = 0

    for t_code, pdata in catalog.items():
        existing_prod = db.query(Product).filter(Product.t_code == t_code).first()

        name_vi = pdata.get("name_vi", "")
        name_en = pdata.get("name_en", "")
        name_th = pdata.get("name_th", "")
        pack_size = pdata.get("pack_size", "100g")

        # Determine produce type and default buffer
        is_herb = any(
            h in name_en.lower() or h in name_vi.lower()
            for h in ["basil", "leaf", "coriander", "quế", "ngò", "lốt", "herb"]
        )
        buffer_pct = 12.00 if is_herb else 8.00

        if not existing_prod:
            new_prod = Product(
                t_code=t_code,
                name_vi=name_vi or t_code,
                name_en=name_en or t_code,
                name_th=name_th or name_en or t_code,
                box_material=BoxMaterial.FOAM if is_herb else BoxMaterial.CARDBOARD,
                temperature=TemperatureZone.COLD,
                control_status=ControlStatus.COMBINABLE,
                produce_type=ProduceType.HERB if is_herb else ProduceType.LEAFY,
                weight_class=WeightClass.LIGHT if is_herb else WeightClass.HEAVY,
                default_pack_sizes=[pack_size, "100g", "200g", "500g", "1kg"],
                default_buffer_percentage=buffer_pct,
            )
            db.add(new_prod)
            products_created += 1
        else:
            if name_th and not existing_prod.name_th:
                existing_prod.name_th = name_th
            existing_prod.default_buffer_percentage = buffer_pct

    db.commit()
    total_prods = db.query(Product).count()
    print(f"  ✓ Added {products_created} new SKUs. Total SKUs in Catalog: {total_prods}")

    # 4. Seed Tonio Selling Price Matrix (€4.25 for herbs, €3.80 for vegetables)
    print("\n[4/4] Seeding Main Customer Agreed Selling Prices (CustomerSellingPrice)...")
    prices_created = 0
    all_products = db.query(Product).all()

    for p in all_products:
        is_herb = (p.produce_type == ProduceType.HERB) or any(
            h in p.name_en.lower() for h in ["basil", "leaf", "coriander"]
        )
        selling_rate = 4.25 if is_herb else 3.80

        existing_price = (
            db.query(CustomerSellingPrice)
            .filter(
                CustomerSellingPrice.main_customer_id == main_cust.id,
                CustomerSellingPrice.product_id == p.id,
                CustomerSellingPrice.sub_customer_id.is_(None),
            )
            .first()
        )

        if not existing_price:
            csp = CustomerSellingPrice(
                main_customer_id=main_cust.id,
                sub_customer_id=None,  # Global rate for all Tonio sub-customers
                product_id=p.id,
                pack_size="100g",
                selling_price_eur=selling_rate,
                valid_from="2026-01-01",
            )
            db.add(csp)
            prices_created += 1

    db.commit()
    total_prices = (
        db.query(CustomerSellingPrice)
        .filter(CustomerSellingPrice.main_customer_id == main_cust.id)
        .count()
    )
    print(f"  ✓ Added {prices_created} selling price entries. Total Price Rules: {total_prices}")

    print("\n" + "=" * 80)
    print("PRODUCTION MASTER DATA SEEDING COMPLETE!")
    print("• Main Customer: Tonio Trade Europe BV")
    print(f"• Sub-Customer Shops: {total_in_db} shops (Pre-tagged with Route Groups)")
    print(f"• Produce Catalog: {total_prods} products (Thai/Viet/English + 12% buffer)")
    print("• Selling Prices: €4.25/pack standard on-behalf rate")
    print("=" * 80)


if __name__ == "__main__":
    seed_production_data()
