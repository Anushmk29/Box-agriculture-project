"""Shipment confirmation must not perform database reads once per shop."""

import pytest
from sqlalchemy import event
from test_order_intake import _draft, _item, _setup
from test_simplified_workflow import headers

from app.models.master_data import Customer
from app.models.order import ClientOrder, ClientOrderItem
from app.models.user import User

pytest_plugins = ["test_simplified_workflow"]


@pytest.mark.parametrize("existing_customers", [True, False])
def test_split_many_shops_batches_reads_and_creates_one_complete_purchase_batch(db_session, transactional_client, existing_customers):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    drafts = [_draft(_item(10), client=f"SHOP-{index}") for index in range(55)]
    if existing_customers:
        for draft in drafts:
            db_session.add(Customer(client_code=draft.client_code, company_name=draft.client_code,
                billing_email="shop@example.com", route_group="TETE", destination_country="France",
                is_main_customer=False, parent_customer_id=raw.main_customer_id))
    raw.acknowledgment_summary = {"drafts": [draft.model_dump(mode="json") for draft in drafts]}
    db_session.commit()
    url = f"/api/v3/orders/{raw.id}"
    assert transactional_client.get(url + "/validate", headers=headers(actor)).json()["can_split"]
    reads = []

    def record_read(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record_read)
    try:
        response = transactional_client.post(url + "/split", headers=headers(actor))
    finally:
        event.remove(engine, "before_cursor_execute", record_read)
    assert response.status_code == 200, response.text
    assert response.json()["total_client_orders_created"] == 55
    assert response.json()["batch_id"]
    assert len(response.json()["sample_orders"]) == 10
    assert db_session.query(ClientOrder).count() == 55
    assert db_session.query(ClientOrderItem).count() == 55
    assert len(reads) <= 45, f"Confirmation made {len(reads)} database reads for 55 shops"
