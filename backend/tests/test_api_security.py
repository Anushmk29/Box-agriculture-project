from datetime import date

import jwt
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import decode_access_token, hash_password
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
from app.models.order import RawOrderFile
from app.models.user import User, UserRole


def client_for(db_session, *, raise_server_exceptions: bool = True):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_login_returns_standard_signed_jwt(db_session):
    user = User(
        email="manager@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Operations Manager",
        role=UserRole.OPERATIONS_MANAGER,
    )
    db_session.add(user)
    db_session.commit()
    client = client_for(db_session)

    response = client.post(
        "/api/v3/auth/login",
        json={"email": user.email, "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    assert len(token.split(".")) == 3
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert decode_access_token(token)["sub"] == user.id
    assert response.cookies.get("access_token") == token
    app.dependency_overrides.clear()


def test_http_only_session_cookie_authenticates_and_logout_clears_it(db_session):
    user = User(
        email="cookie-user@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Cookie User",
        role=UserRole.OPERATIONS_MANAGER,
    )
    db_session.add(user)
    db_session.commit()
    client = client_for(db_session)

    login = client.post(
        "/api/v3/auth/login",
        json={"email": user.email, "password": "correct horse battery staple"},
    )
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert client.get("/api/v3/auth/me").json()["role"] == UserRole.OPERATIONS_MANAGER.value
    assert client.get("/api/v3/master-data/products").status_code == 200

    logout = client.post("/api/v3/auth/logout")
    assert logout.status_code == 204
    assert client.get("/api/v3/master-data/products").status_code == 401
    app.dependency_overrides.clear()


def test_procurement_confirmation_requires_authentication(db_session):
    client = client_for(db_session)

    response = client.put(
        "/api/v3/procurement/batches/not-found/confirm",
        json={"items": []},
    )

    assert response.status_code == 401
    app.dependency_overrides.clear()


def test_service_principal_cannot_confirm_procurement_batch(db_session):
    service_user = User(
        email="procurement-bot@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Procurement Bot",
        role=UserRole.PROCUREMENT_BUYER,
        is_service_principal=True,
    )
    db_session.add(service_user)
    db_session.commit()
    client = client_for(db_session, raise_server_exceptions=False)
    token = client.post(
        "/api/v3/auth/login",
        json={"email": service_user.email, "password": "correct horse battery staple"},
    ).json()["access_token"]

    response = client.put(
        "/api/v3/procurement/batches/not-found/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "items": [
                {
                    "item_id": "not-found",
                    "purchased_pack_count": 0,
                    "purchased_weight_kg": 0,
                }
            ]
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Your role is not permitted to perform this operation"
    app.dependency_overrides.clear()


def test_accounting_level_includes_general_procurement_access(db_session):
    accounting_user = User(
        email="accounting-only@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Accounting Only",
        role=UserRole.ACCOUNTING_OFFICER,
    )
    db_session.add(accounting_user)
    db_session.commit()
    client = client_for(db_session, raise_server_exceptions=False)
    token = client.post(
        "/api/v3/auth/login",
        json={"email": accounting_user.email, "password": "correct horse battery staple"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    consolidate = client.post(
        "/api/v3/procurement/not-found/consolidate",
        headers=headers,
    )
    combine = client.post(
        "/api/v3/procurement/combine-and-consolidate",
        headers=headers,
        json={"raw_file_ids": ["not-found"]},
    )
    buffer_update = client.put(
        "/api/v3/procurement/items/not-found/buffer",
        headers=headers,
        json={"buffer_percentage": 10},
    )

    assert consolidate.status_code == 404
    assert combine.status_code == 400
    assert buffer_update.status_code == 404
    app.dependency_overrides.clear()


def test_procurement_combine_rejects_unknown_order_sources(db_session):
    manager = User(
        email="combine-manager@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Combine Manager",
        role=UserRole.OPERATIONS_MANAGER,
    )
    db_session.add(manager)
    db_session.commit()
    client = client_for(db_session, raise_server_exceptions=False)
    token = client.post(
        "/api/v3/auth/login",
        json={"email": manager.email, "password": "correct horse battery staple"},
    ).json()["access_token"]

    response = client.post(
        "/api/v3/procurement/combine-and-consolidate",
        headers={"Authorization": f"Bearer {token}"},
        json={"raw_file_ids": ["not-found"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Raw order files not found: not-found"
    app.dependency_overrides.clear()


def test_procurement_auto_reference_does_not_collide_for_different_sources(db_session):
    manager = User(
        email="batch-reference-manager@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Batch Reference Manager",
        role=UserRole.OPERATIONS_MANAGER,
    )
    main_customer = Customer(
        client_code="MAIN-BATCH-REF",
        company_name="Batch Reference Customer",
        billing_email="batch-reference@example.com",
        route_group="TETE",
        destination_country="Thailand",
        is_main_customer=True,
    )
    db_session.add_all([manager, main_customer])
    db_session.flush()
    first_raw = RawOrderFile(
        file_name="first.xlsx",
        storage_url="first.xlsx",
        shipment_target_date="2026-09-18",
        route_line="TETE",
        uploaded_by=manager.id,
        main_customer_id=main_customer.id,
    )
    second_raw = RawOrderFile(
        file_name="second.xlsx",
        storage_url="second.xlsx",
        shipment_target_date="2026-09-18",
        route_line="TETE",
        uploaded_by=manager.id,
        main_customer_id=main_customer.id,
    )
    db_session.add_all([first_raw, second_raw])
    db_session.commit()
    client = client_for(db_session)
    token = client.post(
        "/api/v3/auth/login",
        json={"email": manager.email, "password": "correct horse battery staple"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(
        "/api/v3/procurement/combine-and-consolidate",
        headers=headers,
        json={"raw_file_ids": [first_raw.id], "shipment_date": "2026-09-18"},
    )
    second = client.post(
        "/api/v3/procurement/combine-and-consolidate",
        headers=headers,
        json={"raw_file_ids": [second_raw.id], "shipment_date": "2026-09-18"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["batch_id"] != second.json()["batch_id"]
    assert first.json()["batch_reference"] != second.json()["batch_reference"]
    app.dependency_overrides.clear()


def test_phase_one_and_three_routes_require_authentication(db_session):
    client = client_for(db_session)

    consolidate = client.post("/api/v3/procurement/not-found/consolidate")
    packing_list = client.get("/api/v3/packing/orders/not-found/packing-list")
    invoice = client.get("/api/v3/invoicing/not-found")
    invoice_html = client.get("/api/v3/invoicing/not-found/html")
    buyer_sheet = client.get("/api/v3/procurement/batches/not-found/buyer-sheet")

    assert consolidate.status_code == 401
    assert packing_list.status_code == 401
    assert invoice.status_code == 401
    assert invoice_html.status_code == 401
    assert buyer_sheet.status_code == 401
    app.dependency_overrides.clear()


def test_master_data_and_order_details_require_authentication(db_session):
    client = client_for(db_session)

    products = client.get("/api/v3/master-data/products")
    mappings = client.get("/api/v3/master-data/sku-mappings")
    create_mapping = client.post(
        "/api/v3/master-data/sku-mappings",
        json={
            "client_raw_name": "Untrusted",
            "product_id": "not-found",
        },
    )
    selling_price = client.post(
        "/api/v3/master-data/sku-mappings/selling-prices",
        json={
            "main_customer_id": "not-found",
            "product_id": "not-found",
            "pack_size": "100g",
            "selling_price_eur": 1.0,
        },
    )
    client_orders = client.get("/api/v3/orders/not-found/client-orders")
    operations = client.get("/api/v3/operations/summary")

    assert products.status_code == 401
    assert mappings.status_code == 401
    assert create_mapping.status_code == 401
    assert selling_price.status_code == 401
    assert client_orders.status_code == 401
    assert operations.status_code == 401
    app.dependency_overrides.clear()


def test_order_upload_rejects_non_excel_file_without_server_error(db_session):
    user = User(
        email="customer-agent@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Customer Agent",
        role=UserRole.CUSTOMER_AGENT,
    )
    db_session.add(user)
    db_session.commit()
    client = client_for(db_session)
    token = client.post(
        "/api/v3/auth/login",
        json={"email": user.email, "password": "correct horse battery staple"},
    ).json()["access_token"]

    response = client.post(
        "/api/v3/orders/upload-matrix",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("notes.md", b"not a workbook", "text/markdown")},
        data={"shipment_date": "2026-09-20", "route_line": "TETE"},
    )

    assert response.status_code == 422
    assert "Excel" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_order_upload_requires_explicit_shipment_and_route(db_session):
    user = User(
        email="dated-upload@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Dated Upload User",
        role=UserRole.CUSTOMER_AGENT,
    )
    main_customer = Customer(
        client_code="MAIN-DATED",
        company_name="Main Dated Customer",
        billing_email="billing@example.com",
        route_group="TETE",
        destination_country="Thailand",
        is_main_customer=True,
    )
    db_session.add_all([user, main_customer])
    db_session.commit()
    client = client_for(db_session)
    token = client.post(
        "/api/v3/auth/login",
        json={"email": user.email, "password": "correct horse battery staple"},
    ).json()["access_token"]

    response = client.post(
        "/api/v3/orders/upload-matrix",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "orders.xlsx",
                b"not important because request validation must run first",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"main_customer_id": main_customer.id},
    )

    assert response.status_code == 422
    missing_fields = {item["loc"][-1] for item in response.json()["detail"]}
    assert missing_fields == {"shipment_date", "route_line"}
    app.dependency_overrides.clear()


def test_selling_price_defaults_to_the_current_date(db_session):
    user = User(
        email="pricing-accountant@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Pricing Accountant",
        role=UserRole.ACCOUNTING_OFFICER,
    )
    main_customer = Customer(
        client_code="MAIN-PRICE",
        company_name="Main Price Customer",
        billing_email="pricing@example.com",
        route_group="TETE",
        destination_country="Thailand",
        is_main_customer=True,
    )
    product = Product(
        t_code="T-PRICE",
        name_vi="Rau",
        name_en="Produce",
        name_th="ผัก",
        box_material=BoxMaterial.FOAM,
        temperature=TemperatureZone.COLD,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.LEAFY,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["100g"],
    )
    db_session.add_all([user, main_customer, product])
    db_session.commit()
    client = client_for(db_session)
    token = client.post(
        "/api/v3/auth/login",
        json={"email": user.email, "password": "correct horse battery staple"},
    ).json()["access_token"]

    response = client.post(
        "/api/v3/master-data/sku-mappings/selling-prices",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "main_customer_id": main_customer.id,
            "product_id": product.id,
            "pack_size": "100g",
            "selling_price_eur": 3.25,
        },
    )

    assert response.status_code == 200
    assert response.json()["valid_from"] == date.today().isoformat()

    invalid_range = client.post(
        "/api/v3/master-data/sku-mappings/selling-prices",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "main_customer_id": main_customer.id,
            "product_id": product.id,
            "pack_size": "100g",
            "selling_price_eur": 3.25,
            "valid_from": "2026-10-01",
            "valid_to": "2026-09-01",
        },
    )
    assert invalid_range.status_code == 422
    app.dependency_overrides.clear()


def test_manifest_api_does_not_default_to_a_specific_flight(db_session):
    client = client_for(db_session)

    operation = client.get("/openapi.json").json()["paths"]["/api/v3/packing/manifests/sync"][
        "post"
    ]
    carrier_parameter = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "flight_carrier"
    )

    assert "default" not in carrier_parameter["schema"]
    app.dependency_overrides.clear()


def test_staff_has_universal_operational_access(db_session):
    staff_user = User(
        email="operations-staff@example.com",
        password_hash=hash_password("Password123!"),
        full_name="Operations Staff",
        role=UserRole.STAFF,
        is_service_principal=False,
    )
    db_session.add(staff_user)
    db_session.commit()

    client = client_for(db_session, raise_server_exceptions=False)
    login_resp = client.post(
        "/api/v3/auth/login",
        json={"email": staff_user.email, "password": "Password123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me_resp = client.get("/api/v3/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == UserRole.STAFF.value

    # Staff can access procurement endpoints without 403
    buffer_resp = client.put(
        "/api/v3/procurement/items/dummy-item-id/buffer",
        headers=headers,
        json={"buffer_kg": 5.0},
    )
    assert buffer_resp.status_code != 403

    # Staff can access SKU mapping endpoints without 403
    sku_resp = client.get("/api/v3/master-data/sku-mappings", headers=headers)
    assert sku_resp.status_code == 200

    app.dependency_overrides.clear()
