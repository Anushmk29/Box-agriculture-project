import asyncio

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import database
from app.core.database import Base
from app.main import app
from app.models.audit import AuditLog
from app.models.master_data import Customer
from app.models.user import User, UserRole


@pytest.fixture()
def request_sessions(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides.clear()
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _customer(code):
    return Customer(
        client_code=code,
        company_name=code,
        billing_email=f"{code}@example.com",
        route_group="TETE",
        destination_country="France",
    )


def test_logout_commit_failure_does_not_claim_global_revocation(request_sessions, monkeypatch):
    from app.core.security import create_access_token

    with request_sessions() as seed:
        user = User(email="logout@example.invalid", password_hash="unused", full_name="Logout", role=UserRole.GENERAL)
        seed.add(user)
        seed.commit()
        identity = user.id
    token = create_access_token({"sub": identity, "session_version": 0})

    def failing_session():
        db = request_sessions()
        def fail():
            raise RuntimeError("Simulated logout commit failure")
        monkeypatch.setattr(db, "commit", fail)
        return db

    with monkeypatch.context() as patch:
        patch.setattr(database, "SessionLocal", failing_session)
        response = TestClient(app, raise_server_exceptions=False).post("/api/v3/auth/logout",
            headers={"Authorization": "Bearer " + token})
    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    with request_sessions() as observer:
        assert observer.get(User, identity).session_version == 0
        assert observer.query(AuditLog).filter_by(event_type="AUTH_LOGOUT_ALL").count() == 0
    assert TestClient(app).get("/api/v3/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 200


def test_request_commits_on_success_and_rolls_back_on_error(request_sessions):
    gen = database.get_db()
    next(gen).add(_customer("OK"))
    with pytest.raises(StopIteration):
        next(gen)

    gen = database.get_db()
    next(gen).add(_customer("FAILED"))
    with pytest.raises(ValueError):
        gen.throw(ValueError("route failed"))

    codes = {c.client_code for c in request_sessions().query(Customer)}
    assert codes == {"OK"}


def test_module_permission_error_is_403():
    @app.get("/__test__/permission")
    def _raise():
        raise PermissionError("Handoff 02 must be returned by Agent 2")

    try:
        response = TestClient(app).get("/__test__/permission")
    finally:
        app.router.routes.pop()
    assert response.status_code == 403
    assert response.json()["detail"] == "Handoff 02 must be returned by Agent 2"


def test_every_http_database_dependency_finishes_before_response():
    """Cover direct dependencies and nested authentication, without replacing get_db."""
    count = 0

    def check(dependency, path):
        nonlocal count
        if dependency.call is database.get_db:
            count += 1
            assert dependency.scope == "function", path
        for child in dependency.dependencies:
            check(child, path)

    for route in app.routes:
        if isinstance(route, APIRoute):
            check(route.dependant, route.path)
        elif hasattr(route, "effective_route_contexts"):
            # FastAPI 0.141 keeps included routers lazy rather than flattening them.
            for context in route.effective_route_contexts():
                if isinstance(context.original_route, APIRoute):
                    check(context.dependant, context.path)
    assert count > 100


@pytest.mark.parametrize("failure", [None, "commit", "service", "serialization", "bulk"])
def test_http_transaction_outcome_and_response_order(request_sessions, monkeypatch, failure):
    """Observe durable rows at ASGI response start, not just after TestClient returns."""
    class Result(BaseModel):
        saved: bool

    api = FastAPI()
    sessions = []
    commits = []
    outcomes = []

    def new_session():
        db = request_sessions()
        sessions.append(db)
        commit = db.commit

        def finish():
            commits.append("attempted")
            if failure == "commit":
                db.flush()
                raise RuntimeError("Simulated database commit failure")
            commit()

        monkeypatch.setattr(db, "commit", finish)
        return db

    monkeypatch.setattr(database, "SessionLocal", new_session)

    @api.post("/write", response_model=Result)
    def write(db=Depends(database.get_db, scope="function")):
        db.add(_customer("FIRST"))
        db.flush()
        if failure == "service":
            raise HTTPException(409, "Operation rejected")
        if failure == "bulk":
            db.add(_customer("SECOND"))
            db.flush()
            raise ValueError("Second item failed")
        if failure == "serialization":
            return {"saved": "not a boolean"}
        return {"saved": True}

    async def observe(scope, receive, send):
        async def capture(message):
            if message["type"] == "http.response.start":
                with request_sessions() as observer:
                    outcomes.append((message["status"], observer.query(Customer).count()))
            await send(message)
        await api(scope, receive, capture)

    response = TestClient(observe, raise_server_exceptions=False).post("/write")
    expected_status = 200 if failure is None else 409 if failure == "service" else 500
    assert response.status_code == expected_status
    assert outcomes == [(expected_status, 1 if failure is None else 0)]
    assert len(sessions) == 1
    assert commits == (["attempted"] if failure in {None, "commit"} else [])
    assert not sessions[0].in_transaction()
    with request_sessions() as observer:
        assert observer.query(Customer).count() == (1 if failure is None else 0)


def test_cancelled_transaction_rolls_back_and_closes(request_sessions, monkeypatch):
    gen = database.get_db()
    db = next(gen)
    db.add(_customer("CANCELLED"))
    db.flush()
    rolled_back = []
    original_rollback = db.rollback

    def rollback():
        rolled_back.append(True)
        original_rollback()

    monkeypatch.setattr(db, "rollback", rollback)
    with pytest.raises(asyncio.CancelledError):
        gen.throw(asyncio.CancelledError())
    assert rolled_back == [True]
    assert not db.in_transaction()
    with request_sessions() as observer:
        assert observer.query(Customer).count() == 0


@pytest.fixture()
def owner_headers(request_sessions):
    from app.core.security import create_access_token

    with request_sessions() as db:
        user = User(email="transaction-owner@example.invalid", full_name="Transaction Owner",
                    role=UserRole.OWNER_ADMIN, password_hash="unused-test-hash")
        db.add(user)
        db.commit()
        return {"Authorization": "Bearer " + create_access_token(
            {"sub": user.id, "session_version": user.session_version}
        )}


def test_authenticated_write_uses_one_session_and_one_commit(
    request_sessions, owner_headers, monkeypatch,
):
    sessions = []
    commits = []

    def new_session():
        db = request_sessions()
        sessions.append(db)
        commit = db.commit

        def finish():
            commits.append(True)
            commit()

        monkeypatch.setattr(db, "commit", finish)
        return db

    monkeypatch.setattr(database, "SessionLocal", new_session)
    response = TestClient(app).post("/api/v3/master-data/customers", headers=owner_headers,
        json={"client_code": "ATOMIC", "company_name": "Atomic Customer",
              "route_group": "TETE", "destination_country": "France"})
    assert response.status_code == 201, response.text
    assert len(sessions) == len(commits) == 1
    with request_sessions() as observer:
        assert observer.get(Customer, response.json()["id"]).client_code == "ATOMIC"


def test_user_and_audit_roll_back_when_response_validation_fails(
    request_sessions, owner_headers, monkeypatch,
):
    from app.api import admin

    monkeypatch.setattr(admin, "UserCreateResponse", lambda **kwargs: {"user": {}})
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v3/admin/users", headers=owner_headers,
        json={"email": "rollback@example.invalid", "full_name": "Rollback", "role": "GENERAL"},
    )
    assert response.status_code == 500
    with request_sessions() as observer:
        assert observer.query(User).filter_by(email="rollback@example.invalid").count() == 0
        assert observer.query(AuditLog).count() == 0


def test_authenticated_commit_failure_rolls_back_user_and_audit(
    request_sessions, owner_headers, monkeypatch,
):
    def new_session():
        db = request_sessions()

        def fail():
            db.flush()
            raise RuntimeError("Simulated commit failure")

        monkeypatch.setattr(db, "commit", fail)
        return db

    monkeypatch.setattr(database, "SessionLocal", new_session)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v3/admin/users", headers=owner_headers,
        json={"email": "commit-fail@example.invalid", "full_name": "Failure", "role": "GENERAL"},
    )
    assert response.status_code == 500
    assert "temporary_password" not in response.text
    with request_sessions() as observer:
        assert observer.query(User).filter_by(email="commit-fail@example.invalid").count() == 0
        assert observer.query(AuditLog).count() == 0


def test_lost_response_after_commit_leaves_one_durable_write(request_sessions):
    """A network loss after commit is an unknown client outcome; never auto-retry it."""
    api = FastAPI()

    @api.post("/write")
    def write(db=Depends(database.get_db, scope="function")):
        db.add(_customer("LOST-RESPONSE"))
        return {"saved": True}

    async def disconnected(scope, receive, send):
        async def fail_send(message):
            if message["type"] == "http.response.start":
                raise ConnectionError("Client disconnected after commit")
            await send(message)
        await api(scope, receive, fail_send)

    with pytest.raises(ConnectionError, match="disconnected"):
        TestClient(disconnected).post("/write")
    with request_sessions() as observer:
        assert observer.query(Customer).filter_by(client_code="LOST-RESPONSE").count() == 1
