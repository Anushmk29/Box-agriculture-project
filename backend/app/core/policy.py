"""Role policy: every protected action and who may perform it, in one table.

A human may act when their rank is at least the action's minimum role; a service
principal (AI agent) may act only when explicitly listed. Routes, modules and the
web UI (via /auth/me permissions) all ask this table.
"""

from __future__ import annotations

from enum import StrEnum

from app.models.user import HUMAN_ROLE_RANK, User, UserRole


class Action(StrEnum):
    VALIDATE_ORDERS = "validate_orders"
    CONFIRM_PROCUREMENT = "confirm_procurement"
    DISPATCH_HANDOFF_01 = "dispatch_handoff_01"
    RETURN_HANDOFF_02 = "return_handoff_02"
    DESIGN_PACKING = "design_packing"
    FLOOR_PACKING = "floor_packing"
    LOCK_FINAL_PACK = "lock_final_pack"
    DRAFT_INVOICE = "draft_invoice"
    MANAGE_BILLING = "manage_billing"
    MANAGE_USERS = "manage_users"
    EDIT_BUFFERS = "edit_buffers"
    MERGE_PRODUCTS = "merge_products"
    RESOLVE_ORDERS = "resolve_orders"
    RESOLVE_LATE_CANCELLATION = "resolve_late_cancellation"


_NONE: frozenset[UserRole] = frozenset()

# action -> (minimum human role, service principals allowed)
POLICY: dict[Action, tuple[UserRole, frozenset[UserRole]]] = {
    Action.VALIDATE_ORDERS: (UserRole.GENERAL, frozenset({UserRole.AI_AGENT_1})),
    Action.CONFIRM_PROCUREMENT: (UserRole.GENERAL, _NONE),
    Action.DISPATCH_HANDOFF_01: (UserRole.OWNER_ADMIN, frozenset({UserRole.AI_AGENT_1})),
    Action.RETURN_HANDOFF_02: (UserRole.OWNER_ADMIN, frozenset({UserRole.AI_AGENT_2})),
    Action.DESIGN_PACKING: (UserRole.GENERAL, frozenset({UserRole.AI_AGENT_2})),
    Action.FLOOR_PACKING: (UserRole.GENERAL, _NONE),
    Action.LOCK_FINAL_PACK: (UserRole.OWNER_ADMIN, _NONE),
    Action.DRAFT_INVOICE: (UserRole.GENERAL_ACCOUNTING, frozenset({UserRole.AI_AGENT_1})),
    Action.MANAGE_BILLING: (UserRole.GENERAL_ACCOUNTING, _NONE),
    Action.MANAGE_USERS: (UserRole.OWNER_ADMIN, _NONE),
    Action.EDIT_BUFFERS: (UserRole.OWNER_ADMIN, _NONE),
    Action.MERGE_PRODUCTS: (UserRole.OWNER_ADMIN, _NONE),
    Action.RESOLVE_ORDERS: (UserRole.GENERAL, _NONE),
    Action.RESOLVE_LATE_CANCELLATION: (UserRole.OWNER_ADMIN, _NONE),
}

DENIED = {
    Action.VALIDATE_ORDERS: "Order validation requires Agent 1 or an operations user",
    Action.CONFIRM_PROCUREMENT: "A human procurement role must confirm purchases",
    Action.DISPATCH_HANDOFF_01: "Handoff 01 must be dispatched by Agent 1",
    Action.RETURN_HANDOFF_02: "Handoff 02 must be returned by Agent 2",
    Action.DESIGN_PACKING: "Packing design must be executed by Agent 2 or an operations user",
    Action.FLOOR_PACKING: "A floor role must perform this packing step",
    Action.LOCK_FINAL_PACK: "Owner/Admin approval is required to lock final packing",
    Action.DRAFT_INVOICE: "Invoice drafts need Agent 1 or an accounting role",
    Action.MANAGE_BILLING: "Accounting approval is required for this action",
    Action.MANAGE_USERS: "Only the owner can manage users",
    Action.EDIT_BUFFERS: "Only Owner/Admin may modify procurement buffers",
    Action.MERGE_PRODUCTS: "Only Owner/Admin may merge master products",
    Action.RESOLVE_ORDERS: "Only human staff may resolve order exceptions",
    Action.RESOLVE_LATE_CANCELLATION: "Only Owner/Admin may record a later manual cancellation",
}


def allows(role: UserRole, is_service_principal: bool, action: Action) -> bool:
    minimum, services = POLICY[action]
    if is_service_principal:
        return role in services
    return HUMAN_ROLE_RANK.get(role, 0) >= HUMAN_ROLE_RANK[minimum]


def can(user: User, action: Action) -> bool:
    return allows(user.role, bool(user.is_service_principal), action)


def require(user: User, action: Action) -> None:
    """Module-level guard; the app maps PermissionError to 403."""
    if not can(user, action):
        raise PermissionError(DENIED[action])


def permissions(user: User) -> list[str]:
    return [action.value for action in Action if can(user, action)]
