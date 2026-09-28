import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_access
from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.user import HumanRole, User, UserRole

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


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
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
        return value.strip()


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


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_access(UserRole.OWNER_ADMIN)),
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
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.OWNER_ADMIN)),
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
    db.commit()
    db.refresh(user)
    return UserCreateResponse(user=user, temporary_password=password)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.OWNER_ADMIN)),
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
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.OWNER_ADMIN)),
):
    user = get_user_or_404(db, user_id)
    password = temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    audit_user_change(db, request, actor, "USER_PASSWORD_RESET", user, None, None)
    db.commit()
    return PasswordResetResponse(temporary_password=password)
