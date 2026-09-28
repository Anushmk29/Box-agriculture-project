from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.order import OrderStatus, RawOrderFile
from app.models.user import HUMAN_ROLES, User, UserRole
from app.modules.workflow.service import WorkflowService
from app.services.matrix_parser import MatrixOrderParser
from app.services.name_matcher import NameFirstMatcher


@dataclass(frozen=True)
class ProductResolutionIssue:
    product_code: str
    name_vi: str
    name_en: str
    pack_size: str


@dataclass(frozen=True)
class MatrixValidationResult:
    raw_file_id: str
    sheets: list[str]
    total_customers: int
    total_product_rows: int
    resolved_product_rows: int
    unresolved: list[ProductResolutionIssue]

    @property
    def can_split(self) -> bool:
        return not self.unresolved


class OrderValidationService:
    """Validates a matrix and exposes ambiguous products for human resolution."""

    def __init__(self, db: Session):
        self.db = db

    def validate(self, *, raw_file_id: str, actor: User) -> MatrixValidationResult:
        if actor.role not in {*HUMAN_ROLES, UserRole.AI_AGENT_1}:
            raise PermissionError("Order validation requires Agent 1 or an operations manager")
        raw_file = self.db.query(RawOrderFile).filter_by(id=raw_file_id).first()
        if not raw_file:
            raise ValueError(f"Raw order file {raw_file_id} not found")
        if raw_file.status not in {OrderStatus.UPLOADED, OrderStatus.PARSED}:
            raise ValueError("Only an uploaded or parsed matrix can be validated")

        parsed = MatrixOrderParser(raw_file.storage_url).parse_all_clients()
        matcher = NameFirstMatcher(self.db)
        unresolved = []
        resolved = 0
        for product in parsed["products_catalog"].values():
            match, _, _ = matcher.resolve_product_names(
                [product["name_vi"], product["name_en"]],
                raw_code=product["code"],
                main_customer_id=raw_file.main_customer_id,
            )
            if match:
                resolved += 1
            else:
                unresolved.append(
                    ProductResolutionIssue(
                        product_code=product["code"],
                        name_vi=product["name_vi"],
                        name_en=product["name_en"],
                        pack_size=product["pack_size"],
                    )
                )

        if raw_file.status == OrderStatus.UPLOADED:
            WorkflowService(self.db).transition(
                target=raw_file,
                to_status=OrderStatus.PARSED,
                actor=actor,
                target_table="raw_order_files",
            )
        raw_file.acknowledgment_summary = {
            **(raw_file.acknowledgment_summary or {}),
            "validation": {
                "resolved_product_rows": resolved,
                "unresolved_product_rows": len(unresolved),
                "can_split": not unresolved,
            },
        }
        self.db.commit()
        return MatrixValidationResult(
            raw_file_id=raw_file.id,
            sheets=parsed["sheets_processed"],
            total_customers=parsed["total_clients_found"],
            total_product_rows=len(parsed["products_catalog"]),
            resolved_product_rows=resolved,
            unresolved=unresolved,
        )
