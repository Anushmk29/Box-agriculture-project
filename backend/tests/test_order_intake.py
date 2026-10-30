import pytest

from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    Customer,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.user import User, UserRole
from app.modules.orders.intake import OrderIntake, matrix_to_drafts, pack_weight_kg
from app.services.canonical_schema import CanonicalDraftItem, CanonicalOrderDraft, IngestionChannel


def _setup(db):
    user = User(email="ops@example.com", password_hash="x", full_name="Ops", role=UserRole.GENERAL)
    main = Customer(
        client_code="TONIO",
        company_name="Tonio",
        billing_email="t@example.com",
        route_group="TETE",
        destination_country="France",
        is_main_customer=True,
    )
    herb = Product(
        t_code="T001",
        numeric_code="11001",
        name_en="Lemongrass",
        name_vi="Sả",
        name_th="ตะไคร้",
        box_material=BoxMaterial.CARDBOARD,
        temperature=TemperatureZone.NORMAL,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
    )
    db.add_all([user, main, herb])
    db.flush()
    raw = RawOrderFile(
        file_name="m.xlsx",
        storage_url="m.xlsx",
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=user.id,
        main_customer_id=main.id,
        status=OrderStatus.PARSED,
    )
    db.add(raw)
    db.commit()
    return raw, herb


def _draft(*items, client="CT-A"):
    return CanonicalOrderDraft(
        source_file="x", channel=IngestionChannel.PDF_TABLE, client_code=client, items=list(items)
    )


def _item(qty, unit="pack", pack="100g", name="Lemongrass", code=None):
    return CanonicalDraftItem(
        raw_text=name, product_name=name, product_code=code, pack_size=pack, quantity=qty, unit=unit
    )


def test_pack_weight_parses_specs():
    assert pack_weight_kg("100g") == 0.1
    assert pack_weight_kg("80 g") == 0.08
    assert pack_weight_kg("1 kg") == 1.0
    assert pack_weight_kg("loose") == 0.15


def test_materialize_dedups_lines_and_derives_weight(db_session):
    raw, herb = _setup(db_session)
    result = OrderIntake(db_session).materialize(
        raw_file=raw,
        drafts=[_draft(_item(10), _item(5, name="Sả"), _item(2, unit="kg", pack="500g", code="11-001"))],
    )
    (order,) = result.orders
    items = {(i.pack_size, i.ordered_pack_count, float(i.estimated_net_weight_kg)) for i in order.items}
    assert items == {("100g", 15, 1.5), ("500g", 4, 2.0)}
    assert all(i.quantity_source["lines"] for i in order.items)
    assert order.customer.parent_customer_id == raw.main_customer_id
    assert result.lines_total == 3 and result.lines_unmatched == 0


def test_reupload_replaces_lines_instead_of_duplicating(db_session):
    raw, _ = _setup(db_session)
    intake = OrderIntake(db_session)
    intake.materialize(raw_file=raw, drafts=[_draft(_item(10))])
    intake.materialize(raw_file=raw, drafts=[_draft(_item(4))])
    assert db_session.query(ClientOrderItem).count() == 1
    assert db_session.query(ClientOrderItem).one().ordered_pack_count == 4


def test_reupload_cannot_replace_an_order_in_procurement(db_session):
    raw, _ = _setup(db_session)
    intake = OrderIntake(db_session)
    (order,) = intake.materialize(raw_file=raw, drafts=[_draft(_item(10))]).orders
    order.status = OrderStatus.PROCUREMENT_CONFIRMED
    with pytest.raises(ValueError, match="cannot be replaced"):
        intake.materialize(raw_file=raw, drafts=[_draft(_item(4))])


def test_unmatched_lines_require_verified_mapping(db_session):
    raw, _ = _setup(db_session)
    intake = OrderIntake(db_session)
    with pytest.raises(ValueError, match="Unresolved products for CT-A"):
        intake.materialize(raw_file=raw, drafts=[_draft(_item(1, name="Durian"))])

    assert db_session.query(ClientOrder).count() == 0


def test_adhoc_appends_to_its_own_order(db_session):
    raw, _ = _setup(db_session)
    intake = OrderIntake(db_session)
    intake.append_adhoc(raw_file=raw, draft=_draft(_item(2)))
    order = intake.append_adhoc(raw_file=raw, draft=_draft(_item(3)))
    assert order.order_ref_id.endswith("-ADHOC")
    assert order.total_ordered_items_count == 5
    assert db_session.query(ClientOrder).count() == 1


def test_matrix_rows_become_drafts():
    parsed = {
        "clients_data": {
            "CT-A": [
                {"t_code": "T001", "name_vi": "Sả", "name_en": "Lemongrass", "pack_size": "100g", "quantity": 3}
            ]
        },
        "clients_groups": {"CT-A": "GROUP CT"},
    }
    (draft,) = matrix_to_drafts(parsed, "m.xlsx")
    assert draft.route_line == "GROUP CT"
    assert draft.items[0].alt_names == ["Lemongrass"]
    assert draft.items[0].unit == "pack"
