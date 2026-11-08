import pytest
from openpyxl import Workbook
from sqlalchemy import event

from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.models.order import OrderStatus, RawOrderFile
from app.models.user import User, UserRole
from app.modules.orders.validation import OrderValidationService


@pytest.mark.parametrize("customer_specific", [False, True])
def test_verified_mapping_resolves_conflicting_catalogue_names(db_session, customer_specific):
    from test_order_intake import _draft, _item, _setup

    from app.api.sku_mappings import SkuMappingCreate, create_or_update_sku_mapping
    from app.modules.orders.intake import OrderIntake

    raw, product = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    product.t_code = "11-028"
    product.name_en = "GREEN PAPAYA"
    product.name_th = product.name_vi = "มะละกอดิบ"
    other = Product(t_code="1208", name_en="GREEN PAPAYA", name_th="มะละกอ", name_vi="500-600G",
                    box_material=product.box_material, temperature=product.temperature,
                    control_status=product.control_status, produce_type=product.produce_type,
                    weight_class=product.weight_class)
    db_session.add(other)
    item = _item(40, unit="kg", pack="1pc", name="GREEN PAPAYA", code="11-028")
    item.alt_names = ["มะละกอ", "500-600G"]
    raw.acknowledgment_summary = {"drafts": [_draft(item).model_dump(mode="json")]}
    db_session.flush()
    issues = OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor).unresolved
    assert len(issues) == 1
    assert issues[0].catalog_name_match is True

    create_or_update_sku_mapping(SkuMappingCreate(
        main_customer_id=raw.main_customer_id if customer_specific else None,
        client_sku_code="11-028", client_raw_name="GREEN PAPAYA",
        product_id=product.id, standard_pack_size="1pc"), db=db_session, actor=actor)

    result = OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor)
    assert result.unresolved == []
    assert result.resolved_product_rows == 1
    assert OrderIntake(db_session).resolve(item, raw.main_customer_id).id == product.id
    # Matching a product must not silently supply a piece weight for a kilogram order.
    assert len(result.quantity_issues) == 1
    assert not result.can_split


@pytest.mark.parametrize(("name", "alt_names", "code", "name_match"), [
    ("  LEMONGRASS  ", [], "OTHER", True),
    ("Customer wording", ["ตะไคร้"], "OTHER", True),
    ("Customer wording", ["Sa\u0309"], "OTHER", True),
    ("Lemongrass", [], "CUSTOMER-SKU", True),
    ("New dragon fruit variety", [], "NEW", False),
    ("Lemongras", [], "NEW", False),
])
def test_unresolved_indicators_use_exact_multilingual_names_not_suggestions(db_session, name, alt_names, code, name_match):
    from test_order_intake import _draft, _item, _setup

    raw, product = _setup(db_session)
    other = Product(t_code="OTHER", name_en="Other product", name_vi="Other name", name_th="Other Thai",
                    box_material=product.box_material, temperature=product.temperature,
                    control_status=product.control_status, produce_type=product.produce_type,
                    weight_class=product.weight_class)
    db_session.add(other)
    item = _item(1, name=name, code=code)
    item.alt_names = alt_names
    raw.acknowledgment_summary = {"drafts": [_draft(item).model_dump(mode="json")]}
    db_session.flush()

    result = OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=db_session.get(User, raw.uploaded_by))
    assert len(result.unresolved) == 1
    assert result.unresolved[0].catalog_name_match is name_match
    assert not result.can_split
    if name == "Lemongras":
        assert result.unresolved[0].suggestions


def test_verified_customer_mapping_clears_unknown_sku_indicator(db_session):
    from test_order_intake import _draft, _item, _setup

    from app.api.sku_mappings import SkuMappingCreate, create_or_update_sku_mapping
    from app.modules.orders.intake import OrderIntake

    raw, product = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    item = _item(1, name=product.name_en, code="CUSTOMER-SKU")
    raw.acknowledgment_summary = {"drafts": [_draft(item).model_dump(mode="json")]}
    db_session.flush()
    assert OrderIntake(db_session).resolve(item, raw.main_customer_id) is None

    create_or_update_sku_mapping(SkuMappingCreate(main_customer_id=raw.main_customer_id,
        client_sku_code=item.product_code, client_raw_name=item.product_name,
        product_id=product.id, standard_pack_size="100g"), db=db_session, actor=actor)

    result = OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor)
    assert result.unresolved == []
    assert result.can_split
    assert OrderIntake(db_session).resolve(item, raw.main_customer_id).id == product.id


