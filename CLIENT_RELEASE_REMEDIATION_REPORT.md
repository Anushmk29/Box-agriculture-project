# FreshFlow Client Release Remediation Report

**Prepared:** 18 September 2026  
**Project:** Fresh Produce Operations Platform  
**Release decision:** **CONDITIONAL NO-GO — the repaired application passes its code and browser test gates, but the external security, production-data, Git, deployment, recovery, and client-acceptance gates below are still mandatory**

## 0. Remediation execution update — 18 September 2026

This section records the implementation and retest performed after the original audit. It supersedes the historical pass/fail statements in sections 2 through 5 where they conflict. The detailed original findings remain below so that the client, engineering team, and release owner retain the root-cause and remediation history.

### 0.1 Current release position

The application is no longer blocked by the original integrated workflow defects. Authentication, order intake, procurement, both signed handoffs, packing weights and locks, invoicing, payment control, receipts, reconciliation, and live dashboards now have working code paths and automated coverage. A live browser walkthrough also loaded the seeded operational data in every main workstation.

The repository must **not** yet be represented as production-ready or delivered as a final client release. The remaining blockers cannot be safely invented or completed from source code alone: the corrupt Git repository must be recovered, canonical product/customer/price/tax/bank data must be approved, the production stack must be built on a functioning Docker host, backup/monitoring procedures must be installed, and the client must execute UAT. The product owner confirmed that previously exposed credentials were rotated and that this release is for one trusted internal company/user context; customer-facing tenancy, BLE/offline, MFA, email/portal, route analytics, and similar expansion items are outside the current release scope.

### 0.2 Verification evidence after remediation

| Gate | Result | Evidence |
|---|---:|---|
| Backend automated suite | **PASS — 53 tests** | Authentication/cookies/roles, upload rejection, validation, procurement authorization/source/reference integrity, handoff integrity, packing lifecycle, scale capture, QR lookup, transfer guards, invoice invariants, current-date/range validation, overpayment rejection, receipt/close flow, HTML escaping, configuration, and readiness. |
| Python formatting and lint | **PASS** | `ruff format` clean and `ruff check app tests scripts alembic` reports no findings. |
| Python import/bytecode | **PASS** | `python -m compileall -q app scripts`. |
| Python installed-package consistency | **PASS** | `python -m pip check` reports no broken requirements. |
| Python project vulnerability audit | **PASS** | Project-scoped `pip-audit` reports no known vulnerabilities after replacing `python-jose`/`ecdsa` with PyJWT. |
| Database migration chain | **PASS** | Clean SQLite upgrade traversed revisions `0001` through `20260917_0004`; `alembic current` reported `20260917_0004 (head)`. |
| Frontend lint and type/build | **PASS** | ESLint clean; Next.js 16 production build and TypeScript checks complete successfully. |
| Browser automation | **PASS — 8 Chrome tests; 1 credentialed test skipped** | Protected redirect, deterministic invalid-login handling, visible session/customer/procurement/packing/catalogue failures, and SKU-search failure handling. The optional real-account navigation/logout scenario is skipped unless `E2E_EMAIL` and `E2E_PASSWORD` are supplied. |
| Frontend production dependency audit | **PASS** | `npm audit --omit=dev` reports zero vulnerabilities. |
| Compose manifest parsing | **PASS** | Root and backend Compose files pass `docker compose config --quiet` with production-shaped environment values. |
| Container image/runtime | **NOT EXECUTED** | The installed Docker daemon is unavailable. This remains a release gate, even though both manifests parse. |
| Git status/diff/release tag | **BLOCKED** | `D:\.git\index` is corrupt (`index file smaller than expected`). No destructive repair was attempted. |

### 0.3 Live browser acceptance exercised

The running application at `http://127.0.0.1:3000` was tested with its real frontend/API integration. The following behaviors were visibly verified:

1. A signed-out request to `/billing` redirects to `/login?next=%2Fbilling`.
2. Valid authentication creates an HttpOnly session and loads the requested protected page.
3. The operations dashboard displays live counts from the API rather than static demonstration values.
4. Order intake loads the main-customer selector, shipment date, route choices, and workbook control; both configured main customers were returned.
5. Procurement loads the latest batch, 56 SKU rows, calculated order/buffer/target totals, editable purchase confirmation fields, buyer-sheet action, and Handoff 01 action.
6. Packing loads group work queues, customer/order references, box counts, design/floor/transfer/weight/lock/Handoff 02 controls, vehicle-load export, and sticker workflow.
7. Billing loads its work queue and exposes draft, approval, payment, printable invoice, official receipt, and close/reconciliation actions.
8. Logout clears the server session, and a later protected navigation is rejected.

The browser walkthrough intentionally avoided changing real operational records. State-changing workflow behavior is covered in the isolated automated database tests.

### 0.4 Implemented backend changes

- Added cookie and bearer authentication with `/auth/me` and `/auth/logout`; cookies are HttpOnly, SameSite, environment-aware, and API authorization still validates the signed token and active user.
- Added router authentication to master data, SKU mappings, procurement, packing, invoicing, operations, and protected order reads; added role gates to commercial mutations.
- Added audit records for SKU mapping and selling-price changes.
- Enforced 25 MB workbook limits, `.xlsx`/`.xlsm` types, sanitized temporary filenames, chunked writes, cleanup, and controlled 4xx responses.
- Required a valid main customer for workbook intake and removed zero-quantity order rows.
- Added multilingual matching and post-mapping revalidation.
- Returned the actual procurement batch UUID and advanced every contributing order through the required workflow states.
- Corrected Handoff 01 zero-quantity handling and made both handoff payloads hash exactly the immutable JSON that is persisted.
- Added packing weight capture with net/gross, item-total, capacity, stage, lock, and role validation.
- Added same-order/route/date, material, capacity, and lock safeguards to box transfers and invalidated stale weights after a material transfer.
- Added final-pack locking for authorized managers/system administrators and Handoff 02 return.
- Added searchable QR identifiers, authenticated scan lookup, correct environment-based scan URLs, and a forward product-code migration.
- Rejected payment amounts above the outstanding balance; added receipt issuance, order reconciliation/close, and integrity hashes.
- Escaped all dynamic data in invoice, receipt, and procurement HTML to prevent stored markup/script injection.
- Added live operational summary data, `/health` liveness, and database-backed `/ready` readiness.
- Enforced strong production secrets and HTTPS `PUBLIC_APP_URL`; removed production default passwords and insecure Compose fallbacks.
- Replaced the vulnerable `python-jose` dependency chain with PyJWT.
- Rejected service principals at the human procurement-confirmation boundary with an explicit 403 instead of an unhandled 500.
- Added missing role enforcement to procurement consolidation, combination, and buffer mutations.
- Validated every raw-file/client-order source before procurement aggregation so nonexistent IDs cannot create apparently valid empty batches.
- Replaced collision-prone count-based procurement references with a stable source fingerprint, preventing one same-day batch from overwriting another.
- Removed the stale July 2026 upload defaults; shipment date and route are now explicit required request fields.
- Changed selling-price default validity to the actual current date, rejected invalid/reversed validity ranges, and removed the hardcoded Thai Airways/TG930 manifest default.
- Removed fabricated invoice issuer/VAT/bank details. Invoice settlement data is now escaped, configuration-driven, all-or-none validated, and withheld until the client approves it.

### 0.5 Implemented frontend/UI changes

- Removed browser `localStorage` authentication and routed API calls through the same-origin `/backend` proxy.
- Added a protected Next.js route guard, destination-preserving login, role-filtered navigation, session display, and server logout.
- Replaced static dashboard figures with live API metrics.
- Added required main-customer selection, product search, unresolved-SKU presentation, mapping, and revalidation to order intake.
- Corrected ad-hoc order and procurement batch contracts and added authenticated buyer-sheet download.
- Added Handoff 01 and Handoff 02 controls.
- Added group-based packing selection so operators do not need to know internal UUIDs; customer cards are keyboard-accessible buttons.
- Added scale net/gross entry, smart transfer, box sizing, final lock, QR sticker generation/printing, scan page, and vehicle-load CSV.
- Added a billing work queue plus printable invoice/receipt and reconcile/close controls.
- Added Playwright release-smoke tests and a repeatable `npm run test:e2e` command.
- Replaced silent failures in the global session, order-customer, SKU-search, procurement, packing-queue, and box-catalogue paths with visible operator errors.

### 0.6 Finding-by-finding remediation status

Status meanings: **Resolved** means the source defect is repaired and tested; **Partial** means meaningful code is complete but a stated criterion or external dependency remains; **Open** means no safe code-only completion is possible or the capability remains deferred.

