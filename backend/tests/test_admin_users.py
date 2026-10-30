import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.policy import permissions
from app.core.security import hash_password
from app.main import app
from app.models.audit import AuditLog
from app.models.user import User, UserRole


def client_for(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app, raise_server_exceptions=False)


def login(client: TestClient, user: User, password: str = "Password123!") -> None:
    response = client.post(
        "/api/v3/auth/login",
        json={"email": user.email, "password": password},
    )
    assert response.status_code == 200


def make_user(email: str, role: UserRole, *, must_change_password: bool = False) -> User:
    return User(
        email=email,
        password_hash=hash_password("Password123!"),
        full_name=email.split("@", 1)[0].replace("-", " ").title(),
        role=role,
        must_change_password=must_change_password,
    )


def test_human_access_levels_are_hierarchical(db_session):
    general = make_user("general@example.com", UserRole.GENERAL)
    accounting = make_user("accounting@example.com", UserRole.GENERAL_ACCOUNTING)
    owner = make_user("owner@example.com", UserRole.OWNER_ADMIN)
    db_session.add_all([general, accounting, owner])
    db_session.commit()

    general_client = client_for(db_session)
    login(general_client, general)
    general_summary = general_client.get("/api/v3/operations/summary")
    assert general_summary.status_code == 200
    assert "open_balance_eur" not in general_summary.json()
    assert general_client.get("/api/v3/invoicing/work-queue").status_code == 403
    assert general_client.get("/api/v3/admin/users").status_code == 403

    accounting_client = client_for(db_session)
    login(accounting_client, accounting)
    accounting_summary = accounting_client.get("/api/v3/operations/summary")
    assert accounting_summary.status_code == 200
    assert accounting_summary.json()["billing_scope"] == "INVOICE_ONLY"
    assert "open_balance_eur" not in accounting_summary.json()
    assert accounting_client.get("/api/v3/invoicing/work-queue").status_code == 200
    assert accounting_client.get("/api/v3/admin/users").status_code == 403

    owner_client = client_for(db_session)
    login(owner_client, owner)
    assert owner_client.get("/api/v3/admin/users").status_code == 200
    app.dependency_overrides.clear()


def test_owner_creates_user_with_one_time_password_and_user_must_replace_it(db_session):
    owner = make_user("owner@example.com", UserRole.OWNER_ADMIN)
    db_session.add(owner)
    db_session.commit()
    owner_client = client_for(db_session)
    login(owner_client, owner)

    created = owner_client.post(
        "/api/v3/admin/users",
        json={
            "email": "new-person@example.com",
            "full_name": "New Person",
            "role": "GENERAL",
        },
    )

    assert created.status_code == 201
    temporary_password = created.json()["temporary_password"]
    assert temporary_password
    assert created.json()["user"]["must_change_password"] is True

    duplicate = owner_client.post(
        "/api/v3/admin/users",
        json={"email": "new-person@example.com", "full_name": "Duplicate", "role": "GENERAL"},
    )
    service_role = owner_client.post(
        "/api/v3/admin/users",
        json={"email": "agent@example.com", "full_name": "Agent", "role": "AI_AGENT_1"},
    )
    assert duplicate.status_code == 409
    assert service_role.status_code == 422

    user_client = client_for(db_session)
    login_response = user_client.post(
        "/api/v3/auth/login",
        json={"email": "new-person@example.com", "password": temporary_password},
    )
    assert login_response.status_code == 200
    assert login_response.json()["must_change_password"] is True
    assert user_client.get("/api/v3/master-data/products").status_code == 403

    changed = user_client.post(
        "/api/v3/auth/change-password",
        json={"current_password": temporary_password, "new_password": "NewPassword456!"},
    )
    assert changed.status_code == 204
    assert user_client.get("/api/v3/master-data/products").status_code == 401
    assert user_client.post("/api/v3/auth/login", json={
        "email": "new-person@example.com", "password": "NewPassword456!",
    }).status_code == 200
    assert user_client.get("/api/v3/master-data/products").status_code == 200
    app.dependency_overrides.clear()


