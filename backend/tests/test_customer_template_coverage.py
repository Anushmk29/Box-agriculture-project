import json
import re
from copy import deepcopy
from pathlib import Path

import openpyxl
import pytest

from app.modules.orders.templates import template_issues
from app.services.excel_profiles import parse_profiles, references

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / 'documents/excel files of all cutomer'
CONTROLS = json.loads((Path(__file__).parent / 'fixtures/customer_order_cells.json').read_text(encoding='utf-8'))
STANDBY = {'440T.xlsx', '51H-2.xlsx', '86L.ods'}


def sample(filename):
    path = SAMPLES / filename
    if not path.exists():
        pytest.skip('Local customer source workbook unavailable')
    return path


@pytest.mark.parametrize('filename', CONTROLS)
def test_customer_samples_reconcile_every_source_order_cell(filename):
    drafts = parse_profiles(str(sample(filename)), references())
    assert all(d.metadata['template']['status'] == 'MATCHED' for d in drafts)
    cells = [f"{i.source['sheet']}!{i.source['quantity_cell']}" for d in drafts for i in d.items]
    assert sorted(cells) == CONTROLS[filename], filename
    assert len(cells) == len(set(cells))
    match = re.match(r'^(\d+[HLT](?:\s*-\s*\d+)?)', filename)
    if match:
        assert {d.client_code for d in drafts} == {re.sub(r'\s+', '', match[1])}


def test_inventory_keeps_only_the_three_requested_samples_on_standby():
    if not SAMPLES.is_dir():
        pytest.skip('Local customer source folder unavailable')
    active = {p.name for p in SAMPLES.iterdir() if p.suffix in {'.xlsx', '.xls', '.ods'}} - STANDBY
    assert active == set(CONTROLS)
    definitions = references()
    assert len(definitions) <= 100
    assert not STANDBY.intersection(d['sample'] for d in definitions)


def test_filename_code_wins_over_shared_custno_and_copy_suffix():
    for name in ('02T (8).xlsx', 'order_02T.xlsx', '00000000-0000-0000-0000-000000000000_02T.xlsx'):
        draft = parse_profiles(str(sample('02T.xlsx')), references(), original_name=name)[0]
        assert draft.client_code == '02T'
        assert not draft.metadata['template']['issues']


@pytest.mark.parametrize('name', ['440T.xlsx', '51H-2.xlsx', '19T 68H.xlsx', 'unknown customer.xlsx'])
def test_untrained_or_ambiguous_filename_never_inherits_shared_numeric_customer_id(name):
    draft = parse_profiles(str(sample('02T.xlsx')), references(), original_name=name)[0]
    assert draft.client_code == 'REVIEW'
    assert draft.metadata['template']['status'] == 'REVIEW_REQUIRED'


def test_known_filename_and_printed_customer_conflict_stays_in_review():
    draft = parse_profiles(str(sample('19T (1).xlsx')), references(), original_name='68H.xlsx')[0]
    assert draft.client_code == 'REVIEW'
    assert 'identity conflict' in ' '.join(template_issues([draft]))


def test_customer_name_can_select_its_saved_layout():
    draft = parse_profiles(str(sample('90L.xlsx')), references(), original_name='IMS_DRAMMEN next shipment.xlsx')[0]
    assert draft.client_code == '90L'


@pytest.mark.parametrize('heading', ['Order-117', 'New shop', None])
def test_new_matrix_customer_is_visible_and_blocks_automatic_use(tmp_path, heading):
    definition = deepcopy(next(p for p in references() if p['key'] == 'HUNGFAT-MATRIX'))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['HUNGFAT'])
    ws.append(['code no', 'Name Eng', None, None, 'Packing', 'Order-101', 'Order -103', 'Order-110', 'Order-116', heading])
    ws.append(['11001', 'Lemongrass', None, None, 80, 0, 0, 0, 0, 5])
    path = tmp_path / 'Hungfat.xlsx'
    wb.save(path)
    drafts = parse_profiles(path, [definition])
    assert 'Unmapped customer/order column' in ' '.join(template_issues(drafts))


def test_changed_order_heading_does_not_leave_a_matching_filename_automatic(tmp_path):
    wb = openpyxl.load_workbook(sample('02T.xlsx'))
    wb.active['K4'] = 'Delivered'
    path = tmp_path / '02T.xlsx'
    wb.save(path)
    drafts = parse_profiles(path, references())
    assert drafts[0].metadata['template']['status'] == 'REVIEW_REQUIRED'


