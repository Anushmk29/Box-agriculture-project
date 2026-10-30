from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.policy import Action
from app.models.user import User
from app.modules.handoffs.service import HandoffService

router = APIRouter(prefix="/agents", tags=["Agent handoff contracts"])


class Handoff01Request(BaseModel):
    batch_id: str


class Handoff02Request(BaseModel):
    order_id: str


def handoff_response(handoff):
    return {
        "handoff_id": handoff.id,
        "handoff_type": handoff.handoff_type,
        "payload_sha256": handoff.payload_sha256,
        "payload": handoff.payload_json,
    }


@router.post("/handoff-01/dispatch")
def dispatch_handoff_01(
    payload: Handoff01Request,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.DISPATCH_HANDOFF_01)),
):
    try:
        return handoff_response(
            HandoffService(db).dispatch_handoff_01(batch_id=payload.batch_id, actor=actor)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/handoff-02/return")
def return_handoff_02(
    payload: Handoff02Request,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.RETURN_HANDOFF_02)),
):
    try:
        return handoff_response(
            HandoffService(db).return_handoff_02(order_id=payload.order_id, actor=actor)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
