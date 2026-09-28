from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import HUMAN_ROLE_RANK, User, UserRole

bearer = HTTPBearer(auto_error=False)

def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = credentials.credentials if credentials else request.cookies.get("access_token")
    if not token:
        raise unauthorized
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise unauthorized
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise unauthorized
    return user


def authorize_user(
    user: User,
    minimum_rank: int,
    service_roles: set[UserRole],
    detail: str,
) -> User:
    allowed = (
        user.role in service_roles
        if user.is_service_principal
        else HUMAN_ROLE_RANK.get(user.role, 0) >= minimum_rank
    )
    if user.must_change_password or not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user


def require_access(
    minimum_role: UserRole,
    *service_roles: UserRole,
) -> Callable[..., User]:
    minimum_rank = HUMAN_ROLE_RANK[minimum_role]
    allowed_service_roles = set(service_roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        detail = (
            "Password change required before accessing the application"
            if user.must_change_password
            else "Your role is not permitted to perform this operation"
        )
        return authorize_user(user, minimum_rank, allowed_service_roles, detail)

    return dependency


def require_human_access(
    minimum_role: UserRole,
    detail: str = "A human user with an authorized role is required",
) -> Callable[..., User]:
    minimum_rank = HUMAN_ROLE_RANK[minimum_role]

    def dependency(user: User = Depends(get_current_user)) -> User:
        return authorize_user(user, minimum_rank, set(), detail)

    return dependency
