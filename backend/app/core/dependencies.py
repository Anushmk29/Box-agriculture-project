from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.policy import POLICY, Action
from app.core.security import decode_access_token
from app.models.user import HUMAN_ROLE_RANK, User, UserRole

bearer = HTTPBearer(auto_error=False)

def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db, scope="function"),
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
    if payload.get("session_version", 0) != user.session_version:
        raise unauthorized
    return user


def require_access(
    minimum_role: UserRole,
    *service_roles: UserRole,
    detail: str | None = None,
) -> Callable[..., User]:
    minimum_rank = HUMAN_ROLE_RANK[minimum_role]
    allowed_service_roles = set(service_roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        allowed = (
            user.role in allowed_service_roles
            if user.is_service_principal
            else HUMAN_ROLE_RANK.get(user.role, 0) >= minimum_rank
        )
        if user.must_change_password or not allowed:
            error_detail = detail or (
                "Password change required before accessing the application"
                if user.must_change_password
                else "Your role is not permitted to perform this operation"
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_detail)
        return user

    return dependency


def require_action(action: Action) -> Callable[..., User]:
    """Route gate for a policy action; same table the module-level `require` uses."""
    minimum, services = POLICY[action]
    return require_access(minimum, *services, detail=None)
