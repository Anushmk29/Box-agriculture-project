import os
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.services.adaptive_order_orchestrator import AdaptiveOrderOrchestrator
from app.services.canonical_schema import ActionType, IngestionChannel
from app.services.matrix_parser import MatrixOrderParser
from app.services.pdf_order_parser import PdfOrderParser
from app.services.unified_excel_parser import UnifiedExcelParser
from app.services.vision_chat_parser import VisionChatParser


def test_matrix_header_detection_recovers_missing_dimensions():
    class ReadOnlySheet:
        max_row = None
        max_column = None

        def calculate_dimension(self, force=False):
            assert force is True
            self.max_row = 3
            self.max_column = 4
            return "A1:D3"

        def cell(self, row, column):
            values = {(3, 1): "Code", (3, 2): "VIET DESCRIPTION", (3, 4): "Packing"}
            return SimpleNamespace(value=values.get((row, column)))

    parser = MatrixOrderParser.__new__(MatrixOrderParser)
    assert parser._find_header_anchor(ReadOnlySheet()) == (3, 4)


def test_matrix_customers_can_move_between_repeated_group_blocks(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Order Matrix"
    sheet.merge_cells("E2:F2")
    sheet.merge_cells("H2:I2")
    sheet["E2"] = "Group CT"
    sheet["H2"] = "Group CT"
    sheet.append(["Code", "VIET DESCRIPTION", "ENGLISH DESCRIPTION", "Packing", "CT-A", "CT-B", None, "CT-C"])
    sheet.append(["T001", "Huong nhu", "Holy basil", "100g", 2, None, None, 3])
    path = tmp_path / "movable-customers.xlsx"
    workbook.save(path)

    result = MatrixOrderParser(str(path)).parse_all_clients()
    assert result["clients_groups"] == {
        "CT-A": "GROUP CT",
        "CT-B": "GROUP CT",
        "CT-C": "GROUP CT",
    }
    assert len(result["clients_data"]["CT-A"]) == 1
    assert len(result["clients_data"]["CT-C"]) == 1


@pytest.fixture
def docs_dir():
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(backend_dir)
    return os.path.join(project_root, "documents", "new input format")


def test_pdf_order_parser(docs_dir):
    alindar_path = os.path.join(docs_dir, "ALINDAR.pdf")
    if os.path.exists(alindar_path):
        parser = PdfOrderParser(alindar_path)
        draft = parser.parse()
        assert draft.channel == IngestionChannel.PDF_TABLE
        assert draft.client_code == "PARIS-STORE"
        assert len(draft.items) > 0
        cancels = [i for i in draft.items if i.action == ActionType.CANCEL]
        assert len(cancels) > 0


def test_unified_excel_legacy_xls(docs_dir):
    vevey_path = os.path.join(docs_dir, "VEVEY_Ly-Thanh-Sarl-Order-Vegetable-for-07.09.2026.xls")
    if os.path.exists(vevey_path):
        parser = UnifiedExcelParser(vevey_path)
        drafts = parser.parse()
        assert len(drafts) == 1
        assert drafts[0].channel == IngestionChannel.LEGACY_XLS
        assert drafts[0].client_code == "LY-VEVEY"
        assert len(drafts[0].items) > 0


def test_unified_excel_unstructured_text(docs_dir):
    text_excel_path = os.path.join(docs_dir, "20T.xlsx")
    if os.path.exists(text_excel_path):
        parser = UnifiedExcelParser(text_excel_path)
        drafts = parser.parse()
        assert len(drafts) == 1
        assert drafts[0].channel == IngestionChannel.EXCEL_TEXT
        assert drafts[0].client_code == "20T"
        cancels = [i for i in drafts[0].items if i.action == ActionType.CANCEL]
        assert len(cancels) >= 2


def test_vision_chat_parser(docs_dir):
    img_path = os.path.join(docs_dir, "68H.png")
    if os.path.exists(img_path):
        parser = VisionChatParser(img_path)
        draft = parser.parse()
        assert draft.channel == IngestionChannel.EMAIL_SCREENSHOT
        assert draft.client_code == "68H"
        cancels = [i for i in draft.items if i.action == ActionType.CANCEL]
        orders = [i for i in draft.items if i.action == ActionType.ORDER]
        assert len(cancels) == 4
        assert len(orders) == 2


def test_adaptive_orchestrator_end_to_end(db_session, docs_dir):
    # Seed a sample product matching ITM Lemongrass
    p = Product(
        t_code="11-001",
        numeric_code="11001",
        name_en="Lemon grass",
        name_th="ตะไคร้",
        name_vi="Sả",
        box_material=BoxMaterial.CARDBOARD,
        temperature=TemperatureZone.NORMAL,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
    )
    db_session.add(p)
    db_session.commit()

    itm_path = os.path.join(docs_dir, "Order ITM ETD 06-09-2026.pdf")
    if os.path.exists(itm_path):
        orchestrator = AdaptiveOrderOrchestrator(db_session)
        res = orchestrator.ingest_file(itm_path, shipment_date="2026-09-06")
        assert res["channel"] == IngestionChannel.PDF_TABLE.value
        assert res["total_items_processed"] > 0
        assert res["matched_items_count"] > 0
        assert len(res["created_orders"]) > 0


def test_fast_track_template_pack_and_invoicing(db_session):
    from app.models.master_data import BoxCatalogue, Customer, PriceList
    from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
    from app.models.user import User, UserRole
    from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService
    from app.services.packing_override_service import PackingOverrideService

    admin = User(
        email="lead@freshflow.io",
        password_hash="pw",
        full_name="Operations Lead",
        role=UserRole.OWNER_ADMIN,
    )
    main_cust = Customer(
        client_code="TONIO",
        company_name="Tonio Wholesale",
        billing_email="tonio@export.eu",
        route_group="TETE",
        destination_country="France",
        is_main_customer=True,
    )
    sub_cust = Customer(
        client_code="CT-TRUNG",
        company_name="CT Trung",
        billing_email="trung@client.eu",
        route_group="GROUP CT",
        destination_country="France",
        parent_customer=main_cust,
    )
    cat = BoxCatalogue(
        box_type=BoxMaterial.CARDBOARD,
        size_name="Standard Cardboard",
        length_cm=50,
        width_cm=30,
        height_cm=20,
        rated_weight_kg=12,
        volume_m3=0.03,
        max_safe_net_weight_kg=15,
        is_active=True,
    )
    prod = Product(
        t_code="T001",
        numeric_code="11001",
        name_en="Lemongrass 100g",
        name_th="ตะไคร้",
        name_vi="Sả cây",
        box_material=BoxMaterial.CARDBOARD,
        temperature=TemperatureZone.NORMAL,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
    )
    db_session.add_all([admin, main_cust, sub_cust, cat, prod])
    db_session.commit()

    db_session.add(
        PriceList(
            product_id=prod.id,
            pack_size="100g",
            unit_price_eur=1.80,
            valid_from="2026-01-01",
        )
    )
    raw = RawOrderFile(
        file_name="Order_Tonio.xlsx",
        storage_url="Order_Tonio.xlsx",
        shipment_target_date="2026-09-07",
        route_line="GROUP CT",
        uploaded_by=admin.id,
        main_customer_id=main_cust.id,
        status=OrderStatus.PARSED,
    )
    db_session.add(raw)
    db_session.commit()

    order = ClientOrder(
        raw_order_file_id=raw.id,
        customer_id=sub_cust.id,
        main_customer_id=main_cust.id,
        order_ref_id="ORD-20260907-CT-TRUNG",
        status=OrderStatus.SPLIT_AND_VALIDATED,
        total_ordered_items_count=10,
        total_estimated_net_weight_kg=1.0,
    )
    db_session.add(order)
    db_session.commit()

    item = ClientOrderItem(
        client_order_id=order.id,
        product_id=prod.id,
        raw_produce_text="T001 Lemongrass 100g",
        pack_size="100g",
        ordered_pack_count=10,
        confirmed_pack_count=10,
        estimated_net_weight_kg=1.0,
        confirmed_net_weight_kg=1.0,
    )
    db_session.add(item)
    db_session.commit()

    # 1. Execute Template Fast-Track Packing
    pack_service = PackingOverrideService(db_session)
    res = pack_service.fast_track_template_pack_order(order.id, actor=admin)

    assert res["status"] == OrderStatus.HANDOFF_02_RETURNED.value
    assert res["boxes_count"] == 1
    assert res["total_net_weight_kg"] == 1.0
    assert res["total_gross_weight_kg"] == 1.5

    # 2. Invoicing is immediately unlocked without manual scale entry
    inv_service = OnBehalfInvoicingService(db_session)
    invoice = inv_service.generate_sub_customer_invoice(order.id, actor=admin)

    assert invoice.status.value == "INVOICE_DRAFT"
    assert float(invoice.total_amount_eur) == 18.00  # 10 packs * 1.80 EUR
    assert len(invoice.items) == 1
    assert invoice.items[0].billed_pack_count == 10


