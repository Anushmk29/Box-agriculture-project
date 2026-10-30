from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models.order import OrderStatus
from app.models.procurement import MasterPurchasingList
from app.models.user import User, UserRole


def test_latest_procurement_batch_returns_null_when_empty(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: User(
        email="buyer@example.com",
        password_hash="unused",
        full_name="Produce Buyer",
        role=UserRole.GENERAL,
    )
    try:
        response = TestClient(app).get("/api/v3/procurement/latest-batch")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() is None


def test_latest_procurement_batch_prefers_pending_work(db_session):
    pending = MasterPurchasingList(
        batch_reference="PUR-PENDING",
        total_skus_count=0,
        status=OrderStatus.PURCHASING_CONSOLIDATED,
    )
    completed = MasterPurchasingList(
        batch_reference="PUR-COMPLETED",
        total_skus_count=0,
        status=OrderStatus.HANDOFF_01_SENT,
    )
    db_session.add_all([pending, completed])
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: User(
        email="buyer@example.com",
        password_hash="unused",
        full_name="Produce Buyer",
        role=UserRole.GENERAL,
    )
    try:
        response = TestClient(app).get("/api/v3/procurement/latest-batch")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["batch_reference"] == "PUR-PENDING"
