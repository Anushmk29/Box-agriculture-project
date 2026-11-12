import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    Customer,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.models.user import User, UserRole


@pytest.mark.parametrize("category", ["VEGETABLE", "SPICE", "PACKAGED_FOOD", "PERSONAL_CARE", "OTHER", None])
def test_create_non_produce_product_category(db_session, category):
    user = User(email="category@example.com", password_hash="unused", full_name="Category tester", role=UserRole.GENERAL)
    db_session.add(user)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        payload = {"t_code": "CATEGORY-QA", "name_en": "Category test item", "box_material": "CARDBOARD", "temperature": "NORMAL", "default_pack_sizes": ["250g"]}
        if category is not None:
            payload["produce_type"] = category
        response = client.post("/api/v3/master-data/products", json=payload, headers={"Authorization": "Bearer " + create_access_token({"sub": user.id})})
        assert response.status_code == 201, response.text
        assert response.json()["produce_type"] == (category or "OTHER")
        assert db_session.query(Product).one().produce_type.value == (category or "OTHER")
    finally:
        app.dependency_overrides.clear()


def test_product_search_and_main_customer_filter_are_complete(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    user = User(
        email="catalog@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Catalog User",
        role=UserRole.GENERAL,
    )
    products = [
        Product(
            t_code=f"T{index:03d}",
            name_vi=f"Vietnamese {index}",
            name_en="Mango Special" if index == 119 else f"Product {index}",
            name_th=f"Thai {index}",
            box_material=BoxMaterial.CARDBOARD,
            temperature=TemperatureZone.NORMAL,
            control_status=ControlStatus.COMBINABLE,
            produce_type=ProduceType.FRUIT,
            weight_class=WeightClass.LIGHT,
            default_pack_sizes=["100g"],
        )
        for index in range(120)
    ]
    main = Customer(
        client_code="MAIN",
        company_name="Main Customer",
        billing_email="main@example.com",
        route_group="TETE",
        destination_country="France",
        is_main_customer=True,
    )
    child = Customer(
        client_code="CHILD",
        company_name="Child Customer",
        billing_email="child@example.com",
        route_group="TETE",
        destination_country="France",
        is_main_customer=False,
    )
    db_session.add_all([user, main, child, *products])
    db_session.commit()
    client = TestClient(app)
    assert (
        client.post(
            "/api/v3/auth/login",
            json={"email": user.email, "password": "correct horse battery staple"},
        ).status_code
        == 200
    )

    found = client.get("/api/v3/master-data/products?q=mango&limit=20")
    main_customers = client.get("/api/v3/master-data/customers?main_only=true")

    assert found.status_code == 200
    assert [item["t_code"] for item in found.json()] == ["T119"]
    assert main_customers.status_code == 200
    assert [item["client_code"] for item in main_customers.json()] == ["MAIN"]
    assert main_customers.json()[0]["is_main_customer"] is True
    app.dependency_overrides.clear()


def test_create_product_and_smart_search(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    user = User(
        email="ops@example.com",
        password_hash=hash_password("secret-pass-123"),
        full_name="Operations Staff",
        role=UserRole.GENERAL,
    )
    db_session.add(user)
    db_session.commit()

    client = TestClient(app)
    assert (
        client.post(
            "/api/v3/auth/login",
            json={"email": user.email, "password": "secret-pass-123"},
        ).status_code
        == 200
    )

    # 1. Create a brand new product
    payload = {
        "t_code": "11-099-NEW",
        "name_en": "RED LOTUS SHOOT",
        "name_vi": "Ngó sen đỏ",
        "name_th": "ไหลบัวแดง",
        "produce_type": "LEAFY",
        "box_material": "FOAM",
        "temperature": "COLD",
        "control_status": "COMBINABLE",
        "weight_class": "LIGHT",
        "default_pack_sizes": ["200g", "500g"],
    }
    resp = client.post("/api/v3/master-data/products", json=payload)
    assert resp.status_code == 201
    created = resp.json()
    assert created["t_code"] == "11-099-NEW"
    assert created["name_en"] == "RED LOTUS SHOOT"
    assert created["name_vi"] == "Ngó sen đỏ"
    assert created["box_material"] == "FOAM"
    assert created["temperature"] == "COLD"
    assert created["produce_type"] == "LEAFY"

    # 2. Duplicate t_code should fail with 400
    dup_resp = client.post("/api/v3/master-data/products", json=payload)
    assert dup_resp.status_code == 400
    assert "already exists" in dup_resp.json()["detail"]

    # 3. Test unaccented search ("Ngo sen do" matches "Ngó sen đỏ")
    unaccented_search = client.get("/api/v3/master-data/products?q=Ngo sen do")
    assert unaccented_search.status_code == 200
    matched_codes = [p["t_code"] for p in unaccented_search.json()]
    assert "11-099-NEW" in matched_codes

    # 4. Test token search ("LOTUS" matches "RED LOTUS SHOOT")
    token_search = client.get("/api/v3/master-data/products?q=LOTUS")
    assert token_search.status_code == 200
    matched_codes = [p["t_code"] for p in token_search.json()]
    assert "11-099-NEW" in matched_codes

    # 5. Sku Mapping with this newly created product
    mapping_payload = {
        "client_sku_code": "CUSTOM-1109",
        "client_raw_name": "Ngó sen đỏ tươi",
        "product_id": created["id"],
        "standard_pack_size": "200g",
    }
    map_resp = client.post("/api/v3/master-data/sku-mappings", json=mapping_payload)
    assert map_resp.status_code == 200
    mapped = map_resp.json()
    assert mapped["client_sku_code"] == "CUSTOM-1109"
    assert mapped["product_id"] == created["id"]
    assert mapped["is_verified"] is True

    app.dependency_overrides.clear()

