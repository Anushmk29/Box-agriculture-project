import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.policy import Action, allows
from app.core.security import hash_password
from app.core.values import UnicodeText
from app.models.audit import AuditLog
from app.models.user import HUMAN_ROLE_RANK, HumanRole, User, UserRole

router = APIRouter(
    prefix="/admin/users",
    tags=["User administration"],
)

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    must_change_password: bool

    @computed_field
    @property
    def permissions(self) -> list[str]:
        return [action.value for action in Action if allows(self.role, False, action)]


class UserCreate(BaseModel):
    email: UnicodeText = Field(min_length=3, max_length=255)
    full_name: UnicodeText = Field(min_length=1, max_length=255)
    role: HumanRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("A valid email address is required")
        return email

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Full name cannot be blank")
        return value


class UserCreateResponse(BaseModel):
    user: UserOut
    temporary_password: str


class UserUpdate(BaseModel):
    role: HumanRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.role is None and self.is_active is None:
            raise ValueError("Provide a role or active status to update")
        return self


class PasswordResetResponse(BaseModel):
    temporary_password: str


def temporary_password() -> str:
    return secrets.token_urlsafe(12)


def audit_user_change(
    db: Session,
    request: Request,
    actor: User,
    event_type: str,
    target: User,
    before_state: dict | None,
    after_state: dict | None,
) -> None:
    db.add(
        AuditLog(
            event_type=event_type,
            actor_id=actor.id,
            actor_role=actor.role.value,
            target_table="users",
            target_id=target.id,
            before_state=before_state,
            after_state=after_state,
            ip_address=request.client.host if request.client else None,
        )
    )


def get_user_or_404(db: Session, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id, User.is_service_principal.is_(False)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/access-levels")
def access_levels(_actor: User = Depends(require_action(Action.MANAGE_USERS))):
    return [
        {"role": role.value, "permissions": [
            action.value for action in Action if allows(role, False, action)
        ]}
        for role in HUMAN_ROLE_RANK
    ]


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db, scope="function"),
    _actor: User = Depends(require_action(Action.MANAGE_USERS)),
):
    return (
        db.query(User)
        .filter(User.is_service_principal.is_(False))
        .order_by(User.full_name, User.email)
        .all()
    )


@router.post("", response_model=UserCreateResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_USERS)),
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already in use")
    password = temporary_password()
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        password_hash=hash_password(password),
        is_active=True,
        is_service_principal=False,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    audit_user_change(
        db,
        request,
        actor,
        "USER_CREATED",
        user,
        None,
        {"email": user.email, "role": user.role.value, "is_active": True},
    )
    db.flush()
    db.refresh(user)
    return UserCreateResponse(user=user, temporary_password=password)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_USERS)),
):
    user = get_user_or_404(db, user_id)
    before = {"role": user.role.value, "is_active": user.is_active}
    removes_owner = user.role == UserRole.OWNER_ADMIN and (
        payload.role not in {None, UserRole.OWNER_ADMIN} or payload.is_active is False
    )
    if payload.is_active is False and user.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot deactivate your own account",
        )
    if removes_owner:
        active_owners = (
            db.query(User)
            .filter(
                User.role == UserRole.OWNER_ADMIN,
                User.is_active.is_(True),
                User.is_service_principal.is_(False),
            )
            .with_for_update()
            .all()
        )
        if len(active_owners) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="At least one active owner administrator is required",
            )
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    after = {"role": user.role.value, "is_active": user.is_active}
    audit_user_change(db, request, actor, "USER_UPDATED", user, before, after)
    db.flush()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_USERS)),
):
    user = get_user_or_404(db, user_id)
    if user.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot delete your own account",
        )
    if user.role == UserRole.OWNER_ADMIN and user.is_active:
        active_owners = (
            db.query(User)
            .filter(
                User.role == UserRole.OWNER_ADMIN,
                User.is_active.is_(True),
                User.is_service_principal.is_(False),
            )
            .with_for_update()
            .all()
        )
        if len(active_owners) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="At least one active owner administrator is required",
            )

    before = {
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
    }
    db.delete(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account has historical records and cannot be deleted. Deactivate it instead.",
        ) from exc
    audit_user_change(db, request, actor, "USER_DELETED", user, before, None)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_USERS)),
):
    user = get_user_or_404(db, user_id)
    password = temporary_password()
    db.query(User).filter_by(id=user.id).with_for_update().populate_existing().one()
    user.password_hash = hash_password(password)
    user.session_version += 1
    user.must_change_password = True
    audit_user_change(db, request, actor, "USER_PASSWORD_RESET", user, None, None)
    db.flush()
    return PasswordResetResponse(temporary_password=password)
