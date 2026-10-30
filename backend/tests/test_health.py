from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app


def test_liveness_endpoint_is_process_only():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "HEALTHY"}


def test_readiness_endpoint_checks_the_database(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "READY", "database": "reachable", "storage": "local"}


def test_readiness_endpoint_fails_closed_when_database_is_unreachable():
    class BrokenSession:
        def execute(self, _statement):
            raise RuntimeError("database is down")

    def override_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Database readiness check failed",
    }