| ID | Status | Current result and remaining action |
|---|---|---|
| R-001 | **Resolved** | Production-target imports compile; legacy typing failure removed; full backend suite and compile pass. A real Docker build is still required under the deployment gate. |
| R-002 | **Resolved for approved scope** | Authentication, role gates, UI hiding, and mutation audits are implemented. The owner confirmed this is one trusted internal company/user context. Object-level tenant authorization becomes mandatory before any future customer-facing or multi-company access. |
| R-003 | **Resolved by owner confirmation; evidence retained as release record** | Example secrets and production defaults are safe; production rejects weak secrets. The owner confirmed exposed credentials were rotated. Record rotation dates/owners in the private release file without storing secret values. |
| R-004 | **Resolved** | Server cookies, route guard, role navigation, logout, preserved redirects, and browser tests are implemented. |
| R-005 | **Partial / deployment blocker** | Same-origin proxy and configurable backend/public origins are implemented. Final DNS, TLS certificate, reverse proxy, and deployed CORS values still require production verification. |
| R-006 | **Open / release blocker** | Git index corruption remains. Recover from a known-good remote/backup or reconstruct a new repository, review all changes, and create a signed release tag. |
| R-007 | **Open / data blocker** | Canonical Thai catalogue collisions require a business-approved mapping and migration. Do not automatically split or rename commercial SKUs without data-owner approval. |
| R-008 | **Resolved** | Forward migration `20260917_0004` adds external product identifiers and indexes; clean-chain migration passes. |
| R-009 | **Open / current-data blocker** | The unresolved rows in the September workbook need approved product mappings. The UI now supports search/map/revalidate but cannot decide product identity. |
| R-010 | **Resolved** | Product search and pagination are implemented; the UI queries the catalogue instead of assuming the first page is complete. |
| R-011 | **Open / financial-data blocker** | The workflow now requires valid customer context, but approved selling prices and legal customer data must be loaded for every production SKU/pack/customer combination. |
| R-012 | **Resolved** | Consolidation returns `batch_id`; frontend/backend confirmation contracts match. |
| R-013 | **Resolved** | Consolidation advances all contributing raw-file and direct client orders consistently. |
| R-014 | **Resolved** | Zero rows are filtered and Handoff 01 no longer divides by zero. |
| R-015 | **Resolved** | Handoff IDs are assigned before hashing; stored payloads are not mutated after integrity calculation. |
| R-016 | **Resolved** | Both signed handoffs are available in the operator UI. |
| R-017 | **Resolved** | Route/destination logic uses the actual customer route group; NGA behavior targets Norway rather than a hard-coded France path. |
| R-018 | **Partial / scope decision** | Deterministic constraint-aware packing, material/capacity checks, and overrides exist. Obtain client sign-off if the PRD's full optimization/vehicle-planning interpretation is deferred. |
| R-019 | **Resolved** | Transfer ownership, route/date, material, capacity, lock, and stale-weight controls are implemented and tested. |
| R-020 | **Resolved** | Supported net/gross capture exists in API and UI with audit and invariant validation. BLE/offline capture remains a separate deferred capability under R-042. |
| R-021 | **Resolved in code; UAT required** | Real QR generation, correct scan URL/identifier, scan page, and printable sticker exist. Validate the physical printer, paper size, camera, and deployed hostname during UAT. |
| R-022 | **Resolved** | Ad-hoc UI/API contract uses the implemented endpoint. |
| R-023 | **Resolved** | Upload reports parsed/validation state, unresolved rows, and revalidation rather than false success. |
| R-024 | **Resolved** | Invalid/oversized workbook requests return controlled 4xx responses and temporary files are cleaned. |
| R-025 | **Resolved** | Buyer sheet is downloaded through the authenticated API client without hard-coded localhost navigation. |
| R-026 | **Resolved** | Packing group selection and billing work queue replace mandatory UUID typing; UUID entry remains only as an administrator fallback. |
| R-027 | **Resolved** | Dashboard values come from `/operations/summary`. |
| R-028 | **Resolved** | Frontend lint is clean and the procurement loading path no longer emits the reported dependency warning. |
| R-029 | **Partial / data blocker** | Normal upload-to-invoice code path is reachable and tested. Production invoices remain blocked until R-011/R-032 commercial/legal data is complete. |
| R-030 | **Resolved** | Overpayments are rejected before a transaction or receipt is written; regression test passes. |
| R-031 | **Partial** | Printable escaped invoice and official receipt HTML, payment rows, and integrity hash exist. Immutable archived PDF generation, retention, and jurisdiction-specific legal review remain. |
| R-032 | **Open / legal-data blocker** | Production seller/buyer tax IDs and legal identities must be supplied and approved; placeholder legal data must not be invented. |
| R-033 | **Resolved** | System Administrator permissions are accepted consistently for packing and invoicing administration. |
| R-034 | **Resolved** | Paid orders can be reconciled and closed through API and UI; lifecycle test covers the transition. |
| R-035 | **Partial** | Backend coverage increased to 53 tests and Playwright coverage to 9 scenarios (8 deterministic passes and 1 optional credentialed scenario). Add PostgreSQL concurrency, responsive, accessibility, physical-device, and supported-browser coverage as the long-term quality gate. |
| R-036 | **Open / maintenance** | Historical standalone scripts are not release evidence and are not in CI. Convert required scenarios to pytest/Playwright or archive the scripts explicitly. |
| R-037 | **Resolved** | Ruff debt is cleared in application, tests, migrations, and scripts; the runtime annotation defect was removed. |
| R-038 | **Open / future-break risk** | Several frontend pages and service modules remain large. Refactor by workflow only after the release is stabilized, preserving current regression tests. |
| R-039 | **Open / operations blocker** | Structured production telemetry, alerting, trace correlation, durable retry jobs, and dead-letter handling still require infrastructure decisions and credentials. |
| R-040 | **Open / operations blocker** | Backup ownership, retention, restore runbook, and a timed restore drill must be completed against the real Supabase/storage environment. |
| R-041 | **Partial / compliance** | Critical workflow, packing, handoff, pricing, and receipt actions are audited. A complete append-only ledger, access review, search/export, retention, and tamper controls remain. |
| R-042 | **Deferred by product owner for this release** | QR/scan, dashboard, reconciliation, and close are present. The owner confirmed BLE/offline sync, route analytics, exception queues, email/customer portal, immutable PDF archive, MFA/refresh rotation, and tenant isolation are outside the single-company internal release scope. Reassess before scope or users expand. |

### 0.7 Mandatory work before publication

#### Gate A — Security ownership

1. Record the owner-confirmed credential rotation date and responsible owner in the private release record; never record the values.
2. Store production values in the deployment secret manager; do not copy them into `.env.example`, source control, screenshots, or this report.
3. Re-run login, API authorization, and a database connection smoke test with the rotated credentials in staging.
4. Keep customer-facing and multi-company accounts disabled for this release. If that scope changes, implement and test object-level tenant/customer authorization first.

**Required evidence:** rotation timestamp/owner, secret-manager references (not values), revoked-session confirmation, and staging 401/403 authorization results.

#### Gate B — Repository and release provenance

1. Back up the working directory.
2. Recover Git from the authoritative remote or a known-good backup; do not repair the corrupt index by guessing.
3. Review the complete reconstructed diff, especially the broad formatting changes.
4. Run every command in section 0.9 on the recovered commit.
5. Create a reviewed release branch, immutable tag, and rollback tag/image digest.

**Required evidence:** clean `git status`, reviewed diff/PR, commit SHA, signed tag, and release artifact digest.

#### Gate C — Canonical production data

1. Have the catalogue owner decide every R-007 collision and every unresolved September row.
2. Import approved main/sub-customer legal names, billing addresses/emails, destinations, and tax/VAT identifiers.
3. Import approved selling prices for every customer/product/pack size used by the release workbook, including validity dates and currency.
4. Revalidate the September workbook until unresolved and ambiguous row counts are zero.
5. Generate sample invoice/receipt documents and obtain accounting approval.

**Required evidence:** signed mapping workbook, zero unresolved/ambiguous validation rows, price completeness report, and accounting-approved sample documents.

#### Gate D — Production deployment

1. Configure HTTPS `PUBLIC_APP_URL`, production `CORS_ORIGINS`, Supabase pooler URL, strong `SECRET_KEY`, Redis, and one-time seed password through secrets/configuration.
2. Build both Docker images from the recovered release commit on a host with a working daemon.
3. Start Compose, allow migrations to complete, and verify `/health`, `/ready`, login, one authenticated API call, and the scan hostname.
4. Verify TLS, secure-cookie behavior, reverse-proxy body limits, upload size, network exposure, and container non-root users.
5. Exercise rollback against a disposable environment before production cutover.

**Required evidence:** image build logs, image digests, Compose health status, HTTPS smoke results, migration revision, and rollback record.

#### Gate E — Operations, recovery, and acceptance

1. Configure structured logs, error alerting, uptime/readiness monitoring, and security-event review.
2. Define database/file backup frequency, retention, encryption, owners, RPO, and RTO; complete a restore drill.
3. Convert or archive the stale standalone scripts and publish the supported runbook.
4. Execute client UAT with the physical scanner/printer/scale and a production-shaped order.
5. Record the already-decided single-company internal scope and obtain UAT approval for the capabilities that remain in this release.

**Required evidence:** monitoring screenshots/test alert, restore-drill report, operating runbook, signed UAT, and signed release-scope/deferment list.

### 0.8 Recommended publication sequence

1. Freeze features and recover Git.
2. Verify the recorded credential rotation and configure the production secret manager/environment.
3. Approve and import canonical catalogue/customer/price/tax/bank data.
4. Build images, migrate a staging database, and deploy staging.
5. Run automated gates plus the complete UAT scenario: upload → validate/map → split → consolidate → confirm → Handoff 01 → design → floor work → weights → lock → Handoff 02 → draft invoice → approve → payment → receipt → close.
6. Run backup and rollback drills.
7. Obtain accounting, operations, security, and client scope approvals.
8. Tag and publish the exact approved images, monitor the release, and retain rollback capability.

