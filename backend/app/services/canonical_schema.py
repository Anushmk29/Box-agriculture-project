import enum
from typing import Any

from pydantic import BaseModel, Field


class ActionType(str, enum.Enum):
    ORDER = "ORDER"
    CANCEL = "CANCEL"
    UPDATE = "UPDATE"


class IngestionChannel(str, enum.Enum):
    EXCEL_MATRIX = "EXCEL_MATRIX"
    EXCEL_TABULAR = "EXCEL_TABULAR"
    EXCEL_TEXT = "EXCEL_TEXT"
    LEGACY_XLS = "LEGACY_XLS"
    PDF_TABLE = "PDF_TABLE"
    IMAGE_CHAT = "IMAGE_CHAT"
    EMAIL_SCREENSHOT = "EMAIL_SCREENSHOT"


class CanonicalDraftItem(BaseModel):
    raw_text: str = Field(..., description="Original raw line from document, chat or spreadsheet")
    product_code: str | None = Field(None, description="Explicit SKU/product code if detected")
    product_name: str | None = Field(None, description="Extracted product name")
    alt_names: list[str] = Field(
        default_factory=list, description="Other-language names tried after product_name"
    )
    pack_size: str | None = Field(None, description="Declared pack spec, e.g. 100g, 500g, loose")
    quantity: float = Field(..., description="Ordered quantity numeric amount")
    unit: str = Field("kg", description="Unit of measure: kg, pcs, box, pkt, etc.")
    action: ActionType = Field(ActionType.ORDER, description="ORDER, CANCEL, or UPDATE")
    confidence: float = Field(1.0, description="Extraction confidence score (0.0 to 1.0)")
    notes: str | None = Field(None, description="Extracted comments, brand tags, or instructions")
    net_weight_kg: float | None = Field(
        None, description="Declared net weight; overrides the derived weight"
    )
    quantity_issues: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(
        default_factory=dict,
        description="Original quantity, unit, packing label and source location",
    )


class CanonicalOrderDraft(BaseModel):
    source_file: str = Field(..., description="Filename or origin path")
    channel: IngestionChannel = Field(..., description="Detected file channel/format archetype")
    client_code: str = Field(
        ..., description="Client or sub-customer code e.g. 19T, 68H, PARIS-STORE, ITM"
    )
    client_name: str | None = Field(None, description="Client human name or company name")
    main_customer_code: str | None = Field(
        None, description="Parent account code e.g. NGA, LY-THANH, TONIO"
    )
    shipment_date: str | None = Field(None, description="Target shipment date YYYY-MM-DD")
    route_line: str | None = Field(
        None, description="Logistics route code e.g. TETE, NGA-L, NGA-H, NGA-T, JCT"
    )
    destination_country: str | None = Field(
        None, description="Destination country e.g. France, Norway, Italy"
    )
    items: list[CanonicalDraftItem] = Field(
        default_factory=list, description="Extracted line items"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary extra metadata extracted"
    )