def test_staff_remapping_verifies_an_existing_unverified_mapping(db_session):
    from test_order_intake import _setup

    from app.api.sku_mappings import SkuMappingCreate, create_or_update_sku_mapping
    from app.models.master_data import ClientSkuMapping

    raw, product = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    mapping = ClientSkuMapping(main_customer_id=raw.main_customer_id, client_sku_code="OLD",
                               client_raw_name="Imported name", product_id=product.id, is_verified=False)
    db_session.add(mapping)
    db_session.flush()
    result = create_or_update_sku_mapping(SkuMappingCreate(main_customer_id=raw.main_customer_id,
        client_sku_code="OLD", client_raw_name="Imported name", product_id=product.id),
        db=db_session, actor=actor)
    assert result.is_verified


def test_matrix_validation_surfaces_unknown_skus_before_split(db_session, tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Orders"
    sheet.cell(3, 6, "CLIENT-1")
    sheet.append([])
    sheet.cell(5, 1, "T001")
    sheet.cell(5, 2, "Rau")
    sheet.cell(5, 3, "Herb")
    sheet.cell(5, 5, "1kg")
    sheet.cell(5, 6, 2)
    sheet.cell(6, 1, "UNKNOWN")
    sheet.cell(6, 2, "La moi")
    sheet.cell(6, 3, "Unknown leaf")
    sheet.cell(6, 5, "1kg")
    sheet.cell(6, 6, 1)
    path = tmp_path / "order.xlsx"
    workbook.save(path)

    agent = User(
        email="agent1@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    product = Product(
        t_code="T001",
        name_vi="Rau",
        name_en="Herb",
        name_th="Pak",
        box_material=BoxMaterial.FOAM,
        temperature=TemperatureZone.COLD,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["1kg"],
    )
    db_session.add_all([agent, product])
    db_session.flush()
    raw_file = RawOrderFile(
        file_name=path.name,
        storage_url=str(path),
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=agent.id,
        status=OrderStatus.UPLOADED,
    )
    db_session.add(raw_file)
    db_session.commit()

    result = OrderValidationService(db_session).validate(raw_file_id=raw_file.id, actor=agent)

    assert raw_file.status == OrderStatus.PARSED
    assert result.total_product_rows == 2
    assert result.resolved_product_rows == 1
    assert [issue.product_code for issue in result.unresolved] == ["UNKNOWN"]


def test_matrix_validation_finds_alt_language_name_but_requires_unknown_sku_review(db_session, tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Orders"
    sheet.cell(3, 6, "CLIENT-1")
    sheet.cell(5, 1, "LEGACY-212")
    sheet.cell(5, 2, "Me Manila")
    sheet.cell(5, 3, "Manila tamarind")
    sheet.cell(5, 5, "500g")
    sheet.cell(5, 6, 1)
    path = tmp_path / "multilingual-order.xlsx"
    workbook.save(path)

    agent = User(
        email="agent1-multilingual@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    product = Product(
        t_code="22-234",
        name_vi="มะขามเทศ",
        name_en="Manila Tamarind",
        name_th="มะขามเทศ",
        box_material=BoxMaterial.FOAM,
        temperature=TemperatureZone.COLD,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.FRUIT,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["500g"],
    )
    db_session.add_all([agent, product])
    db_session.flush()
    raw_file = RawOrderFile(
        file_name=path.name,
        storage_url=str(path),
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=agent.id,
        status=OrderStatus.UPLOADED,
    )
    db_session.add(raw_file)
    db_session.commit()

    result = OrderValidationService(db_session).validate(raw_file_id=raw_file.id, actor=agent)

    assert result.resolved_product_rows == 0
    assert len(result.unresolved) == 1
    assert result.unresolved[0].catalog_name_match is True


def test_matrix_can_be_revalidated_after_staff_maps_an_unresolved_sku(db_session, tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    workbook.create_sheet("Order Info").append(["Quantity unit", "pack"])
    sheet.title = "Orders"
    sheet.cell(3, 6, "CLIENT-1")
    sheet.cell(5, 1, "NEW-001")
    sheet.cell(5, 2, "New leaf")
    sheet.cell(5, 3, "New leaf")
    sheet.cell(5, 5, "100g")
    sheet.cell(5, 6, 2)
    path = tmp_path / "remappable-order.xlsx"
    workbook.save(path)

    agent = User(
        email="agent1-remap@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    db_session.add(agent)
    db_session.flush()
    raw_file = RawOrderFile(
        file_name=path.name,
        storage_url=str(path),
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=agent.id,
        status=OrderStatus.UPLOADED,
    )
    db_session.add(raw_file)
    db_session.commit()

    first = OrderValidationService(db_session).validate(raw_file_id=raw_file.id, actor=agent)
    assert not first.can_split

    product = Product(
        t_code="NEW-001",
        name_vi="New leaf",
        name_en="New leaf",
        name_th="New leaf",
        box_material=BoxMaterial.CARDBOARD,
        temperature=TemperatureZone.NORMAL,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.LEAFY,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["100g"],
    )
    db_session.add(product)
    db_session.commit()

    second = OrderValidationService(db_session).validate(raw_file_id=raw_file.id, actor=agent)

    assert second.can_split
    assert second.unresolved == []


def test_split_consolidates_a_purchasing_batch(db_session, tmp_path):
    from app.api.orders import split_order_matrix
    from app.models.procurement import MasterPurchasingList

    workbook = Workbook()
    sheet = workbook.active
    workbook.create_sheet("Order Info").append(["Quantity unit", "pack"])
    sheet.title = "Orders"
    sheet.cell(3, 6, "CLIENT-1")
    sheet.cell(5, 1, "T001")
    sheet.cell(5, 2, "Rau")
    sheet.cell(5, 3, "Herb")
    sheet.cell(5, 5, "1kg")
    sheet.cell(5, 6, 2)
    path = tmp_path / "split-order.xlsx"
    workbook.save(path)

    agent = User(
        email="agent1-split@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    product = Product(
        t_code="T001",
        name_vi="Rau",
        name_en="Herb",
        name_th="Pak",
        box_material=BoxMaterial.FOAM,
        temperature=TemperatureZone.COLD,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["1kg"],
    )
    db_session.add_all([agent, product])
    db_session.flush()
    raw_file = RawOrderFile(
        file_name=path.name,
        storage_url=str(path),
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=agent.id,
        status=OrderStatus.UPLOADED,
    )
    db_session.add(raw_file)
    db_session.commit()

    split_order_matrix(raw_file.id, db=db_session, actor=agent)

    batch = db_session.query(MasterPurchasingList).one()
    assert batch.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert batch.total_skus_count == 1
    assert raw_file.status == OrderStatus.PURCHASING_CONSOLIDATED


def test_legacy_validation_retains_source_rows_for_reopening(db_session, monkeypatch):
    from contextlib import nullcontext

    from test_order_intake import _draft, _item, _setup

    from app.modules.orders import acknowledgment

    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    item = _item(2)
    item.source = {"sheet": "Orders", "row": 5, "raw_quantity": 2}
    draft = _draft(item)
    parses = []

    def parse(*args, **kwargs):
        parses.append(True)
        return {"drafts": [draft.model_dump(mode="json")]}

    monkeypatch.setattr(acknowledgment, "source_path", lambda raw: nullcontext("legacy.xlsx"))
    monkeypatch.setattr(acknowledgment, "parse_document", parse)
    raw_id = raw.id
    first = OrderValidationService(db_session).validate(raw_file_id=raw_id, actor=actor)
    db_session.commit()
    db_session.expire_all()
    second = OrderValidationService(db_session).validate(raw_file_id=raw_id, actor=actor)

    assert len(parses) == 1
    assert first.can_split and second.can_split
    assert acknowledgment.order_drafts(db_session.get(RawOrderFile, raw_id))[0].items[0].source == item.source


def test_validation_batches_profiles_and_reuses_catalogue(db_session):
    from test_order_intake import _draft, _item, _setup

    from app.models.master_data import ClientSkuMapping, ProductPackProfile

    raw, herb = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    items = []
    for index in range(24):
        product = Product(
            t_code=f"PERF-{index}", name_en=f"Herb {index}", name_vi=f"Leaf {index}",
            name_th=f"Thai {index}", box_material=herb.box_material,
            temperature=herb.temperature, control_status=herb.control_status,
            produce_type=herb.produce_type, weight_class=herb.weight_class,
            is_active=index < 12,
        )
        db_session.add(product)
        db_session.flush()
        db_session.add(ClientSkuMapping(client_sku_code=product.t_code,
            client_raw_name=product.name_en, product_id=product.id, is_verified=True))
        db_session.add(ProductPackProfile(product_id=product.id, pack_size="bunch", pack_key="bunch",
            reviewed=True, net_weight_kg="0.1", volume_cm3=100))
        if product.is_active:
            items.append(_item(2, code=product.t_code, name=product.name_en, pack="bunch"))
    raw.acknowledgment_summary = {"drafts": [_draft(*items).model_dump(mode="json")]}
    raw_id, actor_id = raw.id, actor.id
    db_session.commit()
    db_session.expunge_all()
    actor = db_session.get(User, actor_id)
    statements = []

    def capture(conn, cursor, sql, params, context, many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        result = OrderValidationService(db_session).validate(raw_file_id=raw_id, actor=actor)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)

    assert result.can_split and result.resolved_product_rows == 12
    assert len(statements) <= 6, statements