def test_unlabelled_order_entry_is_retained_for_review(tmp_path):
    wb = openpyxl.load_workbook(sample('02T.xlsx'))
    ws = wb.active
    ws['D5'] = ws['F5'] = ws['G5'] = ws['H5'] = None
    ws['K5'] = 3
    path = tmp_path / '02T.xlsx'
    wb.save(path)
    item = next(i for d in parse_profiles(path, references()) for i in d.items if i.source['quantity_cell'] == 'K5')
    assert item.source['raw_quantity'] == item.quantity == 3
    assert 'Order entry has no product description' in item.quantity_issues


def test_65h_uses_requested_order_and_retains_reference_and_notes(tmp_path):
    item = parse_profiles(str(sample('65H.xlsx')), references())[0].items[0]
    assert item.source['quantity_cell'] == 'F3'
    assert item.quantity == 0.25  # G3=0.35 is an actual quantity, not the requested order.
    assert item.source['raw_customer_product_code'] == 1532
    definition = deepcopy(next(p for p in references() if p['key'] == '65H'))
    definition['sheets'][0]['fields']['notes'] = 14
    wb = openpyxl.load_workbook(sample('65H.xlsx'))
    wb.active['N3'] = 'Keep separate'
    path = tmp_path / '65H.xlsx'
    wb.save(path)
    item = parse_profiles(path, [definition])[0].items[0]
    assert item.notes == item.source['raw_notes'] == 'Keep separate'


def test_text_orders_keep_all_lines_and_unknown_units_visible():
    drafts = parse_profiles(str(sample('82L.xlsx')), references())
    item = next(i for d in drafts for i in d.items if i.source['quantity_cell'] == 'A22')
    assert item.quantity == 15
    assert item.unit == 'พวง'
    assert item.quantity_issues
    item = parse_profiles(str(sample('427T.xlsx')), references())[0].items[0]
    assert item.quantity == 15  # The dash separates the name from a positive amount.
    assert item.unit == 'stk'
    assert item.quantity_issues


def test_historical_705h_requires_an_order_column_decision():
    drafts = parse_profiles(str(sample('705H.xlsx')), references())
    assert 'Confirm the shipment order column' in ' '.join(template_issues(drafts))


def test_real_customer_samples_fit_the_isolated_upload_worker():
    from app.core.parser_process import parse_document

    profiles = references()
    for filename in ('02T.xlsx', 'Order Tonio 01.09.26 FULL REVISED (2).xlsx'):
        result = parse_document(str(sample(filename)), mode='universal', profiles=profiles,
                                original_name=filename, shipment_date='2026-09-01')
        cells = [f"{i['source']['sheet']}!{i['source']['quantity_cell']}" for d in result['drafts'] for i in d['items']]
        assert sorted(cells) == CONTROLS[filename]


@pytest.mark.parametrize('extra', [
    {'review_issues': 'not a list'},
    {'text_quantity_separator': '/'},
    {'ignored_columns': {'513': 'Order'}},
])
def test_format_review_validates_new_contract_fields(extra):
    from fastapi import HTTPException

    from app.api.order_templates import validate_definition

    definition = deepcopy(references()[0])
    if 'ignored_columns' in extra:
        definition['sheets'][0].update(extra)
    else:
        definition.update(extra)
    with pytest.raises(HTTPException) as error:
        validate_definition(definition)
    assert error.value.status_code == 422


@pytest.mark.parametrize('filename,count,client', [
    ('ALINDAR (1).pdf', 52, 'PARIS-STORE'),
    ('Order ITM ETD 06-09-2026 (1).pdf', 15, 'ITM-ITALY'),
])
def test_active_pdf_samples_keep_all_source_order_lines(filename, count, client):
    from app.services.pdf_order_parser import PdfOrderParser

    draft = PdfOrderParser(str(sample(filename)), shipment_date_reference='2026-09-07').parse()
    assert draft.client_code == client
    assert len(draft.items) == count
    if client == 'PARIS-STORE':
        banana = next(i for i in draft.items if i.product_code == '990214')
        durian = next(i for i in draft.items if i.product_code == '990246')
        assert (banana.quantity, banana.unit) == (5, 'box')
        assert (durian.quantity, durian.unit) == (4, 'box')
        assert durian.source['supplier_code'] == '22-206'
        assert banana.quantity_issues and durian.quantity_issues
        assert all(i.source['pdf_line'] for i in draft.items)


def test_pdf_unknown_or_negative_quantities_are_visible_for_review():
    from app.services.pdf_order_parser import PdfOrderParser

    parser = object.__new__(PdfOrderParser)
    draft = parser._parse_alindar_paris_store('PARIS STORE\n990300 Fruit 4 CRATES\n990301 Herb -2 KG', 'order.pdf')
    assert len(draft.items) == 2
    assert all(i.quantity_issues for i in draft.items)
    assert draft.items[1].source['raw_quantity'] == '-2'
    assert draft.items[1].action.value == 'ORDER'
