"""Reproduce local audit checks with dummy DB credentials and retained logs."""

import os
import subprocess
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
output = backend.parent / "docs" / "audits" / "2026-10-26" / "reruns"
output.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env.update(DATABASE_URL="postgresql://audit:audit@127.0.0.1:1/box_audit",
           SUPABASE_DATABASE_URL="postgresql://audit:audit@127.0.0.1:1/box_audit",
           ENVIRONMENT="test", SECRET_KEY="audit-only-dummy-key-never-use-in-production")
env.pop("BOX_TEST_POSTGRES_URL", None)
name = "adversarial-tests-unmasked" if "--runxfail" in sys.argv else "adversarial-tests"
command = [sys.executable, "-m", "pytest", "tests/test_system_resilience_audit.py",
           "-q", "-rx", "--tb=short", f"--junitxml={output / (name + '.xml')}", *sys.argv[1:]]
result = subprocess.run(command, cwd=backend, env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=180)
(output / (name + ".txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
print(result.stdout)
print(result.stderr, file=sys.stderr)
raise SystemExit(result.returncode)
