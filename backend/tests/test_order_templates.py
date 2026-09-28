from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.user import User, UserRole


def authenticated_client(db_session):
    user = User(
        email="templates@example.com",
        password_hash="unused",
        full_name="Template User",
        role=UserRole.GENERAL,
    )
    db_session.add(user)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {create_access_token({'sub': user.id})}"
    return client


def test_order_templates_require_authentication():
    client = TestClient(app)
    assert client.get("/api/v3/orders/templates").status_code == 401
    assert client.get("/api/v3/orders/templates/group-matrix").status_code == 401


def test_list_order_templates(db_session):
    client = authenticated_client(db_session)
    response = client.get("/api/v3/orders/templates")
    assert response.status_code == 200
    templates = response.json()
    assert isinstance(templates, list)
    assert len(templates) >= 2

    ids = [t["id"] for t in templates]
    assert "group-matrix" in ids
    assert "single-customer" in ids

    for t in templates:
        assert "name" in t
        assert "filename" in t
        assert t["filename"].endswith(".xlsx")
        assert "download_url" in t
        assert t["file_size_bytes"] > 0
    app.dependency_overrides.clear()


def test_download_group_matrix_template(db_session):
    client = authenticated_client(db_session)
    response = client.get("/api/v3/orders/templates/group-matrix")
    assert response.status_code == 200
    assert "spreadsheetml.sheet" in response.headers.get("content-type", "")
    assert 'filename="Group_Order_Matrix_Template.xlsx"' in response.headers.get("content-disposition", "")
    assert len(response.content) > 10000
    app.dependency_overrides.clear()


def test_download_single_customer_template(db_session):
    client = authenticated_client(db_session)
    response = client.get("/api/v3/orders/templates/single-customer")
    assert response.status_code == 200
    assert "spreadsheetml.sheet" in response.headers.get("content-type", "")
    assert 'filename="Single_Customer_Order_Template.xlsx"' in response.headers.get("content-disposition", "")
    assert len(response.content) > 10000
    app.dependency_overrides.clear()


def test_download_nonexistent_template_404(db_session):
    client = authenticated_client(db_session)
    response = client.get("/api/v3/orders/templates/unknown-template-xyz")
    assert response.status_code == 404
    app.dependency_overrides.clear()