### 0.9 Repeatable release commands

Run these from clean environments using secret-manager values; never paste real secrets into command history or reports.

```powershell
cd backend
python -m ruff format --check app tests scripts alembic
python -m ruff check app tests scripts alembic
python -m pytest -q
python -m compileall -q app scripts
python -m pip check
python -m pip_audit .
python -m alembic upgrade head

cd ..\web
npm ci
npm run lint
npm run build
npm run test:e2e
npm audit --omit=dev

cd ..
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
```

The E2E login test requires `E2E_EMAIL` and `E2E_PASSWORD` for a non-production test account. The container commands require production-shaped environment values and a functioning Docker daemon.

## 1. Purpose

This report converts the complete code, database, API, browser, workflow, security, and deployment audit into an implementation plan. It identifies what is working, what is broken, why it is broken, the backend and frontend changes required, the tests that must be added, and the evidence required before the system is delivered to a client.

The order of work is intentional. Later workflow features depend on the earlier security, schema, state-transition, and API-contract corrections. Implement the phases in sequence and complete each phase's acceptance criteria before proceeding.

## 2. Executive release assessment

The application contains a solid backend foundation and several tested business invariants, but the integrated product does not currently complete its promised workflow through the browser. A real order can be uploaded, validated, split, and consolidated. The normal workflow then breaks at procurement confirmation. Additional backend-only attempts expose failures in Handoff 01, packing completion, Handoff 02 integrity verification, invoice readiness, and receipt issuance.

The primary release risks are:

1. Production Docker startup can fail under Python 3.12.
2. Master-data and pricing write endpoints are anonymously accessible.
3. A credential-bearing example environment file and default passwords create an immediate security risk.
4. The frontend and backend disagree about procurement batch identifiers.
5. Procurement does not advance client-order statuses, preventing Handoff 01.
6. Zero-quantity rows from a real workbook crash Handoff 01.
7. Persisted handoff JSON does not match its stored integrity hash.
8. Operators have no supported way to enter the weights required for final packing.
9. Browser-uploaded orders lack the main-customer, selling-price, and tax data required for invoicing and receipts.
10. The Thai catalogue seed has merged incompatible product identities.
11. Several required PRD stages have backend-only implementations or are entirely absent.
12. There is no automated frontend or end-to-end contract suite to detect these failures.

## 3. Audit evidence

### 3.1 Passing checks

- Backend automated suite: **27 tests passed**.
- Frontend TypeScript compilation: passed.
- Frontend production build: passed.
- ESLint: passed with one `useEffect` dependency warning.
- npm production dependency audit: zero reported vulnerabilities.
- Python dependency consistency: `pip check` passed.
- Clean SQLite Alembic upgrade and `alembic check`: passed.
- Database foreign-key check: zero violations.
- Product catalogue: 807 products, no duplicate `t_code`, and no completely nameless products.
- Thai/Norway verification script: passed with 45 EL-controlled products and 20 Norway mappings.
- Seed idempotency check: a second run did not insert duplicate products.
- July workbook: 100 customers discovered, 37/37 product rows resolved, 49 orders created, and 69.7 kg extracted.
- Mobile dashboard and procurement layouts rendered without visible clipping at 390 x 844.
- Authenticated procurement reads, packing design, packing locks, handoff creation, invoice creation, payment, and receipt code can work when their missing prerequisites are repaired manually in a disposable database.

### 3.2 Failing or incomplete checks

- Ruff reports 536 findings across application, tests, and scripts; 341 are in application code.
- The Python 3.12-compatible import path fails because `List` is undefined in a runtime annotation.
- Docker image build was not completed because the local Docker daemon was unavailable.
- Git operations fail because the project is attached to a corrupt `D:\.git\index`.
- Two standalone integration scripts are incompatible with current service signatures or authentication requirements.
- The current September workbook resolves only 74 of 90 product rows and cannot split.
- A non-Excel upload returns HTTP 500 instead of a controlled 4xx response.
- There are no frontend unit, component, API-contract, or Playwright end-to-end tests.

### 3.3 Real end-to-end result

The real July workbook followed this path:

1. Upload: passed.
2. Validate: passed, 37/37 products resolved.
3. Split: passed, 49 orders created.
4. Consolidate: passed.
5. Confirm from the UI contract: failed because the UI submits a batch reference where the backend requires a UUID.
6. Confirm through the backend UUID: passed.
7. Handoff 01: first crashed on division by zero; after removing zero rows in the disposable database, it failed because orders had not advanced to `PROCUREMENT_CONFIRMED`.
8. After test-only state repairs, packing design passed.
9. Floor packing started, but final lock failed because there is no supported weight-entry path.
10. After test-only weight injection, final lock and Handoff 02 passed.
11. Invoice generation then rejected Handoff 02 because the stored JSON and stored hash did not match.
12. After a test-only handoff repair, invoicing failed because the order had no main customer and several product/pack combinations had no selling prices.
13. After test-only customer and price repairs, invoice generation and dispatch passed.
14. Receipt issuance failed until seller and buyer tax IDs were manually added.
15. Once those fields were added, a EUR 40 payment against a EUR 32.30 invoice was incorrectly accepted and receipted.

No test-only database repair described above exists in the production application. These repairs were used only to inspect downstream stages.

## 4. Required implementation sequence

### Phase 0: Protect credentials and make production boot deterministic

Complete issues R-001 through R-006 before exposing any environment to a client or external network.

### Phase 1: Repair schema and canonical master data

Complete issues R-007 through R-011 before processing new client orders. Existing product identities and deployment schemas must be corrected before workflow fixes are validated.

### Phase 2: Repair workflow state and API contracts

Complete issues R-012 through R-019. This establishes one reliable backend path from upload through Handoff 02.

### Phase 3: Implement the complete operator UI

Complete issues R-020 through R-028. The browser must expose every required human action without asking operators to supply internal UUIDs.

### Phase 4: Repair invoicing, payment, and documents

Complete issues R-029 through R-034. Financial output must be deterministic, validated, auditable, and deliverable.

### Phase 5: Add automation, observability, and release controls

Complete issues R-035 through R-042, then execute the release certification checklist.

## 5. Detailed findings and remediation

## R-001 — Python 3.12 Docker startup failure

**Severity:** P0 / release blocker  
**Area:** Backend, deployment  
**Location:** `backend/app/services/packing_override_service.py:2,262`

### Observed failure

`List` is used in `Optional[List[str]]` but is not imported. Development currently runs on Python 3.14, whose annotation behavior masks the error. The Dockerfile uses Python 3.12, where importing the module evaluates the annotation and raises `NameError`.

### Root cause

The local runtime and production runtime are different, and the test suite never imports the application under the Docker target version.

### Required change

1. Add `List` to the typing import, or preferably replace old typing aliases with `list[str]` and `dict[str, Any]`.
2. Add `from __future__ import annotations` consistently to backend modules that retain forward or typing annotations.
3. Standardize local development, CI, and Docker on Python 3.12, or explicitly certify another single version and update the Dockerfile and `pyproject.toml` together.
4. Add a CI smoke command that imports `app.main` under the production Python version.

### Required tests

- `python -c "import app.main"` using Python 3.12.
- Start Uvicorn in CI and verify `/health` returns 200.
- Build and start the Docker image, then call `/health` from outside the container.

### Completion criterion

The production image builds, starts, and serves health and OpenAPI requests on a clean machine using no globally installed Python packages.

## R-002 — Anonymous master-data and commercial-price writes

**Severity:** P0 / security blocker  
**Area:** Backend API, authorization  
**Locations:** `backend/app/api/sku_mappings.py:47,93,117`; `backend/app/api/master_data.py:43`; `backend/app/api/orders.py:322`

### Observed failure

The following operations are publicly accessible:

- Read products, boxes, and customers.
- Read and create client SKU mappings.
- Create customer selling prices.
- Read split client orders for any known raw-file UUID.

### Root cause

These routers were added without a router-level `get_current_user` dependency and without per-operation role checks.

### Required backend change

1. Add authentication to every `/master-data`, `/sku-mappings`, and `/orders/{id}/client-orders` route.
2. Define an explicit authorization matrix:
   - Read master data: authenticated operational roles.
   - Modify SKU mappings: Operations Manager or System Administrator.
   - Modify selling prices: Accounting Officer or System Administrator, optionally requiring a second approval.
   - Read customer orders: only roles and tenant/customer scope permitted for that order.
3. Add tenant/main-customer filtering to every lookup. Authentication without object-level authorization is insufficient.
4. Return 404 rather than confirming the existence of unauthorized object IDs where appropriate.
5. Audit every master-data and price mutation with actor, before-state, after-state, timestamp, and reason.

### Required frontend change

- Hide mutation controls from unauthorized roles.
- Display a clear 403 message when a role lacks permission.
- Add an approval state and audit history to price-management screens.

### Required tests

- Parameterized 401/403/200 tests for every role and route.
- Cross-customer object-access tests.
- Audit-log assertion for mapping and price changes.

### Completion criterion

Every non-public OpenAPI operation declares bearer authentication, and automated tests prove both role-level and object-level authorization.

