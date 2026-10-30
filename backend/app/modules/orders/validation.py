from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session, undefer

from app.core.policy import Action, require
from app.models.order import OrderStatus, RawOrderFile
from app.models.user import User
from app.modules.orders.acknowledgment import order_drafts
from app.modules.orders.quantity_review import quantity_review_data, quantity_rows
from app.modules.orders.templates import recognize_saved_formats, template_issues
from app.modules.workflow.service import WorkflowService
from app.services.canonical_schema import ActionType
from app.services.name_matcher import NameFirstMatcher


@dataclass(frozen=True)
class ProductResolutionIssue:
    product_code: str
    name_vi: str
    name_en: str
    pack_size: str
    suggestions: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class MatrixValidationResult:
    raw_file_id: str
    sheets: list[str]
    total_customers: int
    total_product_rows: int
    resolved_product_rows: int
    unresolved: list[ProductResolutionIssue]
    quantity_issues: list[dict] = field(default_factory=list)
    quantity_review: dict = field(default_factory=dict)

    @property
    def can_split(self) -> bool:
        return not self.unresolved and not self.quantity_issues


class OrderValidationService:
    """Validates a matrix and exposes ambiguous products for human resolution."""

    def __init__(self, db: Session):
        self.db = db

    def validate(self, *, raw_file_id: str, actor: User) -> MatrixValidationResult:
        require(actor, Action.VALIDATE_ORDERS)
        raw_file = (
            self.db.query(RawOrderFile)
            .options(undefer(RawOrderFile.acknowledgment_summary))
            .filter_by(id=raw_file_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not raw_file:
            raise ValueError(f"Raw order file {raw_file_id} not found")
        if raw_file.status not in {OrderStatus.UPLOADED, OrderStatus.PARSED}:
            raise ValueError("Only an uploaded or parsed matrix can be validated")

        drafts = order_drafts(raw_file)
        recognize_saved_formats(self.db, raw_file, drafts)
        catalog = {
            (
                item.product_code or "",
                item.product_name or item.raw_text,
                item.pack_size or "std",
            ): item
            for draft in drafts
            for item in draft.items
            if item.action != ActionType.CANCEL and item.quantity > 0
        }
        matcher = NameFirstMatcher(self.db)
        unresolved = []
        resolved = 0
        for item in catalog.values():
            names = [item.product_name or item.raw_text, *item.alt_names]
            match = matcher.reviewed_product(
                names,
                raw_code=item.product_code,
                main_customer_id=raw_file.main_customer_id,
            )
            if match:
                resolved += 1
            else:
                unresolved.append(
                    ProductResolutionIssue(
                        product_code=item.product_code or "",
                        name_vi=item.product_name or item.raw_text,
                        name_en=next(iter(item.alt_names), item.product_name or item.raw_text),
                        pack_size=item.pack_size or "std",
                        suggestions=matcher.suggestions(names, item.product_code),
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
            # Older uploads only kept the source file. Retain the parsed rows
            # under this validation lock so subsequent reviews need no reparse.
            "drafts": [draft.model_dump(mode="json") for draft in drafts],
            "validation": {
                "resolved_product_rows": resolved,
                "unresolved_product_rows": len(unresolved),
                "can_split": not unresolved,
            },
        }
        rows = quantity_rows(self.db, raw_file, drafts, resolver=matcher)
        issues = [row for row in rows if row["issue"]]
        issues.extend({"issue": issue} for issue in template_issues(drafts))
        raw_file.acknowledgment_summary["validation"].update(
            {"can_split": not unresolved and not issues, "quantity_issue_count": len(issues)}
        )
        self.db.flush()
        return MatrixValidationResult(
            raw_file_id=raw_file.id,
            sheets=[
                row["sheet_name"]
                for row in (raw_file.acknowledgment_summary or {}).get("sheets_processed", [])
            ],
            total_customers=len({draft.client_code for draft in drafts}),
            total_product_rows=len(catalog),
            resolved_product_rows=resolved,
            unresolved=unresolved,
            quantity_issues=issues,
            quantity_review=quantity_review_data(drafts, rows, issues_only=True),
        )
