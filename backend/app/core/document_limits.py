"""Reject expansion/dimension traps before spreadsheet libraries allocate cells."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from app.core import work_limits as limits
from app.core.work_limits import WorkLimit


def inspect_document(path: str | Path):
    path = Path(path)
    if not 0 < path.stat().st_size <= limits.MAX_DOCUMENT_BYTES:
        raise WorkLimit("Order documents must be nonempty and at most 25 MB")
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return
    with ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > limits.MAX_ARCHIVE_MEMBERS or len({m.filename for m in members}) != len(members):
            raise WorkLimit("Workbook archive contains too many or duplicate entries")
        if sum(m.file_size for m in members) > limits.MAX_EXPANDED_BYTES:
            raise WorkLimit("Workbook expanded content exceeds the document budget")
        sheets = [m for m in members if re.fullmatch(r"xl/worksheets/[^/]+\.xml", m.filename)]
        if len(sheets) > limits.MAX_SHEETS:
            raise WorkLimit("Workbook exceeds the 40-sheet budget")
        for member in members:
            if (member.flag_bits & 1 or member.file_size > limits.MAX_MEMBER_BYTES
                    or member.file_size > max(member.compress_size, 1) * 200):
                raise WorkLimit("Workbook archive has an encrypted, oversized or excessive-expansion entry")
        cells = 0
        for member in sheets:
            # Stream bounded XML; clear nodes so inspection itself does not grow
            # an in-memory worksheet. Count actual cells as well as dimensions.
            with archive.open(member) as source:
                for _, element in ET.iterparse(source, events=("end",)):
                    tag = element.tag.rsplit("}", 1)[-1]
                    if tag in {"c", "dimension"}:
                        reference = element.attrib.get("r" if tag == "c" else "ref", "A1").split(":")[-1]
                        match = re.fullmatch(r"([A-Z]+)([0-9]+)", reference.replace("$", "").upper())
                        if not match:
                            raise WorkLimit("Workbook contains an invalid cell or dimension reference")
                        if match:
                            column = 0
                            for letter in match[1]:
                                column = column * 26 + ord(letter) - 64
                            if (column > limits.MAX_COLUMNS or int(match[2]) > limits.MAX_ROWS
                                    or column * int(match[2]) > limits.MAX_CELLS):
                                raise WorkLimit("Workbook exceeds the row/column/cell budget (10000 rows, 512 columns, 500000 cells)")
                        if tag == "c":
                            cells += 1
                            if cells > limits.MAX_CELLS:
                                raise WorkLimit("Workbook exceeds the 500000-cell budget")
                    element.clear()


def validate_drafts(drafts):
    if len(drafts) > limits.MAX_DRAFTS or sum(len(d.items) for d in drafts) > limits.MAX_ITEMS:
        raise WorkLimit("Document exceeds 1000 customer drafts or 20000 order items; split the input")
    for draft in drafts:
        for item in draft.items:
            if len(item.raw_text) > 4096 or len(item.product_name or "") > 512:
                raise WorkLimit("Document contains an oversized item description")
    return drafts
