from dataclasses import replace
from time import monotonic
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from test_packing_optimizer import demand
from test_request_transaction import request_sessions as request_sessions
from test_source_storage import workbook_bytes

from app.core import parser_process, request_limits, work_limits
from app.core.body_limits import BodyLimits
from app.core.document_limits import inspect_document
from app.core.parser_process import parse_document
from app.core.work_limits import WorkLimit
from app.models.master_data import BoxMaterial, Customer
from app.modules.packing.optimizer import DeterministicPackingOptimizer, PackingOption


def archive(path, xml, *, compressed=False, extra=None):
    with ZipFile(path, "w", compression=ZIP_DEFLATED if compressed else ZIP_STORED) as file:
        file.writestr("xl/worksheets/sheet1.xml", xml)
        if extra:
            file.writestr(*extra)


@pytest.mark.parametrize("reference", ["A10001", "SSS1", "a999999999", "$XFD$1048576", "KN2000"])
def test_dimension_bombs_fail_before_openpyxl(tmp_path, reference):
    path = tmp_path / "bomb.xlsx"
    archive(path, f'<worksheet><dimension ref="A1:{reference}"/></worksheet>')
    with pytest.raises(WorkLimit, match="row/column"):
        inspect_document(path)


def test_actual_cells_are_checked_even_when_declared_dimension_lies(tmp_path):
    path = tmp_path / "lying.xlsx"
    archive(path, '<worksheet><dimension ref="A1"/><sheetData><row><c r="A10001"/></row></sheetData></worksheet>')
    with pytest.raises(WorkLimit):
        inspect_document(path)


def test_archive_expansion_and_cell_count_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "compressed.xlsx"
    archive(path, " " * 1000000, compressed=True)
    with pytest.raises(WorkLimit, match="expansion"):
        inspect_document(path)
    monkeypatch.setattr(work_limits, "MAX_CELLS", 2)
    archive(path, '<worksheet><c r="A1"/><c r="A2"/></worksheet>')
    inspect_document(path)
    archive(path, '<worksheet><c r="A1"/><c r="A2"/><c r="A3"/></worksheet>')
    with pytest.raises(WorkLimit, match="cell"):
        inspect_document(path)


def test_normal_workbook_runs_in_real_isolated_worker_without_database(tmp_path):
    path = tmp_path / "orders.xlsx"
    path.write_bytes(workbook_bytes())
    result = parse_document(str(path), mode="matrix")
    assert result["drafts"][0]["client_code"] == "CT-A"
    assert result["acknowledgment"]["total_sub_customers"] == 1


def test_parser_admission_is_bounded_and_slot_is_reusable(tmp_path):
    assert parser_process.PARSER_SLOTS.acquire(False)
    assert parser_process.PARSER_SLOTS.acquire(False)
    try:
        with pytest.raises(WorkLimit) as error:
            parse_document(str(tmp_path / "unused.xlsx"), mode="matrix")
        assert error.value.status_code == 503
    finally:
        parser_process.PARSER_SLOTS.release()
        parser_process.PARSER_SLOTS.release()
    path = tmp_path / "orders.xlsx"
    path.write_bytes(workbook_bytes())
    assert parse_document(str(path), mode="matrix")["drafts"]


def test_real_worker_timeout_is_killed_and_next_document_still_runs(tmp_path, monkeypatch):
    path = tmp_path / "orders.xlsx"
    path.write_bytes(workbook_bytes())
    with monkeypatch.context() as patch:
        patch.setattr(parser_process, "PARSER_SECONDS", 0.001)
        start = monotonic()
        with pytest.raises(WorkLimit):
            parse_document(str(path), mode="matrix")
        assert monotonic() - start < 4
    assert parse_document(str(path), mode="matrix")["drafts"]


def test_real_worker_memory_limit_fails_closed_and_releases_slot(tmp_path, monkeypatch):
    path = tmp_path / "orders.xlsx"
    path.write_bytes(workbook_bytes())
    with monkeypatch.context() as patch:
        patch.setattr(parser_process, "PARSER_MEMORY_MB", 16)
        with pytest.raises(WorkLimit):
            parse_document(str(path), mode="matrix")
    assert parse_document(str(path), mode="matrix")["drafts"]


def test_pdf_page_budget_is_checked_before_text_extraction(tmp_path):
    from pypdf import PdfWriter
    path = tmp_path / "too-many.pdf"
    writer = PdfWriter()
    for _ in range(201):
        writer.add_blank_page(width=72, height=72)
    writer.write(str(path))
    with pytest.raises(WorkLimit):
        parse_document(str(path), mode="universal")


def test_optimizer_chunks_large_counts_without_per_pack_expansion():
    row = replace(demand("tiny"), pack_count=250000, unit_weight_kg=0.0001, unit_volume_cm3=0.001)
    planner = DeterministicPackingOptimizer()
    plan = planner.solve(demands=[row], options=[PackingOption("large", BoxMaterial.FOAM, 100, 100000)], route_code="CT")
    assert len(plan.boxes) == 1
    assert plan.boxes[0].items[0].pack_count == 250000
    assert planner._checks < 20
    with pytest.raises(WorkLimit, match="250000"):
        planner.solve(demands=[replace(row, pack_count=250001)], options=[], route_code="CT")