## R-003 — Exposed credentials and unsafe defaults

**Severity:** P0 / security blocker  
**Area:** Secrets, authentication, deployment  
**Locations:** root `.env.example`; `backend/app/core/config.py:18`; `backend/docker-compose.yml:23`; `backend/scripts/seed_master_data.py:50`

### Observed failure

- The root `.env.example` contains a non-placeholder database URL with embedded credentials and a JWT-shaped secret.
- All eight seeded accounts accept `Password123!`.
- Backend configuration and Docker Compose contain predictable fallback secrets.

### Root cause

Development bootstrap values were treated as deployable configuration, and secret validation is not enforced outside application logic.

### Required change

1. Rotate the Supabase/database password and every secret currently present in `.env.example`, regardless of whether the file was committed previously.
2. Replace example values with unmistakable placeholders.
3. Remove secret fallbacks. Production startup must fail if `SECRET_KEY` or the database URL is missing or weak.
4. Require at least 32 random bytes for the signing secret.
5. Remove fixed production user passwords from seeding. Generate one-time activation tokens or random temporary passwords and require a first-login change.
6. Disable or delete service-principal password login. Use separate scoped credentials or signed service tokens.
7. Add secret scanning to CI and pre-commit.
8. Review repository history and deployment logs for exposure; rotate again if the history cannot be proven private.

### Completion criterion

No repository file contains a live credential, the application refuses predictable production secrets, and every deployed user credential is unique and rotated.

## R-004 — Browser authentication is not enforced at route level

**Severity:** P0 / security and UX blocker  
**Area:** Frontend authentication  
**Locations:** `web/lib/api.ts`; application layout/navigation

### Observed failure

Signed-out users can directly load `/`, `/orders`, `/procurement`, `/packing`, and `/billing`. Protected API calls may return 401, but screens remain visible and often display misleading empty states.

### Root cause

Authentication exists only inside individual API calls. There is no route guard, authenticated layout, or centralized 401 handling.

### Required frontend change

1. Move access tokens from `localStorage` to Secure, HttpOnly, SameSite cookies issued by the backend.
2. Add Next.js middleware or a protected server layout for operational routes.
3. Redirect unauthenticated sessions to `/login?returnTo=...`.
4. Centralize 401 handling so an expired session clears local state and returns to login.
5. Render navigation and actions from the authenticated user's role.
6. Add session expiry and renewal behavior; do not use an eight-hour bearer token without refresh rotation.

### Required backend change

- Add short-lived access tokens and rotating refresh tokens.
- Revoke refresh tokens at sign-out.
- Consider MFA for privileged roles before client release.

### Completion criterion

No operational page renders for a signed-out user, expired sessions recover predictably, and JavaScript cannot read the authentication token.

## R-005 — Production URL and CORS configuration is localhost-bound

**Severity:** P0 / deployment blocker  
**Area:** Frontend, backend, Docker  
**Locations:** root and backend Compose files; `backend/app/main.py`; frontend API configuration

### Observed failure

The generated frontend contains localhost API/download URLs, and CORS permits local development origins. A preflight request from a production-style origin returned 400.

### Root cause

Development URLs are embedded at build time and no validated environment-specific deployment configuration exists.

### Required change

1. Define development, staging, and production environment files managed outside source control.
2. Use one canonical public application origin and one internal backend origin.
3. Route browser API traffic through the same public origin or a configured reverse proxy where possible.
4. Parse and validate `CORS_ORIGINS` as an explicit allow-list.
5. Generate download URLs from configuration or return relative URLs.
6. Add HTTPS termination, trusted proxy headers, secure cookies, and HSTS in the production deployment.

### Completion criterion

A staging build accessed through its public hostname performs login, API calls, and downloads without localhost references or permissive wildcard CORS.

## R-006 — Git repository state is corrupt

**Severity:** P0 / release-process blocker  
**Area:** Source control  

### Observed failure

`git status` fails because the project resolves to `D:\.git` and its index is smaller than expected.

### Root cause

The project is either inside an accidental drive-level repository or the intended repository metadata is missing/corrupt.

### Required change

1. Back up the current workspace.
2. Determine the intended remote repository and verify its complete history.
3. Clone a clean repository into a project-specific directory.
4. Compare and copy current source changes into a new `codex/...` branch.
5. Do not repair or delete `D:\.git` until every other project that may depend on it has been identified.
6. Require a clean status, valid remote, protected main branch, and reproducible release tag.

### Completion criterion

The project has its own healthy Git root, all intended changes are reviewed, and a clean tagged commit can reproduce the release.

## R-007 — Thai seed merges incompatible product identities

**Severity:** P0 / data-integrity blocker  
**Area:** Master data, seeding  
**Location:** `backend/scripts/seed_thai_master_products.py:171-178`

### Observed failure

Examples in the current database include:

- `11-066`: Vietnamese `Cà nút áo`, English `NEEM TREE`, Thai neem leaf.
- `11-041`: Vietnamese `Lá dứa to`, English `PANDAN LEAVE`, but botanical family `Solanaceae`.
- `11-013`: Vietnamese `Cà sọc`, English `EGG PLANT`, Thai round eggplant.

### Root cause

The seed treats an existing `t_code` as proof of product identity, overwrites selected Thai/English/control fields, and leaves the old Vietnamese name and classifications unchanged.

### Required data change

1. Stop running this seed in production.
2. Export every product touched by the Thai seed into a review sheet with old and proposed values.
3. Have an authorized produce/domain owner decide the canonical identity for every conflicting code.
4. Restore or replace contaminated rows and update dependent mappings, prices, order history references, and labels under a controlled migration.
5. Preserve historical invoice text even if the current product name changes.

### Required code change

1. Treat `t_code`, barcode, Norway code, and names as identifiers with explicit provenance, not interchangeable overwrite keys.
2. If an existing code conflicts on normalized name or botanical family, quarantine it for human review instead of updating it.
3. Make seeds insert immutable source records or use versioned import batches with dry-run and conflict reports.
4. Validate barcode uniqueness and expected check-digit format.

### Completion criterion

Every product code maps to one reviewed product identity across languages, classifications, labels, and selling prices, with a signed-off conflict report.

## R-008 — New product fields lack forward migrations

**Severity:** P0 / deployment-data blocker  
**Area:** Alembic, PostgreSQL  
**Locations:** `backend/app/models/master_data.py:38-50`; `backend/alembic/versions/20260909_0001_initial_schema.py`; `backend/scripts/migrate_db.py`

### Observed failure

`barcode` and `norway_code` exist in the current model but not in later Alembic revisions. A clean database appears correct because the initial migration imports live models and calls `create_all`. An already-deployed database at the recorded Alembic head will not automatically receive model additions. The fallback migration script is SQLite-specific and does not add all new fields.

### Root cause

The initial migration is mutable and schema evolution has been performed through model edits and manual SQLite scripts rather than immutable forward revisions.

### Required change

1. Replace the initial revision's live-model `create_all` behavior with explicit, immutable operations for future clean installs.
2. Create a new forward Alembic revision for every field, index, constraint, enum, and table introduced since the last deployed revision.
3. Include at minimum `barcode`, `norway_code`, their indexes, uniqueness decisions, and any Thai/control fields absent from the deployed schema.
4. Test upgrade from an exact copy of the current production/Supabase schema, not only an empty database.
5. Add downgrade behavior or a documented irreversible migration policy.
6. Remove the SQLite-only migration script from the production runbook.

### Completion criterion

Both a blank PostgreSQL database and a copy of the previous production schema upgrade to the same verified schema using Alembic only.

## R-009 — September workbook contains unresolved products

**Severity:** P1 / operational blocker for current data  
**Area:** Validation, master data  

### Observed failure

The September workbook detected 90 product rows, resolved 74, and left 16 unresolved. Splitting correctly returned 409.

The unresolved codes include `T287`, `T147`, `T314`, `T152`, `T146`, `T212`, `T202`, `T269`, `T200`, `T298`, `T224`, `T122`, `T222`, `T280`, `T211`, and `T194`.

### Root cause

- These legacy/client codes do not exist as canonical `t_code` values.
- The validator passes `name_vi or name_en`, so English is never attempted when a Vietnamese string is present.
- At least five unresolved rows have exact English candidates in master data.
- Some names have multiple possible products and require human selection.

### Required backend change

1. Change the matcher contract to accept all available names and identifiers.
2. Apply deterministic tiers: verified client mapping, exact barcode/code, exact normalized names in every language, approved aliases, fuzzy suggestions, unresolved.
3. Never auto-accept an ambiguous exact name such as two `LOTUS ROOT` products.
4. Return candidate products and confidence in the validation response.
5. Preserve the human's mapping as a client-specific verified mapping for future uploads.

### Required data change

Create reviewed mappings for all 16 unresolved codes before the September workbook is processed for a client shipment.

### Completion criterion

The target September workbook validates with zero unresolved rows, and every new mapping has a recorded reviewer and provenance.

## R-010 — Product list pagination hides most of the catalogue

**Severity:** P1  
**Area:** Backend API, order UI  
**Locations:** `backend/app/api/master_data.py:43`; `web/app/orders/page.tsx:97`

### Observed failure

The catalogue contains 807 products, but the products endpoint defaults to 100 and the mapping UI fetches only that first page. Most products cannot be selected.

