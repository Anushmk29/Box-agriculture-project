"""One purchase may supply standard packs and reviewed weight orders together."""

from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import event
from test_procurement_handoff import setup_procurement_batch
from test_simplified_workflow import headers

from app.models.audit import AgentHandoff
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus
from app.modules.handoffs.service import canonical_sha256

pytest_plugins = ["test_simplified_workflow"]


def mixed_batch(db):
    buyer, _, fixed, batch, purchase = setup_procurement_batch(db)
    weight = ClientOrder(raw_order_file_id=fixed.raw_order_file_id, customer_id=fixed.customer_id,
        order_ref_id="ORD-WEIGHT", status=OrderStatus.PURCHASING_CONSOLIDATED,
        total_ordered_items_count=0, total_estimated_net_weight_kg=5.5)
    db.add(weight)
    db.flush()
    db.add(ClientOrderItem(client_order_id=weight.id, product_id=purchase.product_id,
        raw_produce_text="Herb ordered by weight", pack_size="1kg", ordered_pack_count=0,
        estimated_net_weight_kg=5.5, quantity_source={"lines": [{"weight_source": "REVIEWED_ORDER_WEIGHT"}]}))
    purchase.ordered_net_weight_kg = Decimal("15.5")
    purchase.total_required_weight_kg = Decimal("17.05")
    db.commit()
    db.expire(fixed.raw_file, ["client_orders"])
    return buyer, fixed, weight, batch, purchase


def test_mixed_purchase_requires_actual_count_instead_of_prefilling_fixed_count(db_session, transactional_client):
    buyer, _, _, batch, _ = mixed_batch(db_session)
    response = transactional_client.get(f"/api/v3/procurement/batches/{batch.id}", headers=headers(buyer))
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["ordered_pack_count"] == 10 and item["ordered_weight_kg"] == 15.5
    assert item["pack_count_pending"] is True


@pytest.mark.parametrize("packs, kg, expected", [
    (16, 16, (10, 10, 6, 5.5)),
    (15, 14.5, (10, 10, 5, 4.5)),
    (8, 12, (8, 8, 0, 0)),
    (20, 8, (8, 8, 0, 0)),
    (0, 0, (0, 0, 0, 0)),
])
def test_mixed_purchase_confirms_once_without_duplicating_received_stock(db_session, transactional_client, packs, kg, expected):
    buyer, fixed, weight, batch, purchase = mixed_batch(db_session)
    payload = {"items": [{"item_id": purchase.id, "purchased_pack_count": packs, "purchased_weight_kg": kg}]}
    url = f"/api/v3/procurement/batches/{batch.id}/confirm"
    for _ in range(2):
        response = transactional_client.put(url, headers=headers(buyer), json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "HANDOFF_01_SENT"
    left, right = fixed.items[0], weight.items[0]
    assert (left.confirmed_pack_count, float(left.confirmed_net_weight_kg),
            right.confirmed_pack_count, float(right.confirmed_net_weight_kg)) == expected
    assert left.confirmed_pack_count + right.confirmed_pack_count <= packs
    assert left.confirmed_net_weight_kg + right.confirmed_net_weight_kg <= Decimal(str(kg))
    assert left.ordered_pack_count == 10 and right.ordered_pack_count == 0
    assert float(left.estimated_net_weight_kg) == 10 and float(right.estimated_net_weight_kg) == 5.5
    handoff = db_session.query(AgentHandoff).one()
    assert handoff.payload_sha256 == canonical_sha256(handoff.payload_json)
    lines = [line for order in handoff.payload_json["confirmed_orders"] for line in order["items"]]
    assert {line["quantity_basis"] for line in lines} == {"WEIGHT", "PACKS"}


def test_large_mixed_batch_confirmation_batches_database_work(db_session, transactional_client):
    buyer, fixed, _, batch, purchase = mixed_batch(db_session)
    for index in range(53):
        order = ClientOrder(raw_order_file_id=fixed.raw_order_file_id, customer_id=fixed.customer_id,
            order_ref_id=f"ORD-ADDITIONAL-{index}", status=OrderStatus.PURCHASING_CONSOLIDATED,
            total_ordered_items_count=1, total_estimated_net_weight_kg=1)
        order.items.append(ClientOrderItem(product_id=purchase.product_id, raw_produce_text="Herb",
            pack_size="1kg", ordered_pack_count=1, estimated_net_weight_kg=1))
        db_session.add(order)
    db_session.commit()
    db_session.expire_all()
    calls = []

    def count_calls(connection, cursor, statement, parameters, context, executemany):
        calls.append(" ".join(statement.split())[:90])

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_calls)
    try:
        response = transactional_client.put(f"/api/v3/procurement/batches/{batch.id}/confirm",
            headers=headers(buyer), json={"items": [{"item_id": purchase.id,
                "purchased_pack_count": 70, "purchased_weight_kg": 70}]})
    finally:
        event.remove(engine, "before_cursor_execute", count_calls)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "HANDOFF_01_SENT"
    assert len(calls) <= 70, f"Confirmation used {len(calls)} database round trips for 55 shops: {Counter(calls)}"
    contract = db_session.query(AgentHandoff).one().payload_json
    assert len(contract["confirmed_orders"]) == 55
    lines = [line for order in contract["confirmed_orders"] for line in order["items"]]
    assert sum(line["confirmed_pack_count"] for line in lines) == 70
    assert sum(Decimal(str(line["confirmed_net_weight_kg"])) for line in lines) == Decimal("68.5")