def test_owner_can_change_role_reset_password_and_deactivate_user(db_session):
    owner = make_user("owner@example.com", UserRole.OWNER_ADMIN)
    person = make_user("person@example.com", UserRole.GENERAL)
    db_session.add_all([owner, person])
    db_session.commit()
    person_client = client_for(db_session)
    login(person_client, person)
    owner_client = client_for(db_session)
    login(owner_client, owner)

    updated = owner_client.patch(
        f"/api/v3/admin/users/{person.id}",
        json={"role": "GENERAL_ACCOUNTING"},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "GENERAL_ACCOUNTING"
    assert person_client.get("/api/v3/invoicing/work-queue").status_code == 200

    reset = owner_client.post(f"/api/v3/admin/users/{person.id}/reset-password")
    assert reset.status_code == 200
    assert reset.json()["temporary_password"]

    deactivated = owner_client.patch(
        f"/api/v3/admin/users/{person.id}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    assert person_client.get("/api/v3/auth/me").status_code == 401
    assert {event.event_type for event in db_session.query(AuditLog).all()} == {
        "USER_UPDATED",
        "USER_PASSWORD_RESET",
    }
    app.dependency_overrides.clear()


def test_owner_cannot_disable_self_or_remove_final_owner(db_session):
    owner = make_user("owner@example.com", UserRole.OWNER_ADMIN)
    db_session.add(owner)
    db_session.commit()
    client = client_for(db_session)
    login(client, owner)

    disable_self = client.patch(
        f"/api/v3/admin/users/{owner.id}",
        json={"is_active": False},
    )
    demote_final_owner = client.patch(
        f"/api/v3/admin/users/{owner.id}",
        json={"role": "GENERAL"},
    )

    assert disable_self.status_code == 409
    assert demote_final_owner.status_code == 409
    app.dependency_overrides.clear()


def test_owner_can_permanently_delete_an_unused_user(db_session):
    owner = make_user("owner@example.com", UserRole.OWNER_ADMIN)
    person = make_user("unused@example.com", UserRole.GENERAL)
    db_session.add_all([owner, person])
    db_session.commit()
    client = client_for(db_session)
    login(client, owner)

    deleted = client.delete(f"/api/v3/admin/users/{person.id}")

    assert deleted.status_code == 204
    assert db_session.get(User, person.id) is None
    event = db_session.query(AuditLog).filter(AuditLog.event_type == "USER_DELETED").one()
    assert event.before_state["email"] == "unused@example.com"
    assert client.delete(f"/api/v3/admin/users/{owner.id}").status_code == 409
    app.dependency_overrides.clear()


@pytest.mark.parametrize("role", [UserRole.GENERAL, UserRole.GENERAL_ACCOUNTING, UserRole.OWNER_ADMIN])
def test_created_accounts_receive_category_permissions(db_session, role):
    owner = make_user("category-owner@example.com", UserRole.OWNER_ADMIN)
    db_session.add(owner)
    db_session.commit()
    owner_client = client_for(db_session)
    login(owner_client, owner)
    levels = owner_client.get("/api/v3/admin/users/access-levels")
    assert levels.status_code == 200
    expected = next(level["permissions"] for level in levels.json() if level["role"] == role)
    created = owner_client.post("/api/v3/admin/users", json={
        "email": "category-person@example.com", "full_name": "Category Person", "role": role,
    })
    assert created.status_code == 201
    assert created.json()["user"]["permissions"] == expected
    person = db_session.get(User, created.json()["user"]["id"])
    assert expected == permissions(person)
    person_client = client_for(db_session)
    login(person_client, person, created.json()["temporary_password"])
    assert person_client.post("/api/v3/auth/change-password", json={
        "current_password": created.json()["temporary_password"], "new_password": "ChangedPassword123!",
    }).status_code == 204
    login(person_client, person, "ChangedPassword123!")
    assert person_client.get("/api/v3/auth/me").json()["permissions"] == expected
    assert person_client.get("/api/v3/invoicing/work-queue").status_code == (403 if role == UserRole.GENERAL else 200)
    assert person_client.get("/api/v3/admin/users/access-levels").status_code == (200 if role == UserRole.OWNER_ADMIN else 403)
    app.dependency_overrides.clear()


def test_category_downgrade_revokes_existing_session_permissions(db_session):
    owner = make_user("downgrade-owner@example.com", UserRole.OWNER_ADMIN)
    person = make_user("downgrade-person@example.com", UserRole.OWNER_ADMIN)
    db_session.add_all([owner, person])
    db_session.commit()
    owner_client = client_for(db_session)
    login(owner_client, owner)
    person_client = client_for(db_session)
    login(person_client, person)
    assert person_client.get("/api/v3/admin/users").status_code == 200
    updated = owner_client.patch(f"/api/v3/admin/users/{person.id}", json={"role": "GENERAL"})
    assert updated.status_code == 200
    assert "manage_users" not in updated.json()["permissions"]
    assert "manage_billing" not in person_client.get("/api/v3/auth/me").json()["permissions"]
    assert person_client.get("/api/v3/admin/users").status_code == 403
    assert person_client.get("/api/v3/invoicing/work-queue").status_code == 403
    assert person_client.get("/api/v3/operations/summary").status_code == 200
    app.dependency_overrides.clear()
