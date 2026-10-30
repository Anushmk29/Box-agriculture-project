from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models.master_data import Customer
from app.models.user import User, UserRole


def test_create_main_customer_persists_and_rejects_duplicate_or_blank_codes(db_session):
    def override_db():
        yield db_session

    actor = User(role=UserRole.GENERAL, is_service_principal=False, must_change_password=False)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        client = TestClient(app)
        payload = {"client_code": " new-main ", "company_name": " New Customer ",
                   "route_group": "TETE", "destination_country": "France"}
        response = client.post("/api/v3/master-data/customers", json=payload)
        assert response.status_code == 201
        created = response.json()
        assert created["client_code"] == "NEW-MAIN"
        assert created["company_name"] == "New Customer"
        assert created["is_main_customer"] is True
        assert db_session.get(Customer, created["id"]).billing_email == ""
        accounts = client.get("/api/v3/master-data/customers?main_only=true").json()
        assert accounts[0]["id"] == created["id"]
        assert client.post("/api/v3/master-data/customers", json=payload).status_code == 409
        assert client.post("/api/v3/master-data/customers", json={**payload, "client_code": "   "}).status_code == 422
        assert db_session.query(Customer).count() == 1
    finally:
        app.dependency_overrides.clear()


def test_create_main_customer_requires_authentication():
    assert TestClient(app).post("/api/v3/master-data/customers", json={
        "client_code": "NEW", "company_name": "New", "route_group": "TETE",
        "destination_country": "France",
    }).status_code == 401