### Required change

1. Add server-side search by code, barcode, Norway code, Vietnamese, English, and Thai names.
2. Return a paginated object containing `items`, `total`, `page`, and `page_size`.
3. Replace the full prefetch with a debounced searchable combobox.
4. Display code, multilingual name, control status, and pack compatibility in each option.
5. Avoid raising the default limit to 1,000 as the long-term solution.

### Completion criterion

An operator can locate and select any of the 807 products within the mapping modal, including by barcode and Norway code.

## R-011 — Selling-price and customer setup is incomplete

**Severity:** P0 / financial blocker  
**Area:** Master data, UI, invoicing

### Observed failure

- Only one main customer owns selling prices.
- Several real product/pack combinations have no selling price.
- UI uploads do not set `main_customer_id`.
- Generated subcustomers have no parent main customer or tax ID.

### Root cause

The platform has no enforced onboarding/readiness workflow for main customers, subcustomers, prices, pack sizes, tax IDs, or payment terms.

### Required change

1. Make `main_customer_id` mandatory for order upload and ad-hoc orders.
2. Provide a main-customer selector filtered by the signed-in user's scope.
3. When creating a subcustomer, set `parent_customer_id` and inherit only approved defaults.
4. Add customer administration for legal name, billing email, destination, tax ID, currency, and payment terms.
5. Add a selling-price matrix keyed by main customer, optional subcustomer, product, normalized pack size, currency, and validity range.
6. Add a preflight endpoint that lists missing prices and legal fields before packing or invoice generation.
7. Prevent Handoff 02 or invoice drafting when financial readiness is incomplete, with an actionable UI list.

### Completion criterion

Every product/pack combination in the target shipment has an effective price, and every seller/buyer has complete legal invoice data before packing is finalized.

## R-012 — Procurement response omits the UUID required for confirmation

**Severity:** P0 / workflow blocker  
**Area:** Backend/API contract, frontend  
**Locations:** `backend/app/api/procurement.py:67-73,96`; `web/app/procurement/page.tsx:140`

### Observed failure

The frontend calls `/procurement/batches/{batch_reference}/confirm`. The backend confirmation service queries `MasterPurchasingList.id`, so the request always returns batch not found.

### Root cause

`MasterPurchasingResponse` omits `batch_id`, leaving the frontend with only `batch_reference`.

### Required backend change

1. Add `batch_id` to every procurement response.
2. Keep UUID-based mutation routes and reference-based lookup routes clearly separate.
3. Use generated OpenAPI client types or shared schema generation to prevent drift.

### Required frontend change

- Store `batch_id` and use it for confirmation, buffer changes, and future handoffs.
- Display `batch_reference` only as the operator-facing label.

### Required tests

- API contract test asserting `batch_id` is present.
- Playwright flow that loads the latest batch and confirms it.

### Completion criterion

The procurement confirmation button returns 200 and visibly advances the batch and all orders without manual identifier entry.

## R-013 — Consolidation does not advance client-order statuses

**Severity:** P0 / workflow blocker  
**Area:** Backend state machine  
**Location:** `backend/app/services/purchasing_aggregator.py:125-130`

### Observed failure

After consolidation and confirmation, the batch and raw file reach `PROCUREMENT_CONFIRMED`, while all 49 customer orders remain `SPLIT_AND_VALIDATED`. Handoff 01 then rejects `SPLIT_AND_VALIDATED -> HANDOFF_01_SENT`.

### Root cause

The aggregator sets the raw-file status directly but does not transition its customer orders to `PURCHASING_CONSOLIDATED`. The confirmation service only advances orders already in that intermediate state.

### Required change

1. Move all workflow status mutations behind `WorkflowService`.
2. During consolidation, transition every included raw file and client order to `PURCHASING_CONSOLIDATED` in one transaction.
3. During confirmation, transition the batch, raw files, and included orders to `PROCUREMENT_CONFIRMED` in one transaction.
4. Roll back the entire transaction if any target cannot transition.
5. Record one parent audit event plus per-entity status changes.
6. Make consolidation idempotent or explicitly reject repeats without duplicating batches.

### Completion criterion

After a successful procurement confirmation, the batch, all source files, and every included customer order report `PROCUREMENT_CONFIRMED`.

## R-014 — Zero-quantity rows crash Handoff 01

**Severity:** P0 / workflow blocker  
**Area:** Parser, splitter, handoff allocation  
**Location:** `backend/app/modules/handoffs/service.py:240-247`

### Observed failure

The July workbook produced 31 zero-count order-item rows across five product/pack groups. Handoff 01 divided by a `total_requested` value of zero and returned HTTP 500.

### Root cause

Blank or zero spreadsheet cells are persisted as order items. The allocation service assumes every grouped item has positive quantity.

### Required change

1. Filter zero, blank, negative, NaN, and non-numeric quantities during parsing.
2. Do not create customer orders that contain no positive item quantities.
3. Add database checks requiring non-negative quantities and weights.
4. Defensively skip a zero-total allocation group or raise a controlled domain validation error before division.
5. Return row, sheet, customer, code, and value details for invalid spreadsheet quantities.

### Required tests

- Workbook fixture containing blanks, formulas returning zero, explicit zero, negative values, and text.
- Handoff allocation test with an empty/zero group proving no 500 occurs.

### Completion criterion

The July workbook creates no zero-quantity rows, and Handoff 01 handles all valid groups without unhandled exceptions.

## R-015 — Handoff JSON and integrity hash disagree

**Severity:** P0 / workflow and audit blocker  
**Area:** Backend persistence  
**Locations:** `backend/app/modules/handoffs/service.py:98-109,188-198`

### Observed failure

The API response and database store `handoff_id: null`, while `payload_sha256` was calculated after the in-memory payload received the real ID. Reloading the handoff produces a different hash, and invoicing reports `Handoff 02 integrity verification failed`.

### Root cause

A plain JSON dictionary is assigned to SQLAlchemy, flushed, then mutated in place. SQLAlchemy does not detect the nested mutation reliably, so the second value is not persisted.

### Required change

1. Generate the handoff UUID before constructing the payload.
2. Build the final immutable payload once with its actual ID.
3. Calculate the hash from that final payload.
4. Insert ID, payload, and hash together in one operation.
5. Optionally use an immutable Pydantic handoff contract and canonical serializer.
6. Do not rely on mutable JSON change tracking for integrity-critical records.
7. Add a database constraint for a 64-character lowercase SHA-256 value.

### Required tests

- Create a handoff, commit, close the session, open a new session, reload it, and verify the hash.
- Verify the API payload, persisted payload, and recalculated canonical payload are identical.
- Tamper with a copied payload and prove verification rejects it.

### Completion criterion

Both handoff types survive a new database session with matching payload IDs and hashes.

## R-016 — Handoff operations are absent from the UI

**Severity:** P0 / workflow blocker  
**Area:** Frontend  

### Observed failure

Handoff 01 and Handoff 02 backend routes exist, but no page or automatic orchestration calls them. Operators cannot advance from confirmed procurement to packing or from final packing to invoicing.

### Required change

1. Add an order/batch workflow timeline showing current status and available next actions.
2. After procurement confirmation, allow the authorized Agent 1/System Administrator process to dispatch Handoff 01.
3. After final-pack lock, allow Agent 2/System Administrator to return Handoff 02.
4. Display contract ID, hash, timestamp, sender, recipient, and verification result.
5. Make each action idempotent and disable it after successful dispatch.
6. If agents are intended to dispatch automatically, implement a durable job/outbox and show its progress instead of requiring a button.

### Completion criterion

An authorized user can move a real order through both handoffs from the browser, and the UI visibly confirms verified contracts.

## R-017 — Route and Norway behavior can be applied to the wrong customer

**Severity:** P1  
**Area:** Orders, packing optimizer  
**Locations:** `backend/app/modules/packing/service.py:80`; `backend/app/services/order_splitter.py:48`; `backend/app/api/orders.py:249`

### Observed failure

The Norway/NGA exception is unit-tested, but packing selects the raw file's single `route_line`. Newly generated NGA customers default to destination `France`, including ad-hoc customers.

### Root cause

Shipment route, customer group, and destination country are conflated. Customer creation uses hardcoded defaults.

### Required change

1. Define route and destination as separate validated domain fields.
2. Assign route/destination to each customer order during split, based on reviewed group mapping.
3. Remove hardcoded `France` defaults from NGA paths.
4. Make the optimizer use the client order's resolved route and destination.
5. Add route-specific tests for TETE, NGA/Norway, and mixed-source batches.

### Completion criterion

Each order carries the correct route and destination, and the optimizer selects the correct constraints without consulting a global raw-file fallback.

## R-018 — Packing design is not the full specified optimizer

**Severity:** P1 / scope gap  
**Area:** Backend packing

### Observed behavior

The implementation performs deterministic weight/volume placement and segregation. The PRD describes a more advanced 3D genetic/local-search optimizer.

### Required product decision

Choose one of two explicit release positions:

1. Implement the specified optimizer and benchmark it against representative shipments; or
2. Update the client scope and PRD to state that release one uses deterministic best-fit packing, with manual overrides and measurable constraints.

### Required tests

- Capacity, volume, botanical-family, temperature, controlled-product, route, and customer segregation.
- Determinism for identical inputs.
- Comparison against known feasible/optimal fixtures.

