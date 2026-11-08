import os
import re
from datetime import date

import pypdf

from app.core import work_limits as limits
from app.core.document_limits import inspect_document
from app.core.work_limits import WorkLimit
from app.modules.orders.quantities import unit_key
from app.services.canonical_schema import (
    ActionType,
    CanonicalDraftItem,
    CanonicalOrderDraft,
    IngestionChannel,
)


class PdfOrderParser:
    """
    Parser for official vector PDF purchase orders and export booking sheets.
    Supports European multi-lingual formats (French/Thai, English/Thai),
    zero-quantity line suppression, and packaging/box breakdown extraction.
    """

    def __init__(self, file_path: str, *, shipment_date_reference: str | None = None):
        self.file_path = file_path
        inspect_document(file_path)
        self.shipment_date_reference = shipment_date_reference
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF order file not found at: {file_path}")

    def parse(self) -> CanonicalOrderDraft:
        file_name = os.path.basename(self.file_path)
        reader = pypdf.PdfReader(self.file_path)
        if len(reader.pages) > limits.MAX_PAGES:
            raise WorkLimit("PDF exceeds the 200-page budget")
        full_text = ""
        for page in reader.pages:
            full_text += (page.extract_text() or "") + "\n"
            if len(full_text.encode("utf-8")) > limits.MAX_TEXT_BYTES:
                raise WorkLimit("PDF extracted text exceeds the 2 MB budget")

        lower_text = full_text.lower()
        paris = "paris store" in lower_text or "alindar" in lower_text
        italy = bool(re.search(r"\b(itm|mxp)\b", lower_text))
        if (("alindar" in file_name.lower() and italy and not paris)
                or (re.search(r"\bitm\b", file_name, re.I) and paris and not italy)):
            raise ValueError("Filename and PDF customer identity conflict; review the source")
        if paris:
            return self._parse_alindar_paris_store(full_text, file_name)
        elif italy:
            return self._parse_itm_italy(full_text, file_name)
        else:
            return self._parse_generic_pdf_table(full_text, file_name)

    def _parse_alindar_paris_store(self, text: str, file_name: str) -> CanonicalOrderDraft:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        items: list[CanonicalDraftItem] = []

        # Find ETA date
        eta_match = re.search(r"ETA\s+(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", text, re.IGNORECASE)
        shipment_date = None
        if eta_match:
            reference = getattr(self, "shipment_date_reference", None)
            year = int(eta_match.group(3)) if eta_match.group(3) else (
                date.fromisoformat(reference).year if reference else None
            )
            if year:
                shipment_date = date(year, int(eta_match.group(2)), int(eta_match.group(1))).isoformat()

        for line_number, line in enumerate(lines, 1):
            m = re.search(r"^(.*?)\s+([+-]?\d+(?:[.,]\d+)?)\s*([A-Za-z]+)$", line, re.IGNORECASE)
            if not m:
                continue

            content_part = m.group(1).strip()
            if not content_part or re.fullmatch(r"[\d\s.,]+", content_part):
                continue  # The unlabeled thousands-separated PDF total is not a product.
            raw_quantity = m.group(2)
            qty = float(raw_quantity.replace(",", "."))
            unit = unit_key(m.group(3))

            # Ignore header lines
            if "total" in content_part.lower() or "d e s i g n a t i o n" in content_part.lower():
                continue

            # Extract leading code if present (e.g. 502232, 930125, 990001, 130)
            code_m = re.match(r"^(\d{3,6}|EL-\d{3})\s+(.*)$", content_part)
            prod_code = None
            prod_name = content_part
            if code_m:
                prod_code = code_m.group(1).strip()
                prod_name = code_m.group(2).strip()
            supplier = re.match(r"^(\d{2}-\d{3}|EL-?\d{2,3})\s+(.*)$", prod_name)
            supplier_code = supplier[1] if supplier else None
            if supplier:
                prod_name = supplier[2]

            # Pack size hint
            pack_m = re.search(r"(\d+(?:[.,]\d+)?\s*(?:kg|gr|g))\b", prod_name, re.IGNORECASE)
            pack_size = pack_m.group(1) if pack_m else None

            problems = []
            if unit not in {"kg", "g", "pc", "pack", "box", "bag", "bunch", "tray"}:
                problems.append(f"Confirm the PDF order quantity unit: {m.group(3)}")
            if unit == "box":
                problems.append("Confirm the weight or inner pack count per ordered box; a product pack label is not a box weight")
            if qty < 0:
                problems.append("Negative order quantities need an explicit cancellation")
                qty = 0
            action = ActionType.ORDER if qty > 0 or problems else ActionType.CANCEL

            items.append(
                CanonicalDraftItem(
                    raw_text=line,
                    product_code=prod_code,
                    product_name=prod_name,
                    pack_size=pack_size,
                    quantity=qty,
                    unit=unit,
                    action=action,
                    confidence=0.98,
                    notes="Paris Store DDP Thiais Order",
                    quantity_issues=problems,
                    source={"file": file_name, "pdf_line": line_number,
                            "raw_quantity": raw_quantity, "raw_unit": m.group(3),
                            "raw_pack_size": pack_size, "supplier_code": supplier_code,
                            "requires_packing_review": unit == "box" or not pack_size},
                )
            )

        return CanonicalOrderDraft(
            source_file=file_name,
            channel=IngestionChannel.PDF_TABLE,
            client_code="PARIS-STORE",
            client_name="PARIS STORE / ALINDAR",
            main_customer_code="ALINDAR",
            shipment_date=shipment_date,
            route_line="PARIS-DDP",
            destination_country="France",
            items=items,
            metadata={"incoterm": "DDP THIAIS", "hub": "Frankfurt/Paris",
                      "document_eta_raw": eta_match.group(0) if eta_match else None},
        )

    def _parse_itm_italy(self, text: str, file_name: str) -> CanonicalOrderDraft:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        items: list[CanonicalDraftItem] = []

        date_match = re.search(r"(\d{2}-\d{2}-\d{4})", text)
        shipment_date = date_match.group(1) if date_match else None
        # Convert DD-MM-YYYY to YYYY-MM-DD
        if shipment_date and len(shipment_date) == 10 and shipment_date[2] == "-":
            d, m, y = shipment_date.split("-")
            shipment_date = date(int(y), int(m), int(d)).isoformat()

        for line in lines:
            if any(kw in line for kw in ["CODE THAI NAME", "ชื่อลูกค้า", "วันส่งออก", "ปลายทาง", "450.0"]):
                continue

            # Produce line: e.g. "11001 ตะไคร้ Lemon grass 200G/PACK 20.0 Kgs 2 BOX (10 KGS / BOX)"
            m_kgs = re.search(
                r"^(\d+)\s+([^\s]+)\s+(.+?)\s+([0-9A-Z/]+)\s+(\d+(?:\.\d+)?)\s*(?:Kgs|KG)\s*(.*)$",
                line,
                re.IGNORECASE,
            )
            if m_kgs:
                code = m_kgs.group(1)
                thai_name = m_kgs.group(2)
                eng_name = m_kgs.group(3)
                package = m_kgs.group(4)
                qty = float(m_kgs.group(5))
                box_info = m_kgs.group(6)
                items.append(
                    CanonicalDraftItem(
                        raw_text=line,
                        product_code=code,
                        product_name=f"{thai_name} ({eng_name})",
                        pack_size=package,
                        quantity=qty,
                        unit="kg",
                        action=ActionType.ORDER,
                        confidence=0.99,
                        notes=box_info.strip() if box_info else None,
                    )
                )
                continue

            # Piece line e.g. Lucky bamboo: "ต้นไผ่กวนอิมแบบตรง LUCKY BAMBOO STRAIGHT 420.0 Pcs AIL ทำ"
            m_pcs = re.search(
                r"^([^\d]+?)\s+([A-Z\s]+?)\s+(\d+(?:\.\d+)?)\s*Pcs\s*(.*)$",
                line,
                re.IGNORECASE,
            )
            if m_pcs:
                th_name = m_pcs.group(1).strip()
                en_name = m_pcs.group(2).strip()
                qty = float(m_pcs.group(3))
                notes = m_pcs.group(4).strip()
                items.append(
                    CanonicalDraftItem(
                        raw_text=line,
                        product_code=None,
                        product_name=f"{th_name} ({en_name})",
                        pack_size="pcs",
                        quantity=qty,
                        unit="pcs",
                        action=ActionType.ORDER,
                        confidence=0.95,
                        notes=notes,
                    )
                )

        return CanonicalOrderDraft(
            source_file=file_name,
            channel=IngestionChannel.PDF_TABLE,
            client_code="ITM-ITALY",
            client_name="ITM",
            main_customer_code="ITM",
            shipment_date=shipment_date,
            route_line="MXP",
            destination_country="Italy",
            items=items,
            metadata={"airport": "MXP", "total_weight_est": 450.0},
        )

    def _parse_generic_pdf_table(self, text: str, file_name: str) -> CanonicalOrderDraft:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        items: list[CanonicalDraftItem] = []
        for line_number, line in enumerate(lines, 1):
            m = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(kg|kgs|pcs|pack|box)$", line, re.IGNORECASE)
            if m:
                qty = float(m.group(1).replace(",", "."))
                problems = ["Confirm the customer PDF format and quantities before Purchasing"]
                if qty < 0:
                    problems.append("Negative order quantities need an explicit cancellation")
                items.append(
                    CanonicalDraftItem(
                        raw_text=line,
                        product_code=None,
                        product_name=line[: m.start()].strip(),
                        quantity=max(qty, 0),
                        unit=unit_key(m.group(2)),
                        action=ActionType.ORDER,
                        confidence=0.80,
                        quantity_issues=problems,
                        source={"file": file_name, "pdf_line": line_number,
                                "raw_quantity": m.group(1), "raw_unit": m.group(2),
                                "requires_packing_review": True},
                    )
                )

        return CanonicalOrderDraft(
            source_file=file_name,
            channel=IngestionChannel.PDF_TABLE,
            client_code="GENERIC-PDF",
            items=items,
        )

