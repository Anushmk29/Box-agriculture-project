# Security hardening — 12 November 2026

These working-tree changes address the source conditions identified in the audit.
They have not been deployed. The original audit remains a historical source-only
assessment; its three leads are not claims of a reproduced production exploit.

## Changes

- Manual seeding reads `SEED_INITIAL_PASSWORD` from the same Settings/dotenv source
  as the application. Every environment requires an explicit 12–128 character
  secret; the published development password and example placeholder are rejected
  before database work. Normal application startup does not require a seed secret.
- Newly seeded human users must change their password before business access.
  Existing users, passwords, active flags and session versions remain untouched.
  Service accounts require individual credential provisioning/rotation before use.
- Requests with duplicate Content-Type fields receive HTTP 400 before body reads,
  form parsing or upload admission. Existing size/read/parser limits remain active.
- Excel mappings reject duplicate alternate-column numbers. Matching deduplicates
  normalized terms, including legacy saved drafts, while retaining exact/staff
  mapping rules and suggestion ranking.
- `pypdf` is constrained to `>=7.0.1,<8`; the lockfile resolves 7.0.1. Other locked
  package versions are unchanged. References: [cross-reference memory advisory](https://github.com/py-pdf/pypdf/security/advisories/GHSA-59jv-x5x2-gg4c),
  [image memory follow-up](https://github.com/py-pdf/pypdf/security/advisories/GHSA-fhff-j4m8-439h).
- `backend/scripts/reset_operational_data.py` is retired: importing it does not
  load database configuration, and calling it exits without deleting anything.
  The full application reset removes master data too; it is not an operational
  reset replacement. Use a separately reviewed maintenance/backup procedure.

## Manual seeding

Use seeding only for a reviewed new installation. Set a unique
`SEED_INITIAL_PASSWORD` in the backend dotenv file or approved process secret
source before invoking `scripts/seed_master_data.py`. Never use a production
database for tests. Remove the seed secret from normal runtime configuration after
provisioning. Keep the bootstrap secret private to the operator and initial owner.
The owner changes it at first login, then uses normal user administration to issue
individual temporary passwords to other human accounts before handing them over.
Do not distribute the shared bootstrap secret to staff. Provision individual
service-account credentials through the authorized server-side process.

Re-running the seeder does not rotate or reactivate existing accounts. Credential
rotation is a separate owner operation and must include session revocation. Do not
attempt the old development password against a deployed login endpoint.

## Current authorization contract

| Operation | Current allowed audience |
|---|---|
| Simplified packing finalization | Human GENERAL staff and higher; service identities excluded |
| Legacy final-pack approval | Human OWNER_ADMIN |
| Invoice drafting and detailed invoice reads, including internal notes | Human GENERAL_ACCOUNTING and higher, plus AI_AGENT_1 |
| Initial notes supplied during invoice draft creation | Same drafting audience |
| Later internal-note edits and invoice accounting approval/dispatch | Human GENERAL_ACCOUNTING and higher; services excluded |
| Customer-facing printable invoice | Commercial field projection; internal invoice/payment/receipt notes omitted |

This release clarifies existing contracts. Any decision to require owner-only
staff finalization or human-only invoice-note reads needs a separate policy change
across every relevant route, response and role regression.

## Validation

Regression coverage is in `backend/tests/test_security_hardening.py`: dotenv-only
production-shaped dummy settings, missing/existing owner handling, no database
work on invalid seed input, duplicate-header rejection in both orders while
ordinary upload slots are held, normal single-header requests, mapping validation,
bounded matching comparisons, retired-reset refusal and a dummy PDF import through
the real parser worker. Existing workflow/parser/auth regressions are also checked.

Execution uses a disposable Linux source/dependency copy, without dotenv files or
uploads. Its network/PID/IPC namespaces, empty allowlisted dummy environment,
read-only root/source/toolchain mounts, dropped capabilities and resource limits
are preflight-checked. Limits: 1 CPU, 1536 MiB aggregate memory, 64 processes,
2 GiB per-process address space, 120 CPU seconds, 32 MiB per file, 256 MiB scratch
tmpfs and 180 seconds wall time. Runtime scratch files disappear with the namespace.
No production data, identity or endpoint is used. Preflight passed. The focused
backend run completed with **221 passed, 172 skipped** in 105 seconds; all 27 new
hardening cases passed. Skips concern private client workbooks/folders excluded
from the disposable copy. One dependency TestClient deprecation warning was emitted.
The dependency lock check and Ruff lint on every changed Python file also passed.
Passing local regressions does not attest the deployment.

## Owner release gates still requiring the correct environment

Local provisioning/deployment notes do not establish current credential rotation.
The deployment notes also state that a previously connected database belonged to
another application. Confirm the Box project reference, hostname and approved
connected session before any live identity or infrastructure action.

Read-only discovery on 2026-11-12 confirmed that `backend/.env` references Box
project `ayaquhxlbqryfwmjuesm` with `ENVIRONMENT=development`. The root dotenv's
app hostname is a placeholder, so it does not identify a live deployment. The
connected Supabase account lists only `Neko_Salon` (`uxthfnwcttdcbzchueox`), and
the project-scoped connection returns that same unrelated project. Neither
connection currently provides verified access to Box. No account records were
queried, no credentials were tested, and no live settings were changed.

- [ ] Identify the actual staging/production project, deployed image and ingress.
- [ ] Review account provisioning/password-change records without copying secrets.
      Reset accounts whose credential provenance cannot be established; preserve
      owner access, deliver replacements through the approved private channel,
      update service consumers, and revoke prior sessions.
- [ ] Confirm production process environment and secret provenance; never run the
      seeder or any reset script as an automatic upgrade step.
- [ ] Inspect actual exposed database schemas, grants, RLS and callable functions.
- [ ] Confirm the document bucket is private and its policies/keys remain server-only.
- [ ] Verify ingress frame policy, trusted client-IP forwarding and aggregate
      request/resource limits for the selected topology.
- [ ] Verify protected release environments/approvals and tested image digests.
- [ ] Deploy the tested release to isolated staging, perform normal dummy order,
      packing and PDF/invoice smoke checks, then use the existing production release
      approval process. No deployment was performed by this change.

Do not close deployment-dependent audit leads solely because this patch exists.