### Completion criterion

The documented optimizer matches what is delivered, and client acceptance fixtures meet their expected box counts and constraints.

## R-019 — Box transfer lacks ownership and capacity safeguards

**Severity:** P1 / data-integrity blocker  
**Area:** Backend packing overrides  
**Location:** `backend/app/services/packing_override_service.py:270-328`

### Observed risk

The transfer operation accepts arbitrary source and target box IDs. It does not prove they belong to the same order/customer/shipment or re-run capacity and compatibility rules after moving items.

### Required change

1. Require both boxes to belong to the same client order and shipment unless an explicit privileged cross-order workflow exists.
2. Lock both rows in a deterministic order to prevent races.
3. Validate item ownership and requested quantities.
4. Re-run material, temperature, botanical-family, control, net-weight, volume, and route constraints on the target box.
5. Reject negative/zero transfers and target overflow.
6. Audit source, target, items, quantities, reason, actor, and before/after weights.

### Completion criterion

Automated tests prove that cross-customer, cross-shipment, incompatible, overweight, and concurrent transfers are rejected atomically.

## R-020 — No supported floor-weight capture

**Severity:** P0 / workflow blocker  
**Area:** Backend and frontend packing  
**Locations:** `backend/app/modules/packing/service.py:158-181`; `web/app/packing/page.tsx`

### Observed failure

Final lock requires `actual_net_weight_kg` and `actual_gross_weight_kg`, but no endpoint or UI writes those fields. The only confirmation action attempts to lock the existing null values.

### Required backend change

1. Add `PATCH /packing/boxes/{box_id}/weights` with net weight, gross weight, capture source, device ID, captured-at timestamp, and optimistic version.
2. Add endpoints for item add/reduce/swap adjustments with required reason codes.
3. Validate gross >= net, net equals item totals within tolerance, and box capacity.
4. Prevent updates after lock.
5. Record every measurement and adjustment in an immutable event history rather than overwriting the only record.
6. Support idempotency keys for offline/mobile retries.

### Required frontend change

1. Build a floor workflow: scan/select box, review expected contents, enter or capture item weights, enter tare/gross weight, resolve variances, and submit.
2. Show live capacity and variance indicators.
3. Keep final confirmation disabled until all boxes pass readiness checks.
4. Display who captured each weight and whether it came from manual entry or BLE scale.

### Completion criterion

A floor operator can complete a shipment from a mobile browser without database intervention, and final lock succeeds only when all physical measurements are valid.

## R-021 — QR, scan, and printable slip functionality is incomplete

**Severity:** P1 / client-visible gap  
**Area:** Backend and frontend packing  
**Locations:** `backend/app/modules/packing/service.py:96-100`; `web/app/packing/page.tsx:893-905`

### Observed failure

The UI renders a generic QR icon and truncated ID. The database stores `/api/v3/scan/{uuid}`, but that route returns 404. No printable slips are generated.

### Required change

1. Define a versioned QR payload containing a non-secret box identifier and checksum/signature.
2. Generate a real QR image using a maintained QR library.
3. Implement a mobile `/scan/{box_id}` page with authorization and offline-safe cached box data.
4. Provide customer-facing and internal printable box slips with required identifiers, route, box count, control markings, and weights.
5. Add print CSS or server-rendered PDF generation.
6. Expire or revoke public scan links if they expose commercial/customer data.

### Completion criterion

A phone can scan a printed label, open the correct authorized box record, and reproduce both required slips.

## R-022 — Ad-hoc order UI calls a nonexistent contract

**Severity:** P1  
**Area:** Frontend/API contract  
**Locations:** `web/app/orders/page.tsx:201-211`; `backend/app/api/orders.py:217-225`

### Observed failure

The UI calls `/orders/{id}/adhoc-item` with a payload shape that does not exist. The backend exposes `/orders/{id}/add-adhoc-order` and expects `client_code`, optional group/notes, and an `items` array.

### Required change

1. Define the API contract in one schema source.
2. Update the UI to submit the backend request shape, including pack size and quantity.
3. Add client-side validation and a post-submit order summary.
4. Make `main_customer_id`, route, destination, and customer association explicit.
5. Add contract and Playwright tests.

### Completion criterion

An operator can add a WhatsApp/ad-hoc order from the browser and see it included exactly once in consolidation.

## R-023 — Upload UI reports false success and hides validation results

**Severity:** P1  
**Area:** Frontend order intake  
**Locations:** `web/app/orders/page.tsx:120-180`; `backend/app/api/orders.py:29-40`

### Observed failure

Immediately after upload, the UI says all produce SKUs matched even though the upload acknowledgement does not contain validation results. The September workbook actually has 16 unresolved products. Validation errors are swallowed into generic behavior.

### Root cause

The acknowledgement and validation contracts are conflated in the UI.

### Required change

1. Model upload, validation, mapping, and split as distinct states.
2. After upload, display only detected sheets/customers and an `Awaiting validation` state.
3. Call validation explicitly and render resolved count, unresolved rows, suggestions, and mapping controls.
4. Permit split only when `can_split` is true.
5. Surface backend errors with actionable text and correlation ID.

### Completion criterion

The UI never claims matching success without validation evidence, and the September workbook displays all 16 unresolved rows before split.

## R-024 — Upload handling returns 500 for invalid files

**Severity:** P1 / availability and security  
**Area:** Backend upload  
**Location:** `backend/app/api/orders.py:63-100`

### Observed failure

Uploading a Markdown file writes it to storage and then raises an uncaught OpenPyXL exception, returning HTTP 500. There is no size limit.

### Required change

1. Enforce allowed extension and MIME/type checks before persistence.
2. Enforce a configured maximum request and workbook size.
3. Inspect ZIP structure safely and reject malformed or encrypted workbooks.
4. Store to a temporary location, parse, then promote only on success.
5. Remove temporary files on every failure path.
6. Sanitize original filenames and never use them as authoritative storage paths.
7. Convert parser failures into structured 400/422 responses.
8. Add rate limits and malware scanning appropriate to the deployment.

### Completion criterion

Invalid, oversized, corrupt, and encrypted uploads return controlled 4xx responses and leave no database or storage orphan.

## R-025 — Buyer-sheet download cannot authenticate

**Severity:** P1  
**Area:** Frontend downloads  
**Locations:** `web/app/procurement/page.tsx:269`; `web/lib/api.ts`

### Observed failure

The buyer sheet is a plain link to a protected endpoint. Browser navigation does not attach the bearer token, so the request returns 401. The URL is also hardcoded to localhost.

### Required change

Preferred solution: move authentication to HttpOnly cookies and use a relative same-origin download URL. Alternative: fetch the document through the authenticated API client as a Blob and trigger a local download. A short-lived signed download URL is also acceptable for controlled external use.

### Completion criterion

The buyer-sheet button downloads the correct document in staging and production without exposing a token in the URL.

## R-026 — Packing and billing require internal UUID entry

**Severity:** P1 / usability blocker  
**Area:** Frontend UX

### Observed failure

Operators must manually enter client-order or invoice UUIDs. These identifiers are not operational references a client user should need to copy.

### Required change

1. Add queue pages filtered by workflow status, shipment date, route, group, customer, and human-readable reference.
2. Link from order intake to procurement, packing, and billing detail pages using selected records.
3. Keep UUIDs internal to URLs/state while displaying references such as order number and invoice number.
4. Add empty, loading, permission, and error states.

### Completion criterion

An operator can locate and process every record using business identifiers and lists, without entering a UUID.

## R-027 — Static dashboard presents demo values as operational data

**Severity:** P1 / client trust risk  
**Area:** Frontend dashboard  
**Location:** `web/app/page.tsx`

### Observed failure

The dashboard displays a hardcoded date, route, customer, safeguards, and workflow labels.

### Required change

1. Replace static values with authenticated API metrics.
2. Add filters for shipment date and route.
3. Show counts by workflow status, unresolved SKU count, procurement shortages, boxes awaiting weight, invoices awaiting approval, overdue balances, and failed jobs.
4. Display `No live data` rather than sample data when empty.
5. Clearly label any demonstration environment.

### Completion criterion

Every operational number on the dashboard can be traced to current database records and changes after a workflow action.

## R-028 — React procurement effect has an unstable dependency

**Severity:** P2  
**Area:** Frontend quality  
**Location:** `web/app/procurement/page.tsx:58`

### Observed warning

ESLint reports that `loadLatestBatch` is missing from a `useEffect` dependency list.

### Required change

Wrap the callback in `useCallback`, include it in the dependency list, or restructure the initial load so the effect has stable dependencies. Ensure the fix does not create repeated requests.

### Completion criterion

Frontend lint completes with zero warnings and the batch loads once per intended trigger.

## R-029 — Invoice generation is unreachable for normal uploads

**Severity:** P0 / financial blocker  
**Area:** Backend and frontend invoicing

### Observed failure

Normal UI uploads produce orders with `main_customer_id = null`. Invoice generation then cannot select seller-specific prices. Even after assigning a seller in the disposable database, real lines lacked approved pack-specific prices.

### Required change

