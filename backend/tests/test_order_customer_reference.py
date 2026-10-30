import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models.master_data import Customer
from app.models.user import User, UserRole
from app.services.order_customer_reference import import_reference_customers, order_routes


def test_import_reuses_existing_accounts_and_is_idempotent(db_session):
    existing = Customer(client_code="TONIO-MAIN", company_name="Tonio Trade Europe BV",
                        route_group="TETE", destination_country="France", billing_email="existing@example.com",
                        is_main_customer=True)
    # A shop with the same name must not become the billing account.
    shop = Customer(client_code="DTD05", company_name="DAN", route_group="DTD",
                    destination_country="", billing_email="", is_main_customer=False)
    db_session.add_all([existing, shop])
    db_session.flush()
    results = import_reference_customers(db_session)
    assert sum(row["action"] == "created" for row in results) == 8
    assert db_session.query(Customer).filter(Customer.is_main_customer.is_(True)).count() == 9
    assert existing.company_name == "Tonio Trade Europe BV"
    assert existing.billing_email == "existing@example.com"
    assert not shop.is_main_customer
    dan = db_session.query(Customer).filter_by(client_code="DTD-MAIN").one()
    assert (dan.company_name, dan.route_group) == ("DAN", "DTD")
    assert dan.destination_country == dan.billing_email == ""
    assert all(row["action"] == "kept" for row in import_reference_customers(db_session))
    assert "PRD" in order_routes(db_session)
    assert not db_session.query(Customer).filter_by(route_group="PRD").count()


def test_ambiguous_accounts_do_not_create_any_reference_customers(db_session):
    for code in ["DAN", "DTD-MAIN"]:
        db_session.add(Customer(client_code=code, company_name=code, route_group="DTD",
                                destination_country="", billing_email="", is_main_customer=True))
    db_session.flush()
    with pytest.raises(ValueError, match="Multiple billing accounts"):
        import_reference_customers(db_session)
    assert db_session.query(Customer).count() == 2


def test_routes_and_imported_accounts_are_available_to_order_intake(db_session):
    import_reference_customers(db_session)
    db_session.add(Customer(client_code="EXISTING", company_name="Existing account", route_group=" extra ",
                            destination_country="", billing_email="", is_main_customer=True))
    db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: User(
        role=UserRole.GENERAL, is_service_principal=False, must_change_password=False)
    try:
        client = TestClient(app)
        response = client.get("/api/v3/master-data/order-routes")
        assert response.status_code == 200
        assert response.json() == ["CD", "DTD", "EXTRA", "ITM", "JCT", "LYT", "MLD", "NGA", "PARIS", "PRD", "TETE"]
        register = client.get("/api/v3/master-data/customer-register?q=DAN&sort=name&direction=asc").json()
        assert register["total"] == 1
        assert register["items"][0]["route_group"] == "DTD"
    finally:
        app.dependency_overrides.clear()


def test_route_options_require_authentication():
    assert TestClient(app).get("/api/v3/master-data/order-routes").status_code == 401
