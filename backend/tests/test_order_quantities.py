from pathlib import Path

import pytest
from openpyxl import Workbook

from app.modules.orders.quantities import derive_quantity, normalize_pack
from app.services.canonical_schema import CanonicalDraftItem
from app.services.unified_excel_parser import UnifiedExcelParser


@pytest.mark.parametrize(
    "amount,unit,size,count,kg",
    [
        (1, "KG", "100g", 10, 1),
        (1, "kgs.", "200Gr.", 5, 1),
        (1500, "grams", "100g tray", 15, 1.5),
        (2, "kg", "500g", 4, 2),
        (10, "packs", "100g", 10, 1),
        (2, "Box", "10Kg/Box", 2, 20),
        (3, "pack", "25g bag", 3, 0.075),
    ],
)
def test_weight_and_pack_units_have_distinct_meanings(amount, unit, size, count, kg):
    item = CanonicalDraftItem(raw_text="source", quantity=amount, unit=unit, pack_size=size)
    result = derive_quantity(item)
    assert result.packs == count
    assert float(result.net_kg) == kg


@pytest.mark.parametrize(
    "amount,unit,size,error",
    [
        (1.05, "kg", "100g", "whole number"),
        (0.48, "kg", "100g", "whole number"),
        (1, "unknown", "100g", "quantity unit"),
        (1, "kg", "Pcs", "physical pack size"),
        (1, "kg", "loose", "physical pack size"),
        (1, "pack", "~500g", "nominal pack weight"),
        (2.5, "packs", "100g", "whole number"),
    ],
)
def test_ambiguous_or_fractional_quantities_require_review(amount, unit, size, error):
    with pytest.raises(ValueError, match=error):
        derive_quantity(
            CanonicalDraftItem(raw_text="source", quantity=amount, unit=unit, pack_size=size)
        )


def test_numeric_packing_uses_template_units_and_conflicts_are_retained():
    assert normalize_pack(100, numeric_unit="g") == ("100g", [])
    assert normalize_pack(0.1, numeric_unit="kg") == ("100g", [])
    assert normalize_pack(100)[1]
    size, issues = normalize_pack(250, numeric_unit="g", adjacent_kg=0.2)
    assert size == "250g" and "conflicts" in issues[0]


def test_declared_line_weight_cannot_silently_override_conflicting_pack_math():
    item = CanonicalDraftItem(
        raw_text="source", quantity=10, unit="pack", pack_size="100g", net_weight_kg=2
    )
    with pytest.raises(ValueError, match="conflicts"):
        derive_quantity(item)


def test_yachi_numeric_grams_survives_uploaded_uuid_and_preserves_original(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Customer Name", "YACHI"])
    sheet.append(
        [
            None,
            "Product Codes",
            "Name Eng",
            "Name Thai",
            "Name Viet",
            "Packing / size",
            "Amount to order",
            "Weight",
        ]
    )
    sheet.append([None, "11-001", "LEMON GRASS", None, None, 100, 1, "KG"])
    path = tmp_path / "87f29e2d-720b-4584-9fc0-5a7ee49d7210_Order YACHI shipment.xlsx"
    workbook.save(path)
    (draft,) = UnifiedExcelParser(str(path)).parse()
    (item,) = draft.items
    assert item.pack_size == "100g" and item.unit == "kg"
    assert item.source["raw_pack_size"] == 100
    assert derive_quantity(item).packs == 10


@pytest.mark.parametrize(
    "filename",
    [
        "Order YACHI shipment ETD 07.08.2026.xlsx",
        "Ly-Thanh-Sarl Order Vegetable for Monday 07-09-2026.xlsx",
        "50H.xlsx",
        "928L.xlsx",
    ],
)
def test_supplied_order_files_preserve_pack_size_and_quantity_meaning(filename):
    path = Path(__file__).resolve().parents[2] / "documents/new input format" / filename
    if not path.exists():
        pytest.skip("Supplied local workbook is not available")
    drafts = UnifiedExcelParser(str(path)).parse()
    if filename.startswith("Order YACHI"):
        row = next(i for i in drafts[0].items if i.product_code == "11-001")
        assert derive_quantity(row).packs == 10
    elif filename.startswith("Ly-Thanh"):
        row = drafts[0].items[0]
        assert derive_quantity(row).packs == 50
    elif filename == "50H.xlsx":
        assert drafts[0].items[0].pack_size == "100g"
        with pytest.raises(ValueError, match="whole number"):
            derive_quantity(drafts[0].items[0])
    else:
        # The conflicting kale row is un-ordered in this file, so it must not
        # fabricate an order merely to expose the reference conflict.
        assert all(i.source["row"] != 9 for i in drafts[0].items)
        ordered = next(i for i in drafts[0].items if i.source["row"] == 41)
        assert ordered.unit == "kg" and ordered.pack_size == "200g"
        assert derive_quantity(ordered).packs == 10