1. Complete R-011 before allowing packing finalization.
2. Add `GET /orders/{id}/invoice-readiness` returning seller, buyer, tax fields, currency, missing prices, handoff verification, and locked-box status.
3. Display readiness blockers before dispatching Handoff 02.
4. Prevent invoice generation from partially writing a draft when any prerequisite is absent.
5. Decide how price changes after packing affect an invoice; snapshot the selected price and validity source.

### Completion criterion

Every packed line resolves to one effective selling price and invoice generation succeeds without database repair.

## R-030 — Overpayments are accepted and receipted

**Severity:** P0 / financial-control blocker  
**Area:** Backend payments  
**Location:** `backend/app/services/on_behalf_invoicing_service.py:271-297`

### Observed failure

A EUR 40 payment against a EUR 32.30 balance was accepted, the invoice was marked paid, and a EUR 40 receipt was issued.

### Root cause

The remaining balance is clamped to zero, but the service does not reject or separately account for the excess amount.

### Required change

Choose and implement one explicit accounting policy:

- **Reject policy:** reject any payment greater than the outstanding balance within currency tolerance; or
- **Credit policy:** record the exact payment, allocate only the outstanding amount, create a customer credit balance for the excess, and issue documents showing both allocation and credit.

Also:

1. Use `Decimal`, not binary floats, for all money input and calculations.
2. Enforce positive amounts and currency consistency.
3. Make bank references unique within the relevant account/tenant.
4. Add reversal/refund handling rather than editing confirmed payments.
5. Require an accounting role and immutable audit record.

### Completion criterion

Overpayment behavior matches the approved accounting policy and is covered by exact-cent tests.

## R-031 — Receipt and invoice documents are incomplete

**Severity:** P1 / client-delivery gap  
**Area:** Backend documents, frontend

### Observed behavior

- Invoice HTML renders.
- Receipt endpoint returns JSON.
- There is no immutable PDF, digital seal/signature, email dispatch, customer portal, or delivery status.

### Required change

1. Generate immutable invoice and receipt PDFs from versioned templates.
2. Snapshot legal names, addresses, tax IDs, prices, currency, payment reference, and box/weight data into the document record.
3. Store content hash, template version, generation time, and storage URL.
4. Add review/approve/dispatch states and email delivery status.
5. Provide re-download without silently regenerating changed content.
6. Add retention and access-control policies.

### Completion criterion

Approved invoices and receipts are immutable, downloadable PDFs whose hashes remain stable across repeated downloads.

## R-032 — Tax identifiers are absent for seeded and generated customers

**Severity:** P1 / receipt blocker  
**Area:** Customer onboarding

### Observed failure

Receipt generation first rejected the seller tax ID and then the buyer tax ID. Generated customers contain placeholder names/emails and no legal fields.

### Required change

1. Require complete legal profiles before financial activation.
2. Separate an operational destination/contact from a legal billing entity.
3. Add validation appropriate to each destination country without assuming every country uses the same VAT format.
4. Mark generated workbook customers as `INCOMPLETE` and block invoicing until reviewed.
5. Provide bulk customer onboarding/import and duplicate detection.

### Completion criterion

All shipment customers required for billing have reviewed legal data, and no placeholder identity reaches a final invoice.

## R-033 — System Administrator permissions are inconsistent

**Severity:** P2 / operational risk  
**Area:** Authorization design

### Observed behavior

System Administrator can perform many actions but cannot confirm final packing or generate an invoice, while it can approve and record payments. Some restrictions may be intentional, but the role model is undocumented and inconsistent.

### Required change

1. Define a written permission matrix for every workflow action.
2. Decide whether System Administrator is a break-glass role, a superuser, or a configuration-only role.
3. Encode permissions in one policy module instead of repeated role lists.
4. Require reason and enhanced audit logging for break-glass operations.

### Completion criterion

Every route has a reviewed policy test matching the approved permission matrix.

## R-034 — Close/archive and reconciliation stages are missing

**Severity:** P1 / workflow scope gap  
**Area:** Backend and frontend

### Missing functionality

- Reconcile ordered, confirmed, packed, invoiced, and paid quantities.
- Explain shortages, substitutions, damage, and variance.
- Close a shipment only after all mandatory financial and operational gates.
- Seal final ledgers and archive artifacts.
- Reopen through an audited exception workflow.

### Required change

1. Add a reconciliation projection per order and shipment.
2. Add variance thresholds and required reason codes.
3. Add `READY_TO_CLOSE`, `CLOSED`, and controlled `REOPENED` transitions if consistent with the domain model.
4. Prevent edits to closed operational and financial records.
5. Produce a closeout report containing handoff hashes, box totals, invoices, payments, receipts, and unresolved exceptions.

### Completion criterion

A shipment can be closed from the browser only when reconciliation passes, and its closeout package can be reproduced and verified.

## R-035 — Automated tests do not cover frontend/backend contracts

**Severity:** P0 / quality-system blocker  
**Area:** Testing

### Observed gap

There are no frontend tests. Current tests did not detect the procurement identifier mismatch, ad-hoc endpoint mismatch, handoff persistence failure, real-workbook zero division, or unreachable weight capture.

### Required test architecture

1. **Backend unit tests:** parsing, matching, state transitions, pricing, money, optimizer constraints.
2. **Backend API tests:** request/response schemas, authentication, authorization, invalid inputs, idempotency.
3. **Database integration tests:** PostgreSQL migrations, transaction rollback, constraints, concurrent updates, JSON persistence across sessions.
4. **Frontend component tests:** loading/error/permission states and forms.
5. **Generated contract types:** produce frontend types from OpenAPI in CI and fail on uncommitted drift.
6. **Playwright E2E:** real user flows for every role.
7. **Real workbook fixtures:** sanitized July, September, NGA/Norway, corrupt, empty, formula, and large workbooks.

### Mandatory E2E scenarios

- Sign in/out and session expiry.
- Upload, inspect, validate, map unresolved SKU, and split.
- Add an ad-hoc order.
- Consolidate, edit buffer, download buyer sheet, confirm procurement.
- Handoff 01, packing design, scan, weigh, adjust, lock, Handoff 02.
- Invoice readiness, draft, approve, dispatch, partial payment, final payment, receipt, reconciliation, close.
- Every restricted action attempted by an unauthorized role.
- NGA/Norway route and packing rules.

### Completion criterion

CI runs the complete suite against PostgreSQL and the production frontend build, and no release-blocking flow depends on manual database repair.

## R-036 — Standalone test scripts are stale and outside CI

**Severity:** P1  
**Area:** Testing and maintenance

### Observed failure

- `test_api_endpoints.py` uses obsolete unauthenticated behavior.
- `test_client_updates_pipeline.py` calls the invoice service without the required `actor` argument.
- `test_new_docs_workflow.py` uses a hardcoded database and obsolete document path.
- Thai/Norway verification is not collected by pytest.

### Required change

1. Convert valuable scripts into pytest tests with fixtures and temporary databases.
2. Delete or archive obsolete scripts after their assertions are covered.
3. Prohibit tests from hardcoding the development database.
4. Add a marker for slower workbook/E2E tests while keeping them in CI release jobs.

### Completion criterion

Every maintained test is discoverable through documented commands and runs without modifying the developer or client database.

## R-037 — Lint debt contains real runtime defects

**Severity:** P1  
**Area:** Backend quality

### Observed result

Ruff reports 341 application findings, including the undefined `List` that can break Docker startup. Duplicate imports and inconsistent typing increase change risk.

### Required change

1. Fix all `F`-class errors first: undefined names, unused imports that hide incorrect structure, and redefinitions.
2. Apply formatting and safe automated fixes separately from behavioral changes.
3. Configure Ruff rules deliberately and document justified exceptions.
4. Add Ruff to CI with zero errors.
5. Refactor large mixed-responsibility modules after behavioral tests exist.

### Completion criterion

Ruff and frontend lint pass with zero errors and zero warnings in CI.

## R-038 — Oversized modules obscure domain boundaries

**Severity:** P2 / future-break risk  
**Area:** Maintainability

### Observed risk

The packing page is approximately 933 lines, and the financial service combines invoice creation, approval, payments, receipt generation, document data, hashing, and auditing.

### Required change

After regression coverage exists:

1. Split frontend pages into workflow containers, domain hooks, form components, tables, and status panels.
2. Separate invoice drafting, pricing, payment allocation, receipt issuance, and document rendering services.
3. Centralize workflow policies and shared money/weight value objects.
4. Keep transaction boundaries in application services, not UI or persistence helpers.

### Completion criterion

Each module has one clear responsibility, while behavior remains protected by existing tests.

## R-039 — No production observability or durable background jobs

**Severity:** P1 / operations blocker  
**Area:** Deployment and operations

### Missing capability

There are no defined metrics, distributed traces, job retries, dead-letter handling, or operational alerts for parsing, handoffs, document generation, and email delivery.

### Required change

1. Add structured JSON logs with correlation, shipment, order, batch, invoice, and actor IDs.
2. Add metrics for request failures, workflow transitions, unresolved SKUs, job age, document failures, and payment exceptions.
3. Use a durable outbox/job queue for agent handoffs, PDFs, email, and external integrations.
4. Add readiness/liveness endpoints that check required dependencies separately.
5. Define actionable alerts and a support runbook.

### Completion criterion

A staging failure can be located from an alert to the exact workflow record and correlation ID without reading raw database tables.

## R-040 — Backup, recovery, and retention are undefined

