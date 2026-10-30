import os
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dates import canonical_day
from app.core.parser_process import parse_document
from app.models.order import OrderStatus, RawOrderFile
from app.models.user import User
from app.modules.orders.acknowledgment import prepare_acknowledgment
from app.modules.orders.intake import OrderIntake
from app.modules.orders.templates import template_snapshots
from app.services.canonical_schema import ActionType, CanonicalOrderDraft


class AdaptiveOrderOrchestrator:
    """Universal intake: picks the parser adapter by file type, then hands drafts to OrderIntake."""

    def __init__(self, db: Session):
        self.db = db
        self.intake = OrderIntake(db)

    def ingest_file(
        self,
        file_path: str,
        shipment_date: str,
        actor: User,
        main_customer_id: str | None = None,
        route_line: str | None = None,
        storage_url: str | None = None,
        original_file_name: str | None = None,
        file_sha256: str | None = None,
        file_size_bytes: int | None = None,
    ) -> dict[str, Any]:
        """
        Universal entrypoint for any order file format.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        shipment_date = canonical_day(shipment_date)
        main_cust = self.intake.require_main_customer(main_customer_id) if main_customer_id else None

        file_name = original_file_name or os.path.basename(file_path)

        parser_options = {}
        if settings.EXCEL_TEMPLATE_PROFILES_ENABLED and os.path.splitext(file_path)[1].lower() in {".xlsx", ".xls", ".xlsm"}:
            parser_options = {"profiles": template_snapshots(self.db, main_customer_id), "original_name": file_name}
        parsed = parse_document(file_path, mode="universal", shipment_date=shipment_date, **parser_options)
        drafts = [CanonicalOrderDraft.model_validate(value) for value in parsed["drafts"]]

        for draft in drafts:
            if draft.shipment_date:
                draft.shipment_date = canonical_day(draft.shipment_date)
                if draft.shipment_date != shipment_date:
                    raise ValueError(
                        f"Shipment date conflict: document says {draft.shipment_date}, "
                        f"selected date is {shipment_date}; review the document and selected date"
                    )
            if (main_cust and draft.main_customer_code
                    and draft.main_customer_code.casefold() != main_cust.client_code.casefold()):
                raise ValueError("Document main customer conflicts with the selected main customer; review the account")

        # 2. Persist RawOrderFile Record
        if not main_cust:
            main_cust = self.intake.get_or_create_customer(
                (drafts[0].main_customer_code if drafts else None) or "GENERAL",
                route_group=None,
                parent_id=None,
                is_main=True,
            )

        target_route = route_line or (drafts[0].route_line if drafts else "GENERAL") or "GENERAL"
        raw_file = RawOrderFile(
            id=str(uuid.uuid4()),
            file_name=file_name,
            storage_url=storage_url or file_path,
            file_sha256=file_sha256,
            file_size_bytes=file_size_bytes,
            shipment_target_date=shipment_date,
            route_line=target_route,
            uploaded_by=actor.id,
            main_customer_id=main_cust.id,
            status=OrderStatus.PARSED,
        )
        self.db.add(raw_file)
        self.db.flush()
        raw_file.acknowledgment_summary = {"drafts": [draft.model_dump(mode="json") for draft in drafts]}
        prepare_acknowledgment(raw_file, drafts)
        items = [item for draft in drafts for item in draft.items if item.action != ActionType.CANCEL and item.quantity > 0]
        matched = sum(self.intake.resolve(item, raw_file.main_customer_id) is not None for item in items)
        self.db.flush()

        return {
            "templates": [d.metadata["template"] for d in drafts if "template" in d.metadata],
            "file_name": file_name,
            "channel": drafts[0].channel.value if drafts else "UNKNOWN",
            "raw_file_id": raw_file.id,
            "detected_sub_customers": len(drafts),
            "created_orders": [],
            "total_items_processed": len(items),
            "matched_items_count": matched,
            "unmatched_items_count": len(items) - matched,
            "match_rate_percentage": round(
                (matched / len(items) * 100) if items else 0,
                1,
            ),
            "total_net_weight_kg": 0,
            "procurement_consolidated_skus": 0,
        }
