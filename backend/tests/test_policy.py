from app.core.policy import Action, allows
from app.models.user import UserRole

G, ACC, OWNER = UserRole.GENERAL, UserRole.GENERAL_ACCOUNTING, UserRole.OWNER_ADMIN
A1, A2 = UserRole.AI_AGENT_1, UserRole.AI_AGENT_2


def who(action):
    humans = {r for r in (G, ACC, OWNER) if allows(r, False, action)}
    agents = {r for r in (A1, A2) if allows(r, True, action)}
    return humans | agents


def test_policy_table():
    assert who(Action.VALIDATE_ORDERS) == {G, ACC, OWNER, A1}
    assert who(Action.CONFIRM_PROCUREMENT) == {G, ACC, OWNER}
    assert who(Action.DISPATCH_HANDOFF_01) == {OWNER, A1}
    assert who(Action.RETURN_HANDOFF_02) == {OWNER, A2}
    assert who(Action.DESIGN_PACKING) == {G, ACC, OWNER, A2}
    assert who(Action.FLOOR_PACKING) == {G, ACC, OWNER}
    assert who(Action.LOCK_FINAL_PACK) == {OWNER}
    assert who(Action.EDIT_BUFFERS) == {OWNER}
    assert who(Action.MERGE_PRODUCTS) == {OWNER}
    assert who(Action.DRAFT_INVOICE) == {ACC, OWNER, A1}
    assert who(Action.MANAGE_BILLING) == {ACC, OWNER}
    assert who(Action.MANAGE_USERS) == {OWNER}


def test_agent_role_without_service_flag_is_not_an_agent():
    assert not allows(A1, False, Action.DRAFT_INVOICE)
