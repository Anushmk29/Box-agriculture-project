import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models.master_data import Customer
from app.models.order import RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingOrderSource
from app.models.user import User, UserRole


def test_batch_picker_searches_source_client_file_date_and_paginates(db_session):
    buyer = User(email="picker@example.com", password_hash="unused", full_name="Buyer", role=UserRole.GENERAL)
    customer = Customer(company_name="Sunny Client", client_code="PICKER", billing_email="picker@example.com",
                        route_group="BKK", destination_country="Thailand")
    db_session.add_all([buyer, customer])
    db_session.flush()
    raw = RawOrderFile(file_name="sunny-orders.xlsx", storage_url="unused", shipment_target_date="2026-10-06",
                       route_line="BKK", uploaded_by=buyer.id, main_customer_id=customer.id)
    first = MasterPurchasingList(batch_reference="PUR-FIRST", total_skus_count=3)
    second = MasterPurchasingList(batch_reference="PUR-SECOND", total_skus_count=2)
    db_session.add_all([raw, first, second])
    db_session.flush()
    db_session.add(PurchasingOrderSource(purchasing_list_id=first.id, raw_order_file_id=raw.id))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: buyer
    try:
        client = TestClient(app)
        for term in ["Sunny Client", "sunny-orders", "2026-10-06", "BKK", "PUR-FIRST"]:
            response = client.get("/api/v3/procurement/batches", params={"q": term})
            assert response.status_code == 200
            result = response.json()
            assert [row["batch_id"] for row in result["items"]] == [first.id]
            assert result["items"][0]["customers"] == ["Sunny Client"]
            assert result["items"][0]["shipment_date"] == "2026-10-06"
        page_one = client.get("/api/v3/procurement/batches?limit=1").json()
        page_two = client.get("/api/v3/procurement/batches?limit=1&offset=1").json()
        assert page_one["has_more"] is True
        assert page_two["has_more"] is False
        assert page_one["items"][0]["batch_id"] != page_two["items"][0]["batch_id"]
        assert client.get("/api/v3/procurement/batches?q=missing").json()["items"] == []
        assert client.get("/api/v3/procurement/batches?q=%").json()["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_batch_picker_requires_authentication(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        assert TestClient(app).get("/api/v3/procurement/batches").status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("role", [UserRole.GENERAL, UserRole.GENERAL_ACCOUNTING])
def test_lower_roles_cannot_call_owner_endpoints_directly(db_session, role):
    actor = User(email="role-check@example.com", password_hash="unused", full_name="Role check", role=role)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        client = TestClient(app)
        assert client.get("/api/v3/admin/users").status_code == 403
        assert client.post("/api/v3/agents/handoff-01/dispatch", json={"batch_id": "missing"}).status_code == 403
        assert client.put("/api/v3/procurement/batches/missing/buffer", json={"buffer_percentage": 10}).status_code == 403
        assert client.post("/api/v3/master-data/products/merge", json={"source_id": "missing", "target_id": "other"}).status_code == 403
    finally:
        app.dependency_overrides.clear()
