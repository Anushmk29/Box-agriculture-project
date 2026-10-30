"""Apply the name-checked volume proposal with backup, revision checks and read-back.

No schema changes, approvals, profile rewrites or order-history changes. Defaults
to a local preview; --apply requires the exact configured Supabase project ref.
"""

import argparse
import copy
import csv
import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, make_url, text

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/product-volume-research"
ALLOWED = {
    "packing_volume_l_per_kg", "packing_volume_reviewed", "packing_volume_estimated",
    "packing_revision", "packing_source", "packing_reviewed",
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_review_csv(proposal, readback):
    saved = {p["id"]: p for p in readback}
    fields = ["t_code", "name_en", "name_th", "status", "action", "previous_volume_l_per_kg",
              "proposed_volume_l_per_kg", "saved_volume_l_per_kg", "estimate_low_l_per_kg",
              "estimate_high_l_per_kg", "reference_id", "commodity", "match", "source_url",
              "source_caveat", "reason"]
    with (OUTPUT / "product-volumes-818.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in proposal["products"]:
            record = {key: row.get(key, "") for key in fields}
            record.update(saved_volume_l_per_kg=saved[row["id"]]["packing_volume_l_per_kg"],
                          commodity=row.get("reference", {}).get("commodity", ""),
                          source_url=row.get("source", {}).get("url", ""),
                          source_caveat=row.get("source", {}).get("caveat", ""))
            writer.writerow(record)


def validate_proposal(proposal, snapshot):
    products = proposal["products"]
    assert len(products) == len(snapshot) == 818, "Expected the reviewed 818-row catalogue"
    assert len({p["id"] for p in products}) == len(products)
    assert len({p["t_code"] for p in products}) == len(products)
    before = {p["id"]: p for p in snapshot}
    assert set(before) == {p["id"] for p in products}
    for row in products:
        original = before[row["id"]]
        for key in ("t_code", "name_en", "name_th", "name_vi", "packing_revision"):
            assert row[key] == original[key], (row["t_code"], key)
        assert row["previous_volume_l_per_kg"] == original["packing_volume_l_per_kg"]
        if row["action"] == "SET_ESTIMATE":
            assert row["status"] == "COMMODITY_ESTIMATE"
            assert not row["match"].endswith("NOT_APPLIED")
            assert row["source"]["url"].startswith("https://")
            value = Decimal(row["proposed_volume_l_per_kg"])
            assert 0 < value <= 10000
            expected = Decimal(1) / Decimal(row["reference"]["density_low_kg_per_l"])
            assert abs(value - expected) <= Decimal(".0000005")
            assert not original["packing_volume_reviewed"]
        elif row["action"] == "WITHDRAW_INVALID":
            assert row["status"] == "WITHDRAWN_MISMATCH"
            assert row["proposed_volume_l_per_kg"] is None and row["reason"]
            assert not original["packing_volume_reviewed"]
        else:
            assert row["action"] == "KEEP" and row["proposed_volume_l_per_kg"] is None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-project-ref")
    args = parser.parse_args()
    proposal = json.loads((OUTPUT / "volume-proposal.json").read_text(encoding="utf-8"))
    snapshot_bytes = (OUTPUT / "catalogue-before.json").read_bytes()
    assert hashlib.sha256(snapshot_bytes).hexdigest() == proposal["catalogue_sha256"]
    snapshot = json.loads(snapshot_bytes)
    validate_proposal(proposal, snapshot)
    changes = [r for r in proposal["products"] if r["action"] != "KEEP"]
    summary = {"products": len(snapshot), "changed_rows": len(changes),
               "new_estimates": sum(r["action"] == "SET_ESTIMATE" and r["previous_volume_l_per_kg"] is None for r in changes),
               "refreshed_estimates": sum(r["action"] == "SET_ESTIMATE" and r["previous_volume_l_per_kg"] is not None for r in changes),
               "withdrawn_mismatches": sum(r["action"] == "WITHDRAW_INVALID" for r in changes),
               "status_counts": dict(Counter(r["status"] for r in proposal["products"]))}
    if not args.apply:
        print(encode({"mode": "preview", **summary}))
        return
    assert args.expected_project_ref == proposal["project_ref"], "Explicit expected project ref required"
    config = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / "backend/.env")}
    url = make_url(config.get("DATABASE_URL") or config.get("SUPABASE_DATABASE_URL"))
    project_ref = ((url.username or "").removeprefix("postgres.") if (url.username or "").startswith("postgres.")
                   else (url.host or "").removeprefix("db.").removesuffix(".supabase.co"))
    assert project_ref == args.expected_project_ref, "Configured database differs from expected project"
    engine = create_engine(url.set(drivername="postgresql+psycopg2"),
                           connect_args={"connect_timeout": 15, "sslmode": "require"})
    run_id = f"volumes-20261028-{uuid.uuid4().hex[:12]}"
    backup = ROOT / ".codex-artifact/database-backups" / run_id
    backup.mkdir(parents=True, exist_ok=False)
    baseline = {p["id"]: p for p in snapshot}
    with engine.begin() as db:
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("SET LOCAL statement_timeout = '60s'"))
        db.execute(text("LOCK TABLE products IN SHARE ROW EXCLUSIVE MODE"))
        current = [dict(r) for r in db.execute(text("SELECT * FROM products ORDER BY id")).mappings()]
        assert len(current) == len(snapshot)
        for product in current:
            expected = baseline[product["id"]]
            for key, value in expected.items():
                actual = product[key]
                assert encode(actual) == encode(value), f"Stale catalogue: {expected['t_code']} {key}"
        write_json(backup / "products-before.json", current)
        write_json(backup / "proposal.json", proposal)
        before_by_id = {p["id"]: p for p in current}
        profile_count = db.scalar(text("SELECT count(*) FROM product_pack_profiles"))
        # Protect against accidentally changing historical profile data in this transaction.
        profile_hash = db.scalar(text("SELECT md5(string_agg(to_jsonb(p)::text, '' ORDER BY id)) FROM product_pack_profiles p"))
        events = []
        for row in changes:
            product = before_by_id[row["id"]]
            assert not product["packing_volume_reviewed"]
            assert "volume" not in (product["packing_source"] or {}).get("table", {}).get("manual_fields", [])
            source = copy.deepcopy(product["packing_source"] or {})
            evidence = {k: v for k, v in row.items() if k not in {"id", "packing_revision"}}
            evidence.update(run_id=run_id, applied_at=datetime.now(UTC).isoformat(),
                            approval="DRAFT_REQUIRES_LOCAL_PACK_CHECK")
            source["volume_web_research"] = evidence
            value = Decimal(row["proposed_volume_l_per_kg"]) if row["action"] == "SET_ESTIMATE" else None
            result = db.execute(text("""
                UPDATE products SET packing_volume_l_per_kg=:value,
                    packing_volume_estimated=true, packing_volume_reviewed=false,
                    packing_reviewed=false, packing_revision=packing_revision+1,
                    packing_source=CAST(:source AS json)
                WHERE id=:id AND packing_revision=:revision
            """), {"id": product["id"], "revision": product["packing_revision"],
                     "value": value, "source": encode(source)})
            assert result.rowcount == 1, "Concurrent product change"
            before = {k: product[k] for k in ALLOWED}
            after = {**before, "packing_volume_l_per_kg": value,
                     "packing_volume_estimated": True, "packing_volume_reviewed": False,
                     "packing_reviewed": False, "packing_revision": product["packing_revision"] + 1,
                     "packing_source": source}
            events.append({"id": str(uuid.uuid4()), "target_id": product["id"],
                           "before": encode(before), "after": encode(after)})
        db.execute(text("""
            INSERT INTO audit_logs (id,event_type,actor_id,actor_role,target_table,target_id,before_state,after_state,created_at)
            VALUES (:id,'PRODUCT_VOLUME_WEB_RESEARCH','codex-web-volume-research','SYSTEM','products',:target_id,
                    CAST(:before AS json),CAST(:after AS json),CURRENT_TIMESTAMP)
        """), events)
        after = [dict(r) for r in db.execute(text("SELECT * FROM products ORDER BY id")).mappings()]
        after_by_id = {p["id"]: p for p in after}
        changed_ids = {r["id"] for r in changes}
        for identity, previous in before_by_id.items():
            observed = after_by_id[identity]
            permitted = ALLOWED if identity in changed_ids else set()
            assert {k: encode(v) for k, v in previous.items() if k not in permitted} == {
                k: encode(v) for k, v in observed.items() if k not in permitted
            }, "Unexpected product-column change"
        for row in changes:
            product = after_by_id[row["id"]]
            assert encode(product["packing_volume_l_per_kg"]) == encode(row["proposed_volume_l_per_kg"])
            assert not product["packing_volume_reviewed"] and product["packing_volume_estimated"]
            assert product["packing_revision"] == row["packing_revision"] + 1
        assert profile_count == db.scalar(text("SELECT count(*) FROM product_pack_profiles"))
        assert profile_hash == db.scalar(text("SELECT md5(string_agg(to_jsonb(p)::text, '' ORDER BY id)) FROM product_pack_profiles p"))
        write_json(backup / "products-after.json", after)
    # Verify on a new connection after commit, including each changed value and provenance.
    with engine.connect() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        readback = [dict(r) for r in db.execute(text("SELECT id,t_code,name_en,name_th,packing_volume_l_per_kg,packing_volume_reviewed,packing_volume_estimated,packing_revision,packing_source FROM products ORDER BY t_code")).mappings()]
        by_id = {p["id"]: p for p in readback}
        for row in changes:
            observed = by_id[row["id"]]
            assert encode(observed["packing_volume_l_per_kg"]) == encode(row["proposed_volume_l_per_kg"])
            assert observed["packing_source"]["volume_web_research"]["run_id"] == run_id
        audit_count = db.scalar(text("SELECT count(*) FROM audit_logs WHERE after_state->'packing_source'->'volume_web_research'->>'run_id' = :run"), {"run": run_id})
        assert audit_count == len(changes)
    result = {"status": "applied_and_verified", "run_id": run_id,
              "project_ref": project_ref, **summary, "products_after": len(readback),
              "volumes_after": sum(p["packing_volume_l_per_kg"] is not None for p in readback),
              "reviewed_volumes_after": sum(p["packing_volume_reviewed"] for p in readback),
              "historical_profiles_preserved": profile_count, "audit_events": audit_count,
              "backup": str(backup), "verified_at": datetime.now(UTC).isoformat()}
    write_json(OUTPUT / "catalogue-after.json", readback)
    write_review_csv(proposal, readback)
    write_json(OUTPUT / "apply-result.json", result)
    write_json(backup / "result.json", result)
    print(encode(result))


if __name__ == "__main__":
    main()
