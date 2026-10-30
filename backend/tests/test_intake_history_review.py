import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from app.modules.orders.history_review import review_records


def test_history_review_reports_candidates_without_changing_records():
    rows = {"price_lists": [
        {"id": "one", "product_id": "herb", "pack_size": "1kg", "valid_from": "20260901", "valid_to": None},
        {"id": "two", "product_id": "herb", "pack_size": "1000g", "valid_from": "2026-10-01", "valid_to": None},
        {"id": "bag", "product_id": "herb", "pack_size": "1kg bag", "valid_from": "2026-10-01", "valid_to": None},
        {"id": "tray", "product_id": "herb", "pack_size": "1kg tray", "valid_from": "2026-10-01", "valid_to": None},
    ], "raw_order_files": [{"id": "bad", "shipment_target_date": "2026-02-31"}],
        "customer_order_context": [{"id": "order", "customer_id": "shop", "main_customer_id": "wrong", "parent_customer_id": "actual"}]}
    before = deepcopy(rows)
    report = review_records(rows)
    assert rows == before
    assert report["automatic_corrections"] is False
    assert {row["kind"] for row in report["issues"]} == {
        "noncanonical_date", "invalid_date", "customer_parent_review", "equivalent_pack_aliases_require_dated_review"
    }
    aliases = [row for row in report["issues"] if row["kind"].startswith("equivalent")]
    assert len(aliases) == 1 and aliases[0]["row_ids"] == ["one", "two"]


def test_history_review_marks_truncated_results_as_incomplete():
    rows = {"raw_order_files": [{"id": str(i), "shipment_target_date": "bad"} for i in range(1001)]}
    report = review_records(rows)
    assert report["issues_truncated"] is True
    assert report["issue_count"] == 1001
    assert len(report["issues"]) == 1000


def test_history_cli_never_falls_back_to_application_database(tmp_path):
    env = os.environ.copy()
    env.pop("SUPABASE_STAGING_DATABASE_URL", None)
    script = Path(__file__).resolve().parents[1] / "scripts/inspect_r2_history.py"
    result = subprocess.run([sys.executable, str(script), "--output", str(tmp_path / "review.json")],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "never falls back" in result.stderr
    assert not (tmp_path / "review.json").exists()


def test_history_cli_connection_errors_do_not_print_credentials(tmp_path):
    env = os.environ.copy()
    env["SUPABASE_STAGING_DATABASE_URL"] = "invalid-url-with-private-password"
    script = Path(__file__).resolve().parents[1] / "scripts/inspect_r2_history.py"
    result = subprocess.run([sys.executable, str(script), "--output", str(tmp_path / "review.json")],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    assert "private-password" not in result.stdout + result.stderr
    assert not (tmp_path / "review.json").exists()
