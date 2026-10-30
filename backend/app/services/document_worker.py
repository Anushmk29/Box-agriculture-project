"""One parser invocation. No credentials or database access are needed here."""

import json
import sys
from pathlib import Path


def main():
    request = json.loads(sys.stdin.readline())  # Parent installs the job limit before sending.
    if sys.platform != "win32":
        import resource
        memory = request["memory_mb"] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (request["cpu_seconds"], request["cpu_seconds"] + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (9 * 1024 * 1024, 9 * 1024 * 1024))
    from app.core.document_limits import inspect_document, validate_drafts
    from app.core.work_limits import MAX_RESULT_BYTES, WorkLimit
    from app.modules.orders.intake import matrix_to_drafts
    from app.services.matrix_parser import MatrixOrderParser
    from app.services.pdf_order_parser import PdfOrderParser
    from app.services.unified_excel_parser import UnifiedExcelParser
    try:
        path = request["path"]
        inspect_document(path)
        acknowledgment = None
        if request["mode"] == "matrix":
            parser = MatrixOrderParser(path)
            acknowledgment = parser.inspect_sub_customers()
            drafts = matrix_to_drafts(parser.parse_all_clients(), Path(path).name)
        elif Path(path).suffix.lower() == ".pdf":
            drafts = [PdfOrderParser(path, shipment_date_reference=request.get("shipment_date")).parse()]
        else:
            if "profiles" in request:
                from app.services.excel_profiles import parse_profiles
                drafts = parse_profiles(path, request["profiles"], original_name=request.get("original_name"), selected_key=request.get("selected_key"))
            else:
                drafts = UnifiedExcelParser(path).parse()
        validate_drafts(drafts)
        result = {"drafts": [draft.model_dump(mode="json") for draft in drafts], "acknowledgment": acknowledgment}
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False).encode()
        if len(encoded) > MAX_RESULT_BYTES:
            raise WorkLimit("Parsed document exceeds the result-size budget; split the input")
    except (WorkLimit, MemoryError):
        encoded = json.dumps({"error": "limit"}).encode()
    except Exception:
        encoded = json.dumps({"error": "invalid"}).encode()
    Path(request["output"]).write_bytes(encoded)


if __name__ == "__main__":
    main()