**Severity:** P1 / operations blocker  
**Area:** Database and document storage

### Required change

1. Define recovery point and recovery time objectives with the client.
2. Enable managed PostgreSQL point-in-time recovery and encrypted backups.
3. Back up immutable invoice/receipt/packing artifacts separately.
4. Test restoration into an isolated environment.
5. Define retention and deletion policies for order files, audit logs, invoices, receipts, and personal data.
6. Document incident recovery and credential rotation.

### Completion criterion

A timed restore drill recovers a consistent database and documents within the agreed objectives.

## R-041 — Audit records are not a complete immutable ledger

**Severity:** P1 / compliance risk  
**Area:** Audit

### Observed gap

Core actions produce audit rows, but the table is mutable and does not consistently capture user agent, source, correlation ID, category, reason, or hash chaining.

### Required change

1. Make audit writes append-only at the database permission level.
2. Record actor type, role, source IP where lawful, user agent, correlation ID, event category, reason, before/after state, and timestamp.
3. Hash-chain or externally seal high-value financial and handoff events.
4. Add searchable audit UI and export.
5. Avoid recording passwords, tokens, or sensitive payment data.

### Completion criterion

Privileged, financial, master-data, and workflow changes can be reconstructed from immutable audit events.

## R-042 — Several promised product capabilities remain absent

**Severity:** P1 / scope and acceptance risk  
**Area:** Product scope

### Missing or partial capabilities

- BLE scale capture and offline synchronization.
- Real QR scan workflow and printable slips.
- Reconciliation and close/archive.
- Executive dashboard and route analytics.
- Diagnostics and operational exception queues.
- SMTP/email delivery and customer portal.
- Immutable PDF invoices and receipts.
- Full backup/DR and observability implementation.
- PRD-grade MFA, refresh rotation, service-principal authentication, and tenant isolation.

### Required decision

For every item, either implement it before release or create a client-approved release-scope document that explicitly defers it. Static labels such as `Phase 1–3 foundation` do not replace scope agreement.

### Completion criterion

The signed release scope, UI, API, tests, and client demonstration all describe the same capabilities.

## 6. Backend change checklist

- [ ] Fix Python 3.12 annotation/import failure.
- [ ] Add authentication and object authorization to every master-data and order-read endpoint.
- [ ] Replace unsafe secrets and password seeds.
- [ ] Add immutable forward PostgreSQL migrations.
- [ ] Repair contaminated master-product data.
- [ ] Extend multilingual matching and candidate responses.
- [ ] Require main customer for upload/ad-hoc orders.
- [ ] Add `batch_id` to procurement responses.
- [ ] Transition raw files and orders transactionally during consolidation/confirmation.
- [ ] Filter zero-quantity rows and defend allocation against zero totals.
- [ ] Persist final handoff payload and hash atomically.
- [ ] Add weight-capture and item-adjustment endpoints.
- [ ] Add scan and slip/PDF endpoints.
- [ ] Enforce box-transfer ownership and constraints.
- [ ] Add invoice-readiness validation.
- [ ] Complete price, legal customer, and tax-ID requirements.
- [ ] Enforce approved overpayment policy using `Decimal`.
- [ ] Add reconciliation and close/archive services.
- [ ] Add append-only audit, idempotency, and correlation IDs.
- [ ] Add structured errors instead of unhandled 500 responses.

## 7. Frontend/UI change checklist

- [ ] Implement protected routes and cookie-based sessions.
- [ ] Render navigation and actions by role.
- [ ] Add main-customer selection at intake.
- [ ] Separate upload acknowledgement, validation, mapping, and split states.
- [ ] Add searchable, paginated product mapping.
- [ ] Correct the ad-hoc order route and payload.
- [ ] Use `batch_id` for procurement confirmation.
- [ ] Implement authenticated buyer-sheet download.
- [ ] Add workflow timeline and both handoff actions/statuses.
- [ ] Replace UUID text inputs with queues and business-reference selectors.
- [ ] Implement mobile floor scanning, weighing, variance resolution, and final lock.
- [ ] Render real QR codes and printable slips.
- [ ] Add customer/legal profile and price administration.
- [ ] Add invoice readiness, review, PDF preview, dispatch, payment, and receipt screens.
- [ ] Add reconciliation and shipment closeout UI.
- [ ] Replace all static dashboard values with live data.
- [ ] Add accessible labels, keyboard navigation, focus handling, loading skeletons, and error states.
- [ ] Resolve all lint warnings.

## 8. Data correction and migration runbook

1. Freeze production writes and take a verified backup.
2. Rotate exposed credentials before connecting a remediation build.
3. Export current schema and Alembic revision from the deployed PostgreSQL database.
4. Build forward migrations against that exact schema.
5. Export the product catalogue and generate a Thai-seed conflict report.
6. Obtain domain-owner approval for every conflicting code.
7. Migrate corrected canonical products and update mappings/prices transactionally.
8. Import reviewed mappings for the 16 September workbook SKUs.
9. Complete main-customer and subcustomer legal profiles.
10. Import a complete, effective-dated selling-price matrix by pack size.
11. Run orphan, duplicate, foreign-key, invalid-status, zero-quantity, and missing-price queries.
12. Run both target workbooks through staging.
13. Compare source totals, split totals, procurement totals, packed totals, and invoice totals.
14. Obtain business sign-off before removing the write freeze.

## 9. Required CI pipeline

Every pull request should execute:

1. Secret scan.
2. Backend Ruff and formatting check.
3. Backend type check if adopted.
4. Backend unit and API tests on Python 3.12.
5. PostgreSQL migration from blank schema.
6. PostgreSQL migration from the previous release snapshot.
7. Frontend lint and TypeScript checks.
8. Frontend production build.
9. OpenAPI client generation and drift check.
10. Component tests.
11. Playwright critical-path tests against the production build.
12. npm and Python dependency vulnerability scans.
13. Docker build, startup, and health smoke test.
14. Software bill of materials and signed image publication for release tags.

No release artifact should be produced when any mandatory step fails.

## 10. Client acceptance test plan

Use sanitized but structurally authentic data and have client representatives execute these scenarios in staging:

### Operations intake

- Upload the July workbook and verify 100 discovered customers and agreed active orders.
- Upload the September workbook and verify every reviewed mapping.
- Correct an intentionally unknown SKU and confirm the mapping persists.
- Add a WhatsApp/ad-hoc order and confirm it is not duplicated.

### Procurement

- Consolidate the selected orders.
- Change a buffer percentage and verify totals.
- Download the buyer sheet.
- Record full purchase and a shortage/replacement.
- Confirm procurement and dispatch Handoff 01.

### Packing

- Generate packing design for TETE and NGA orders.
- Verify product and customer segregation rules.
- Scan a real printed QR.
- Add, reduce, and swap an item with reasons.
- Capture weights manually and, if in scope, via BLE.
- Attempt overweight and incompatible transfers and verify rejection.
- Lock final packing and dispatch Handoff 02.

### Billing

- Review price readiness and legal data.
- Generate and approve an invoice.
- Download the immutable PDF.
- Record partial and final payment.
- Attempt duplicate reference, negative payment, and overpayment.
- Download the receipt and verify its hash/document data.
- Reconcile and close the shipment.

### Security and recovery

- Verify every role sees only permitted actions and customer data.
- Verify expired sessions and sign-out.
- Restore a staging backup and verify a completed shipment and documents.
- Demonstrate audit history for one master-data change, handoff, packing lock, invoice, and payment.

## 11. Final release gates

Publication is allowed only when all statements below are true:

- [ ] All P0 issues are closed with reviewed regression tests.
- [ ] Every P1 issue is closed or explicitly deferred in a client-signed scope document.
- [ ] Docker builds and starts using the supported production runtime.
- [ ] Staging uses rotated secrets, HTTPS, production-like CORS, and no localhost URLs.
- [ ] Both target workbooks complete the agreed workflow without database edits.
- [ ] The browser completes procurement confirmation and both handoffs.
- [ ] Operators can capture weights and lock every box.
- [ ] Persisted handoff hashes verify after a new database session.
- [ ] Every invoice line has an approved price and both legal entities are complete.
- [ ] Overpayment follows the approved accounting policy.
- [ ] Invoice and receipt PDFs are immutable and downloadable.
- [ ] Authorization and tenant-boundary tests pass for every role.
- [ ] Ruff, lint, TypeScript, backend tests, PostgreSQL migrations, Playwright tests, and vulnerability scans pass in CI.
- [ ] Backup restoration has been tested.
- [ ] Git is healthy and the exact release commit is tagged.
- [ ] Client acceptance results and deferred-scope decisions are signed off.

## 12. Recommended definition of done for each remediation ticket

Every implementation ticket created from this report should include:

1. The affected workflow stage and business invariant.
2. Backend/API/schema changes.
3. Frontend and role/permission changes.
4. Migration or data-backfill behavior.
5. Unit, API, database, and E2E regression tests.
6. Audit and observability behavior.
7. Failure and rollback behavior.
8. Updated operational or client documentation.
9. A demonstrable acceptance scenario.

A ticket is complete only when its acceptance scenario passes against the production build and PostgreSQL schema, not merely when its unit test passes.


for this alao thw loading product looks okay i want skeleton loading , and make skeleton lodaing animation for every table
