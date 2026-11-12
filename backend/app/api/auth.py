from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import bearer, get_current_user
from app.core.login_limits import enforce_login_limit
from app.core.policy import permissions
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.core.values import UnicodeText
from app.models.audit import AuditLog
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email: UnicodeText = Field(min_length=1, max_length=255)
    password: UnicodeText = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    full_name: str
    role: str
    is_service_principal: bool
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    current_password: UnicodeText = Field(min_length=1, max_length=1024)
    new_password: UnicodeText = Field(min_length=12, max_length=128)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db, scope="function")):
    enforce_login_limit(db, req.email, request.client.host if request.client else "unknown")
    user = db.query(User).filter(User.email == req.email.strip().lower()).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account has been deactivated"
        )

    if password_needs_rehash(user.password_hash):
        # Password reset/deactivation may finish while the initial hash is being
        # verified. Refresh under the same user lock before replacing its hash.
        user = db.query(User).filter_by(id=user.id).with_for_update().populate_existing().first()
        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account has been deactivated")
        user.password_hash = hash_password(req.password)
        db.flush()

    token = create_access_token(
        {
            "sub": user.id,
            "email": user.email,
            "role": user.role.value,
            "is_service_principal": user.is_service_principal,
            "session_version": user.session_version,
        }
    )

    response.set_cookie(
        key="access_token",
        value=token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=settings.ENVIRONMENT.lower() not in {"development", "test"},
        samesite="lax",
        path="/",
    )

    return TokenResponse(
        access_token=token,
        user_id=user.id,
        full_name=user.full_name,
        role=user.role.value,
        is_service_principal=user.is_service_principal,
        must_change_password=user.must_change_password,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db, scope="function"),
) -> None:
    """Explicit logout revokes every token issued for this account's session version.

    Invalid/expired tokens can clear their cookie but cannot revoke other sessions.
    A delayed repeat from an old session must not invalidate a later fresh login.
    """
    token = credentials.credentials if credentials else request.cookies.get("access_token")
    payload = decode_access_token(token) if token else None
    version = payload.get("session_version", 0) if payload else None
    if payload and isinstance(payload.get("sub"), str) and type(version) is int and version >= 0:
        user = db.query(User).filter_by(id=payload["sub"]).with_for_update().populate_existing().first()
        if user and user.session_version == version:
            user.session_version += 1
            db.add(AuditLog(event_type="AUTH_LOGOUT_ALL", actor_id=user.id,
                actor_role=user.role.value, target_table="users", target_id=user.id,
                before_state={"session_version": version},
                after_state={"session_version": user.session_version, "scope": "ALL_SESSIONS"}))
            db.flush()
    response.delete_cookie(
        key="access_token",
        httponly=True,
        secure=settings.ENVIRONMENT.lower() not in {"development", "test"},
        samesite="lax",
        path="/",
    )


@router.get("/me")
def current_session(user: User = Depends(get_current_user)):
    return {
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_service_principal": user.is_service_principal,
        "must_change_password": user.must_change_password,
        "permissions": permissions(user),
        "request_replay_supported": True,
        "upload_replay_supported": True,
    }


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    req: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> None:
    db.query(User).filter_by(id=user.id).with_for_update().populate_existing().one()
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if req.current_password == req.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password",
        )
    user.password_hash = hash_password(req.new_password)
    user.session_version += 1
    user.must_change_password = False
    db.flush()
    response.delete_cookie(key="access_token", path="/", httponly=True,
        secure=settings.ENVIRONMENT.lower() not in {"development", "test"}, samesite="lax")