def test_optimizer_bounds_output_and_preserves_pure_product_limits(monkeypatch):
    row = replace(demand("pure"), pack_count=6, single_product_limits={"box": 2.0})
    planner = DeterministicPackingOptimizer()
    options = [PackingOption("box", BoxMaterial.FOAM, 20, 100000)]
    plan = planner.solve(demands=[row], options=options, route_code="CT")
    assert [box.items[0].pack_count for box in plan.boxes] == [2, 2, 2]
    monkeypatch.setattr(work_limits, "MAX_BOXES", 2)
    with pytest.raises(WorkLimit, match="box"):
        planner.solve(demands=[row], options=options, route_code="CT")


def test_chunked_optimizer_matches_per_pack_reference_on_representative_small_orders():
    import random

    from app.modules.packing.planning import plan_summary
    options = [PackingOption("small", BoxMaterial.FOAM, 2, 3000), PackingOption("large", BoxMaterial.FOAM, 7, 10000)]
    for seed in range(30):
        randomizer = random.Random(seed)
        demands = [replace(demand(str(index)), pack_count=randomizer.randint(1, 20),
            unit_weight_kg=randomizer.choice([0.125, 0.5, 1]), unit_volume_cm3=randomizer.choice([50, 250, 800])) for index in range(5)]
        planner = DeterministicPackingOptimizer()
        arithmetic = planner.solve(demands=demands, options=options, route_code="CT")
        per_pack = planner.solve(demands=[replace(row, pack_count=1) for row in demands for _ in range(row.pack_count)], options=options, route_code="CT")
        assert plan_summary(arithmetic) == plan_summary(per_pack), seed


def test_request_deadline_rolls_back_and_does_not_leak_to_next_session(request_sessions, monkeypatch):
    from app.core import database
    generator = database.get_db()
    db = next(generator)
    db.add(Customer(client_code="EXPIRED", company_name="Expired", billing_email="x@example.invalid",
                    route_group="CT", destination_country="Thailand"))
    db.flush()
    monkeypatch.setattr(request_limits, "monotonic", lambda: db.info["request_deadline"] + 1)
    with pytest.raises(WorkLimit):
        next(generator)
    with request_sessions() as observer:
        assert observer.query(Customer).count() == 0
        observer.add(Customer(client_code="FRESH", company_name="Fresh", billing_email="f@example.invalid",
            route_group="CT", destination_country="Thailand"))
        observer.commit()


def test_rollback_after_connection_invalidation_does_not_reconnect():
    from sqlalchemy import create_engine, event, text

    engine = create_engine("sqlite://")
    connections = []
    event.listen(engine, "connect", lambda *args: connections.append(1))
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.info["request_deadline"] = monotonic() + 45
            connection.invalidate()
            connection.rollback()
            assert len(connections) == 1
            assert connection.execute(text("SELECT 2")).scalar_one() == 2
            assert "request_deadline" not in connection.info
    finally:
        engine.dispose()


def test_interrupted_query_preserves_original_error_during_cleanup(monkeypatch):
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.info["request_deadline"] = monotonic() + 45

            def interrupt(*args, **kwargs):
                raise KeyboardInterrupt("interrupted query")

            with monkeypatch.context() as patch:
                patch.setattr(engine.dialect, "do_execute", interrupt)
                with pytest.raises(KeyboardInterrupt, match="interrupted query"):
                    with connection.begin_nested():
                        connection.execute(text("SELECT 2"))
            connection.rollback()
            assert connection.execute(text("SELECT 3")).scalar_one() == 3
    finally:
        engine.dispose()


def test_chunked_body_cannot_bypass_declared_size_and_handler_never_runs(monkeypatch):
    from app.core import body_limits
    monkeypatch.setattr(body_limits, "JSON_BYTES", 8)
    app = FastAPI()
    app.add_middleware(BodyLimits)
    calls = []
    @app.exception_handler(WorkLimit)
    async def limited(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=413, content={"detail": str(error)})
    @app.post("/write")
    async def write(request: Request):
        await request.body()
        calls.append(1)
        return {"saved": True}
    def chunks():
        yield b"1234"
        yield b"56789"
    result = TestClient(app).post("/write", content=chunks())
    assert result.status_code == 413 and not calls


def test_storage_response_stream_has_deadline_and_decodes_compressed_json_safely(monkeypatch):
    import gzip

    import httpx

    from app.core import file_storage
    from app.core.file_storage import StorageError, SupabaseStorage
    encoded = gzip.compress(b'{"id":"order-sources","public":false}')
    def response(request):
        return httpx.Response(200, content=encoded, headers={"Content-Encoding": "gzip"})
    store = SupabaseStorage("https://test.supabase.co", "sb_secret_disposable", "order-sources", transport=httpx.MockTransport(response))
    store.check_private()
    ticks = iter([0, 20])
    monkeypatch.setattr(file_storage, "monotonic", lambda: next(ticks))
    with pytest.raises(StorageError, match="budget"):
        store.check_private()
