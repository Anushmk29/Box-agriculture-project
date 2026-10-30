"""Build a source inventory and bounded local measurements; never connects to PostgreSQL."""

import ast
import hashlib
import importlib.metadata
import json
import os
import statistics
import time
from pathlib import Path

os.environ.update(DATABASE_URL="postgresql://audit:audit@127.0.0.1:1/box_audit",
                  SUPABASE_DATABASE_URL="postgresql://audit:audit@127.0.0.1:1/box_audit",
                  ENVIRONMENT="test", SECRET_KEY="audit-only-dummy-key-never-use-in-production")

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.database import Base
from app.main import app
from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    ProduceType,
    TemperatureZone,
    WeightClass,
)
from app.modules.packing.optimizer import (
    DeterministicPackingOptimizer,
    PackingDemand,
    PackingOption,
)
from app.modules.packing.planning import lock_inputs

root = Path(__file__).resolve().parents[2]
output = root / "docs" / "audits" / "2026-10-26"
output.mkdir(parents=True, exist_ok=True)
paths = []
for folder, suffixes in [("backend/app", {".py"}), ("backend/alembic", {".py"}),
                         ("backend/tests", {".py"}), ("backend/scripts", {".py"}),
                         ("web/app", {".ts", ".tsx", ".css"}), ("web/components", {".ts", ".tsx"}),
                         ("web/lib", {".ts", ".tsx"}), ("web/tests", {".ts"})]:
    paths.extend(p for p in (root / folder).rglob("*") if p.suffix in suffixes and "__pycache__" not in p.parts)
records = []
for path in sorted(paths):
    content = path.read_bytes()
    record = {"path": path.relative_to(root).as_posix(), "lines": len(content.splitlines()),
              "sha256": hashlib.sha256(content).hexdigest()}
    if path.suffix == ".py":
        tree = ast.parse(content, filename=str(path))
        record["symbols"] = [{"name": node.name, "line": node.lineno}
                             for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    records.append(record)
schema = app.openapi()
routes = [{"method": method.upper(), "path": path, "operation": data.get("operationId"), "tags": data.get("tags", [])}
          for path, methods in sorted(schema["paths"].items())
          for method, data in methods.items() if method in {"get", "post", "put", "patch", "delete"}]
models = [{"table": table.name, "columns": len(table.columns),
           "indexes": [{"name": index.name, "columns": [col.name for col in index.columns], "unique": index.unique} for index in table.indexes],
           "constraints": [{"kind": type(c).__name__, "name": c.name} for c in table.constraints]}
          for table in Base.metadata.sorted_tables]
inventory = {"source": "current dirty working tree; enumeration is not a claim of line-by-line review",
             "files": records, "api_operations": routes, "tables": models,
             "versions": {package: importlib.metadata.version(package) for package in ["fastapi", "starlette", "sqlalchemy", "pydantic", "bcrypt", "PyJWT", "openpyxl", "pypdf"]}}
(output / "source-inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")

# The intentionally small benchmark stops at 800 packs; no stress on shared services.
measurements = []
for count in [50, 100, 200, 400, 800]:
    demand = PackingDemand("dummy", "1kg", count, 1, BoxMaterial.FOAM, TemperatureZone.COLD,
                           ControlStatus.COMBINABLE, ProduceType.HERB, WeightClass.LIGHT, None, 1000)
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        plan = DeterministicPackingOptimizer().solve(demands=[demand],
            options=[PackingOption("dummy-box", BoxMaterial.FOAM, 1, 2000)], route_code="AUDIT")
        samples.append(time.perf_counter() - started)
    measurements.append({"packs": count, "boxes": len(plan.boxes),
                         "median_seconds": round(statistics.median(samples), 6), "samples_seconds": samples})

# Capture actual generated SELECTs for lock_inputs; SQLite is not evidence of lock behavior.
engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
statements = []
with Session(engine) as db:
    event.listen(engine, "before_cursor_execute", lambda _c, _u, statement, _p, _ctx, _many: statements.append(statement))
    lock_inputs(db, None)

(output / "bounded-measurements.json").write_text(json.dumps({
    "optimizer_one_pack_per_box": measurements,
    "locking_query_shape_only": statements,
    "warning": "Local algorithm timing only. No HTTP capacity, database latency, concurrency, or production availability claim."
}, indent=2), encoding="utf-8")
print(json.dumps({"files": len(records), "lines": sum(row["lines"] for row in records),
                  "operations": len(routes), "tables": len(models), "optimizer": measurements}, indent=2))
