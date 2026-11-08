# Graph Report - Box-agriculture project  (2026-10-30)

## Corpus Check
- 474 files · ~475,610 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 40 file(s) not represented in the graph (top: .xml 13, .zip 13, (none) 5)

## Summary
- 3255 nodes · 11350 edges · 196 communities (145 shown, 51 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 1470 edges (avg confidence: 0.94)
- Token cost: unavailable — the agent tool does not expose actual token usage. Numeric zero fields in extraction/cost sidecars are placeholders, not measured usage; AST extraction has no LLM cost.

## Community Hubs (Navigation)
- Product Catalogue and Materials
- Floor Packing Lifecycle
- Order Intake and Customers
- Shared Frontend Components
- Order Validation API
- Box Catalogue and Capacity
- Order Submission Workspace
- Packing Revisions API
- Invoice Authorization and Reads
- Bounded Adaptive Intake
- Packing Planning Rules
- Procurement Confirmation and Concurrency
- Database Sessions and Dependencies
- Parser Resource Budgets
- Invoice Generation Models
- Transaction Outcome Tests
- Operational Dashboard Navigation
- Administrative Maintenance Scripts
- Pack Quantity Normalization
- Role and Token Security
- Durable Request Replay
- Product Table Reconciliation
- Workflow Audit and Handoffs
- Register Read Scaling
- Database Configuration
- Procurement Batch API
- Source Storage Resilience
- Purchasing Batch Aggregation
- Browser Workflow Tests
- Frontend API Error Handling
- Revision Conflict Responses
- Billing Workspace
- Procurement Workspace
- Schema Migration Test Infrastructure
- Password and Session Security
- Reusable Excel Profiles
- Historical Adversarial Test Evidence
- Bounded Register API
- User Administration API
- Private Storage Adapters
- QR Routes and Build Evidence
- Packing Profile Freshness
- Historical Browser Harness Failures
- Loading and Notification Components
- Source Recovery Lifecycle
- Workspace Guidance and Readiness
- Input Boundary Validation
- Permanent Selling Price Policy
- Packing Workspace Release Remediation
- Canonical Document Parsing
- System Resilience Regression Tests
- Customer Matrix Parsing
- Managed Database Reliability
- Order Models and Pipeline
- Order Exception Tests
- User Confirmation Dialogs
- Retained History and Pricing
- TypeScript Compiler Configuration
- Cross Stage Handoff API
- Product Table API
- Historical Quantity Review
- Invoice Only Workflow Boundary
- Packing Profile Decision Rules
- Recurring Order Template Contracts
- Operations Permissions and Status
- Order Exception Resolution
- Selling Price and SKU API
- Upload Staging and Idempotency
- Price Catalogue Validation
- Product Volume Research Sources
- Scanner and Frontend Dependencies
- Schema Migration Authority
- Authenticated Test Sessions
- Sealed Handoff Allocation
- Operator Workflow Guide
- Packing Setup Workspace
- Order Quantity Review Dialog
- Reliability Implementation Ledger
- Frontend Development and Smoke Scripts
- Order Exceptions API
- Adaptive Ingestion Orchestrator
- User Administration Tests
- Durable Retry Design
- Storage Recovery Design
- Page Refinement and Browser Coverage
- Packing Profile Rollout Scripts
- Historical Resilience Audit Findings
- Release Improvement Gates
- Operational Data Maintenance
- Production Recovery Readiness
- Frontend Runtime Dependencies
- Operational Register Indexing
- Platform Scope and Settlement History
- Procurement and Packing Design
- Product Table Activation Rules
- Original Packing Guide
- Recurring Excel Import Policy
- Development Work Log
- Hosted Migration Preservation
- Packing Measurement Research
- Project Architecture Overview
- Historical Payment Internals
- October Audit Fixes
- Invoice Scope Browser Discovery
- Long Term Operating Reliability
- Remaining Recovery Gates
- Client Journey Browser Evidence
- Compact Workspace Layout
- Database Setup Guidance
- Backend Container Readiness
- Local Deployment Stack
- System Architecture Guide
- Audit Coverage Ledger
- Physical Packing Rollout
- Frontend Package Scripts
- Order Intake Browser Coverage
- Historical Billing Desk Screenshot
- Mobile Packing Screenshot
- Production Frontend Build Evidence
- Product Code Migration
- Database Readiness Endpoint Tests
- Historical Selling Price Screenshot
- Frontend Development Dependencies
- Agriculture Company Brand
- Compact Layout Browser Tests
- Packing Constraint Migration
- Financial Gate Migration
- Handoff Index Migration
- Master Invoice Source Migration
- Packing Profile Migration
- Price Read Index Migration
- Historical Mobile Harness Failure
- Historical Dimension Harness Failure
- Register Navigation Harness Failure
- Generated Next Type References
- Release Smoke Coverage
- Stale Write Browser Coverage
- Invoicing Session Constructors
- Billing Price Test Fixtures
- Blank Historical Billing Image
- Blank Historical Login Image
- Blank Historical Quantity Image
- Historical Logout Backend Evidence
- Historical R0 R1 Evidence
- Historical R0 R2 Evidence
- Historical R0 R3 Evidence
- Historical R0 R4 Evidence
- Historical R5 Retry Evidence
- Historical R6 Storage Evidence
- Historical R7 Budget Evidence
- Historical R8 Read Evidence
- Historical Source Inventory
- System Guide Document Builder
- Repository Agent Guidance
- Category Access Browser Coverage
- Shared Loading Browser Coverage
- Table Loading Browser Coverage
- Business Module Namespace
- Price Catalogue Session Constructor
- Permanent Price Date Regression
- Historical Backend Lint Findings
- Historical Invoice Approval Timeout
- Historical Draft Selector Timeout
- Historical R8 Build Evidence
- Historical R9 Build Evidence
- Empty Browser Lint Evidence
- Blank Historical Dashboard Image
- Blank Historical Mobile Image
- Blank Historical EL Image
- Blank Historical Dimension Image
- Blank Historical Stale Plan Image
- Blank Historical Procurement Image
- Blank Historical Batch Image
- Blank Historical Register Image
- Historical Frontend Type Evidence
- Historical Browser Stderr Evidence
- Backend Package Metadata

## God Nodes (most connected - your core abstractions)
1. `User` - 300 edges
2. `OrderStatus` - 165 edges
3. `UserRole` - 158 edges
4. `ClientOrder` - 139 edges
5. `RawOrderFile` - 116 edges

## Surprising Connections (you probably didn't know these)
- `Historical expected failures: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F13` --references--> `test_price_lookup_uses_the_same_pack_units_as_intake()`  [EXTRACTED]
  docs/audits/2026-10-26/implementation-evidence/r0-r1-backend.txt → backend/tests/test_system_resilience_audit.py
- `Scoped cross-stage handoffs` --semantically_similar_to--> `Optimistic revision checks`  [INFERRED] [semantically similar]
  .impeccable/critique/2026-10-14T20-16-02Z__web-app.md → docs/MANAGEMENT_PROGRESS_AND_PRODUCTION_READINESS_REPORT_2026-10-29.md
- `Historical expected failures: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F13` --references--> `test_password_change_accepts_schema_valid_long_password()`  [EXTRACTED]
  docs/audits/2026-10-26/implementation-evidence/r0-r1-backend.txt → backend/tests/test_system_resilience_audit.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Database and object recovery gate** — docs_audits_2026_10_26_supabase_reliability_plan_database_recovery, docs_audits_2026_10_26_supabase_reliability_plan_object_recovery, docs_audits_2026_10_26_supabase_reliability_plan_private_storage [EXTRACTED 1.00]
- **Backend-authorized write architecture** — docs_audits_2026_10_26_supabase_reliability_plan_browser_portal, docs_audits_2026_10_26_supabase_reliability_plan_backend_api, docs_audits_2026_10_26_supabase_reliability_plan_sqlalchemy, docs_audits_2026_10_26_supabase_reliability_plan_database [EXTRACTED 1.00]
- **Durable replay transaction** — docs_audits_2026_10_26_r5_request_replay_idempotency_key_request_contract, docs_audits_2026_10_26_r5_request_replay_atomic_durable_request_receipts, docs_audits_2026_10_26_r5_request_replay_account_scoped_browser_pending_intent [EXTRACTED 1.00]
- **Coordinated source recovery set** — docs_audits_2026_10_26_r6_source_storage_private_immutable_supabase_objects, docs_audits_2026_10_26_r6_source_storage_independent_source_backup_manifests, docs_audits_2026_10_26_r6_source_storage_coordinated_database_and_object_recovery [EXTRACTED 1.00]
- **Internal exceptional order resolution** — docs_audits_2026_10_26_r9_internal_workflow_full_unavailable_order_closure, docs_audits_2026_10_26_r9_internal_workflow_early_staff_cancellation, docs_audits_2026_10_26_r9_internal_workflow_later_admin_manual_cancellation, docs_audits_2026_10_26_r9_internal_workflow_reason_revision_and_request_identity [EXTRACTED 1.00]
- **Internal shipment responsibility chain** — docs_management_progress_and_production_readiness_report_2026_10_29_versioned_recurring_excel_formats, docs_management_progress_and_production_readiness_report_2026_10_29_procurement_handoff_atomicity, docs_management_progress_and_production_readiness_report_2026_10_29_packing_finalization_atomicity, docs_management_progress_and_production_readiness_report_2026_10_29_internal_final_invoice_boundary [EXTRACTED 1.00]
- **Reviewed packing plan inputs** — docs_packing_profiles_reviewed_sku_packing_profiles, docs_packing_profiles_staff_selected_el_mode, docs_packing_profiles_workbook_only_external_box_sizes, docs_packing_profiles_snapshot_freshness [EXTRACTED 1.00]
- **Accepted product planning readiness** — docs_product_packing_table_confirmation, docs_product_packing_table_temperature_material, docs_product_packing_table_strict_planning, docs_product_packing_table_volume_formula [EXTRACTED 1.00]
- **Coordinated recovery identity** — docs_production_readiness_plan_coordinated_database_object_recovery, docs_production_readiness_plan_alembic_schema_authority, docs_production_readiness_plan_durable_key_retention, docs_production_readiness_plan_custom_jwt_session_version [EXTRACTED 1.00]
- **Normal customer packing approval flow** — graphify_out_converted_agricultural_order_management_and_packing_guide_8a5ebd9e_customer_separation, graphify_out_converted_agricultural_order_management_and_packing_guide_8a5ebd9e_planner, graphify_out_converted_agricultural_order_management_and_packing_guide_8a5ebd9e_scale_approval [EXTRACTED 1.00]

## Communities (196 total, 51 thin omitted)

### Community 0 - "Product Catalogue and Materials"
Cohesion: 0.07
Nodes (71): ast, BoxOut, create_main_customer(), create_product(), CustomerOut, list_boxes(), list_customers(), list_products() (+63 more)

### Community 1 - "Floor Packing Lifecycle"
Cohesion: 0.05
Nodes (75): packing_approval_queue(), Response, scan_box(), Box, new_box(), IATA air freight volumetric weight: L x W x H (cm) / 6000., Unsaved Box with its QR identity; caller adds it to the session., volumetric_weight_kg() (+67 more)

### Community 2 - "Order Intake and Customers"
Cohesion: 0.07
Nodes (56): Customer, RawOrderFile, acknowledgment_csv(), IntakeResult, OrderIntake, pack_weight_kg(), Any, Session (+48 more)

### Community 3 - "Shared Frontend Components"
Cohesion: 0.06
Nodes (53): jsqr, lucide-react, next, react, LoginPage(), submit(), CombineFiles(), ScanStickerPage() (+45 more)

### Community 4 - "Order Validation API"
Cohesion: 0.07
Nodes (69): response(), acknowledgment_response(), add_adhoc_order(), apply(), AdHocItemInput, AdHocOrderRequest, BulkQuantityCorrection, choose_unknown_quantity_unit() (+61 more)

### Community 5 - "Box Catalogue and Capacity"
Cohesion: 0.08
Nodes (60): apply_reference_drafts(), audit(), box_out(), BoxInput, create_box(), create_product(), CreateProductInput, LimitInput (+52 more)

### Community 6 - "Order Submission Workspace"
Cohesion: 0.06
Nodes (47): r5-request-replay-frontend-checks historical audit evidence, r5-request-replay-frontend-checks frontend unit results, Persisted submission intent recovery, r6-source-storage-frontend-checks historical audit evidence, r6-source-storage-frontend-checks frontend unit results, Persisted submission intent recovery, Upload fingerprint and original upload recovery, r7-work-budgets-frontend-checks historical audit evidence (+39 more)

### Community 7 - "Packing Revisions API"
Cohesion: 0.08
Nodes (61): add_extra_box_to_order(), AddBoxRequest, BoxDetailOut, BoxItemOut, BoxOverrideRequest, change_box_packing_mode(), confirm_final_pack(), export_vehicle_loading() (+53 more)

### Community 8 - "Invoice Authorization and Reads"
Cohesion: 0.08
Nodes (56): approve_and_dispatch_invoice(), approve_bulk_invoices(), billing_work_queue(), BulkApprovalRequest, BulkInvoicesRequest, check_billing_prices(), close_paid_order(), generate_bulk_invoices() (+48 more)

### Community 9 - "Bounded Adaptive Intake"
Cohesion: 0.07
Nodes (38): canonical_day(), Canonical business calendar dates; never guess a year or date convention., Reject expansion/dimension traps before spreadsheet libraries allocate cells., Shared safety budgets, independent of application settings and database imports., prepare_acknowledgment(), Order acknowledgment reports available within the platform., Order intake: every parser's CanonicalOrderDraft becomes ClientOrders through…, ActionType (+30 more)

### Community 10 - "Packing Planning Rules"
Cohesion: 0.09
Nodes (50): lookup_pack_key(), AdjustmentType, BoxItem, Base, str, TransportationManifest, order_route_code(), The optimizer's constraints applied to persisted floor packing contents. (+42 more)

### Community 11 - "Procurement Confirmation and Concurrency"
Cohesion: 0.10
Nodes (47): AgentHandoff, ProcurementConfirmation, ProcurementConfirmationService, Session, Implements the mandatory market-buyer approval gate., postgres_sessions(), fixture, parametrize (+39 more)

### Community 12 - "Database Sessions and Dependencies"
Cohesion: 0.09
Nodes (28): Confirmation, BaseModel, Review format mappings, save a new immutable version, and explicitly re-read…, get_db(), Default factory for String(36) primary key columns., One transaction; every HTTP caller must use Depends(..., scope="function").…, uuid_pk(), get_current_user() (+20 more)

### Community 13 - "Parser Resource Budgets"
Cohesion: 0.07
Nodes (36): asyncio, BodyLimits, bounded_receive(), Bound actual ASGI bytes (including chunked requests), not just Content-Length., inspect_document(), Path, parse_document(), Killable parser boundary with per-process admission and memory/wall limits. (+28 more)

### Community 14 - "Invoice Generation Models"
Cohesion: 0.12
Nodes (36): Invoice, Base, User, validate_verified_net(), _invoice_number(), _money(), OnBehalfInvoicingService, Any (+28 more)

### Community 15 - "Transaction Outcome Tests"
Cohesion: 0.08
Nodes (36): disconnected(), fail_send(), failing_session(), _customer(), owner_headers(), fixture, parametrize, Cover direct dependencies and nested authentication, without replacing get_db. (+28 more)

### Community 16 - "Operational Dashboard Navigation"
Cohesion: 0.10
Nodes (26): R9 frontend lint scope, FileRegister(), PackingRegister(), destination(), OperationsPage(), load(), Queue, queues (+18 more)

### Community 17 - "Administrative Maintenance Scripts"
Cohesion: 0.09
Nodes (24): argparse, Build reference mappings from supplied samples, without changing the workbooks., Read the supplied workbook into review-only reference data; never write to the…, Extract source values without interpreting document text as instructions., main(), Inspect an explicitly configured Supabase review target without writing data., Read-only inventory for a schema-preserving database reset., main() (+16 more)

### Community 18 - "Pack Quantity Normalization"
Cohesion: 0.11
Nodes (29): _bounded_key(), pack_key(), Physical pack identity shared by intake, packing and selling prices., decimal_value(), derive_quantity(), label_weight(), normalize_pack(), OrderQuantity (+21 more)

### Community 19 - "Role and Token Security"
Cohesion: 0.12
Nodes (31): decode_access_token(), hash_password(), StrEnum, UserRole, main(), client_for(), test_accounting_level_includes_general_procurement_access(), test_general_user_can_map_skus_but_cannot_edit_buffers() (+23 more)

### Community 20 - "Durable Request Replay"
Cohesion: 0.10
Nodes (31): Session, run_once(), PaymentTransaction, Base, Sub-ledger for partial payments, bank clearances, and rolling balances, Receipt, test_final_invoice_finishes_application_work_without_payment(), test_post_invoice_money_and_receipt_routes_are_not_exposed() (+23 more)

### Community 21 - "Product Table Reconciliation"
Cohesion: 0.13
Nodes (33): apply_reconciliation(), parse_limits(), Only explicit kg notes and uniquely named approved boxes are executable., reconciliation(), sources(), main(), preview(), Preview source reconciliation; back up, migrate and import drafts without… (+25 more)

### Community 22 - "Workflow Audit and Handoffs"
Cohesion: 0.13
Nodes (17): Module-level guard; the app maps PermissionError to 403., require(), AuditLog, Base, canonical_sha256(), Hash of sorted, compact JSON; the signature for handoffs and receipts., Session, ValueError (+9 more)

### Community 23 - "Register Read Scaling"
Cohesion: 0.12
Nodes (28): ClientOrder, PurchasingOrderSource, Base, Junction table recording source files and client orders combined into a Master…, capture(), line(), parametrize, Retained history, legacy contracts and bounded query fan-out regressions. (+20 more)

### Community 24 - "Database Configuration"
Cohesion: 0.09
Nodes (27): run_migrations_online(), model_validator, Settings, build_engine_options(), Any, Choose safe connection settings for Supabase pooler modes., migration_connect_args(), normalize_database_url() (+19 more)

### Community 25 - "Procurement Batch API"
Cohesion: 0.12
Nodes (32): adjust_batch_buffer(), adjust_purchasing_buffer(), _batch_response(), BatchBufferRequest, BufferUpdateRequest, combine_orders_for_procurement(), CombineOrdersRequest, confirm_procurement_batch() (+24 more)

### Community 26 - "Source Storage Resilience"
Cohesion: 0.12
Nodes (28): object_key(), Base, Durable, backend-only write receipts. Never expire keys into reusable intents., RequestResult, Cloud, parametrize, test_changed_upload_bytes_or_context_cannot_reuse_key(), test_content_tampering_and_missing_integrity_metadata_are_detected() (+20 more)

### Community 27 - "Purchasing Batch Aggregation"
Cohesion: 0.13
Nodes (17): MasterPurchasingList, PurchasingItem, merge_products(), Merge future SKU demand while preserving confirmed packing and invoice history., PurchasingBatchService, Session, Purchasing batch: consolidate order demand, apply buffers, until the buyer…, Apply one buffer % to every line of the batch in a single transaction. (+9 more)

### Community 28 - "Browser Workflow Tests"
Cohesion: 0.06
Nodes (5): @playwright/test, select(), DraftProfile, empty, permissions

### Community 29 - "Frontend API Error Handling"
Cohesion: 0.09
Nodes (27): ChangePasswordPage(), submit(), createAndMapNewProduct(), quickMapSku(), displayCustomer(), PackingContent(), applyReplan(), changeBoxSize() (+19 more)

### Community 30 - "Revision Conflict Responses"
Cohesion: 0.11
Nodes (30): Exception, require_revision(), RevisionConflict, catalogue_review_required(), concurrent_write(), database_busy(), database_wait(), health_check() (+22 more)

### Community 31 - "Billing Workspace"
Cohesion: 0.12
Nodes (29): BillingContent(), approveDraft(), approveSelectedInvoices(), changeTab(), createBulkDrafts(), createIndividualDraft(), createMasterDrafts(), openDocument() (+21 more)

### Community 32 - "Procurement Workspace"
Cohesion: 0.09
Nodes (23): downloadSource(), formatKg(), getCategoryDisplay(), getProduceEmoji(), MasterProduct, MasterPurchasingBatch, ProcurementContent(), applyBulkBuffer() (+15 more)

### Community 33 - "Schema Migration Test Infrastructure"
Cohesion: 0.13
Nodes (20): alembic_migration, alembic_operations, Connection URL handling without loading application settings or an engine., parametrize, test_revision_migration_preserves_rows_and_never_resets_used_tokens(), parametrize, test_template_and_cutoff_migration_preserves_rows_and_repeats_safely(), parametrize (+12 more)

### Community 34 - "Password and Session Security"
Cohesion: 0.09
Nodes (26): argon2, argon2_exceptions, change_password(), ChangePasswordRequest, login(), LoginRequest, logout(), BaseModel (+18 more)

### Community 35 - "Reusable Excel Profiles"
Cohesion: 0.14
Nodes (29): confirm(), pending(), get, post, Session, review(), validate_definition(), ExcelTemplate (+21 more)

### Community 36 - "Historical Adversarial Test Evidence"
Cohesion: 0.15
Nodes (32): Historical adversarial tests ordinary mode, Historical ordinary run: 4 passed, 25 xfailed, Historical adversarial tests unmasked, Historical unmasked run: 25 failed, 4 passed, Historical complete backend suite, Historical complete run: 274 passed, 3 skipped, 25 xfailed, Commit before reporting successful writes, Final successful browser runs: 126 passed and 1 skipped (+24 more)

### Community 37 - "Bounded Register API"
Cohesion: 0.17
Nodes (24): billing_order_record(), billing_orders(), customers(), file_detail(), file_orders(), file_summary(), files(), invoice_memberships() (+16 more)

### Community 38 - "User Administration API"
Cohesion: 0.14
Nodes (25): access_levels(), audit_user_change(), create_user(), delete_user(), get_user_or_404(), list_users(), PasswordResetResponse, BaseModel (+17 more)

### Community 39 - "Private Storage Adapters"
Cohesion: 0.15
Nodes (9): LocalStorage, Path, SupabaseStorage, chunks(), validate_key(), Exception, Safe, non-provider-specific error without credential/response reflection., StorageError (+1 more)

### Community 40 - "QR Routes and Build Evidence"
Cohesion: 0.09
Nodes (28): logout-price-policy-frontend-build historical audit evidence, Next.js 16.3.4 webpack production build, Static routes and dynamic QR scan route, r0-r1-frontend-build historical audit evidence, Next.js 16.3.4 webpack production build, Static routes and dynamic QR scan route, r0-r3-frontend-build historical audit evidence, Next.js 16.3.4 webpack production build (+20 more)

### Community 41 - "Packing Profile Freshness"
Cohesion: 0.15
Nodes (28): ProductPackProfile, packing_details(), stale_reasons(), payload(), prepare(), fixture, parametrize, reviewed() (+20 more)

### Community 42 - "Historical Browser Harness Failures"
Cohesion: 0.07
Nodes (28): Isolated browser server connection refused on port 3106, audit-fixes.spec.ts >> billing preserves historical invoice currency in every displayed amount — historical browser context, billing preserves historical invoice currency in every displayed amount, Isolated browser server connection refused on port 3106, audit-fixes.spec.ts >> dashboard reports separate balances for different currencies — historical browser context, dashboard reports separate balances for different currencies, Isolated browser server connection refused on port 3106, audit-fixes.spec.ts >> password change returns the operator to sign in — historical browser context (+20 more)

### Community 43 - "Loading and Notification Components"
Cohesion: 0.11
Nodes (22): web_app_compact, web_app_globals, body, display, metadata, mono, LoadingDialog(), LoadingProvider() (+14 more)

### Community 44 - "Source Recovery Lifecycle"
Cohesion: 0.18
Nodes (24): Content, get_storage(), lock_object(), persist_source(), Held through the caller's COMMIT/ROLLBACK, including uncertain commits., source_path(), Path, Offline backup verification: no app settings, credentials or database imports. (+16 more)

### Community 45 - "Workspace Guidance and Readiness"
Cohesion: 0.09
Nodes (25): Bounded processing and reads, Agriculture Is Life: Development, Improvements and Production Readiness Report, Durable retry outcomes, Guided staff workspaces, Hosted upgrade with preservation, Implemented verified released distinction, Optimistic revision checks, Packing finalization atomicity (+17 more)

### Community 46 - "Input Boundary Validation"
Cohesion: 0.13
Nodes (22): DraftInvoiceUpdateRequest, model_validator, PackInput, model_validator, ProcurementItemConfirmationRequest, payment(), parametrize, test_duplicate_invoice_lines_are_rejected_before_dictionary_conversion() (+14 more)

### Community 47 - "Permanent Selling Price Policy"
Cohesion: 0.19
Nodes (24): CustomerSellingPrice, PriceList, PriceCatalogue, test_resolution_saves_shipment_price_and_invoice_uses_it(), test_equivalent_new_price_preserves_old_effective_period(), test_equivalent_pack_weights_use_existing_price(), test_legacy_compact_price_dates_resolve_without_rewriting_history(), test_packaging_prices_remain_distinct_and_alias_conflicts_require_review() (+16 more)

### Community 48 - "Packing Workspace Release Remediation"
Cohesion: 0.09
Nodes (21): Canonical commercial data approval, Conditional release decision, Current production baseline, FreshFlow Client Release Remediation Report, Historical payment and receipt workflow, HttpOnly cookie authentication, Production deployment gate, Recovery and acceptance gate (+13 more)

### Community 49 - "Canonical Document Parsing"
Cohesion: 0.13
Nodes (15): validate_drafts(), matrix_to_drafts(), Adapter: MatrixOrderParser.parse_all_clients() output -> drafts (quantities are…, CanonicalOrderDraft, BaseModel, main(), One parser invocation. No credentials or database access are needed here., PdfOrderParser (+7 more)

### Community 50 - "System Resilience Regression Tests"
Cohesion: 0.20
Nodes (20): parametrize, Local audit evidence. Xfails document open defects, not passing safeguards. Run…, test_general_user_cannot_edit_financial_data(), test_invoice_price_rejects_duplicate_lines(), test_invoice_price_rejects_nonfinite_number(), test_logout_revokes_presented_session(), test_password_change_accepts_schema_valid_long_password(), test_payment_over_balance_is_rejected() (+12 more)

### Community 51 - "Customer Matrix Parsing"
Cohesion: 0.16
Nodes (12): MatrixOrderParser, Any, Robust Streaming Matrix & Tabular Order Parser with Dynamic Floating Anchor…, Dynamically finds header row and packing delimiter column by scanning for…, Resolves the route group name based on column group header, client code prefix…, Extracts declared client code from 'Order Info' sheet if present., Infers client code from sheet top metadata, sheet name, or file basename., Fast lightweight inspection to immediately discover sub-customers on file… (+4 more)

### Community 52 - "Managed Database Reliability"
Cohesion: 0.15
Nodes (21): Runtime database role, grants and RLS boundary, Alembic single migration authority, FastAPI trusted write path, Next.js operator portal, Per-process SQLAlchemy connection budget, FastAPI custom JWT authentication, Supabase Database managed PostgreSQL, Database backup and PITR policy (+13 more)

### Community 53 - "Order Models and Pipeline"
Cohesion: 0.17
Nodes (13): app_services_order_splitter, app_services_packing_override_service, app_services_purchasing_aggregator, procurement_source_orders(), Response, ClientOrderItem, Base, Actual approved goods; product_id continues to describe the original order. (+5 more)

### Community 54 - "Order Exception Tests"
Cohesion: 0.21
Nodes (17): OrderStatus, str, parametrize, Internal-only exception outcomes and the invoice-only application boundary., shortage(), submit(), test_cancelling_one_order_recalculates_shared_purchase_and_remaining_order_dispatches(), test_cancelling_only_order_closes_empty_batch_and_keeps_original_demand() (+9 more)

### Community 55 - "User Confirmation Dialogs"
Cohesion: 0.21
Nodes (18): permissionSummary(), UsersPage(), changeAccountStatus(), changeRole(), createUser(), deleteUser(), loadUsers(), resetPassword() (+10 more)

### Community 56 - "Retained History and Pricing"
Cohesion: 0.11
Nodes (19): Legacy price review before the permanent-price rollout, Issued invoice immutability, Legacy ended-price candidates, Permanent-price business policy, Physical pack alias review, Restricted finance review log, Adjacent-date scoped price reads, Batched invoice and source relationships (+11 more)

### Community 57 - "TypeScript Compiler Configuration"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 58 - "Cross Stage Handoff API"
Cohesion: 0.16
Nodes (17): dispatch_handoff_01(), Handoff01Request, Handoff02Request, handoff_response(), BaseModel, post, Session, return_handoff_02() (+9 more)

### Community 59 - "Product Table API"
Cohesion: 0.22
Nodes (17): Bulk, create(), Edit, listing(), NewProduct, BaseModel, get, post (+9 more)

### Community 60 - "Historical Quantity Review"
Cohesion: 0.15
Nodes (12): Read-only candidates for date, physical-pack and customer identity review., review_records(), bushel(), density(), main(), missing_reason(), quantize(), Build an explicit SKU-to-web-evidence proposal; never infer identity from row… (+4 more)

### Community 61 - "Invoice Only Workflow Boundary"
Cohesion: 0.12
Nodes (18): Alembic schema authority, Custom backend JWT and roles, Database and Storage recovery, Production baseline and document index, Internal staff admin finance scope, Invoice-only completion boundary, Private immutable uploaded sources, Production requirement baseline (+10 more)

### Community 62 - "Packing Profile Decision Rules"
Cohesion: 0.14
Nodes (18): Deterministic box heuristic, Box Agriculture: packing decisions and rule changes, Floor constraint validation, Forward packing lifecycle, Historical live-rule coupling, Historical NGA exception, Proposed rule-change handling, Template fast-track shortcut (+10 more)

### Community 63 - "Recurring Order Template Contracts"
Cohesion: 0.14
Nodes (18): Reviewed immutable revision flow, Group Order Matrix Template 8cda9119, Group matrix template contract, Reviewed immutable revision flow, Group Order Matrix Template d53ae8e8, Group matrix template contract, Single Customer Order Template 351a96df, Single order quantity and pack weight contract (+10 more)

### Community 64 - "Operations Permissions and Status"
Cohesion: 0.17
Nodes (14): current_session(), get, operations_summary(), get, Session, allows(), can(), permissions() (+6 more)

### Community 65 - "Order Exception Resolution"
Cohesion: 0.21
Nodes (15): apply(), buffer_percentage(), fixed_decimal(), money(), positive_money(), purchase_weight(), Decimal, Accepted SQL-backed money and text values shared by requests and services. (+7 more)

### Community 66 - "Selling Price and SKU API"
Cohesion: 0.16
Nodes (15): create_or_update_sku_mapping(), list_sku_mappings(), BaseModel, field_validator, get, post, Session, On-Behalf Invoicing Database: Configures the main customer's designated selling… (+7 more)

### Community 67 - "Upload Staging and Idempotency"
Cohesion: 0.15
Nodes (14): _copy(), Private immutable intake objects; local disk is a development adapter only. An…, stage_upload(), Reserve and complete a write receipt in the caller's business transaction. The…, ValueError, UploadTooLarge, test_original_basename_is_preserved_for_parser_customer_detection(), BinaryIO (+6 more)

### Community 68 - "Price Catalogue Validation"
Cohesion: 0.24
Nodes (11): _day_before(), _money(), _pack_size_candidates(), _price_pack_key(), PriceCatalogueReviewRequired, Decimal, ValueError, Price catalogue: dated selling prices, written and read in one place. Lookup… (+3 more)

### Community 69 - "Product Volume Research Sources"
Cohesion: 0.12
Nodes (17): Banana bunch/hand/finger measurements, Tables 5–7, Basil and coriander leaf measurements, Table 1, Product volume research — 28 October 2026, FAO/INFOODS definitions and commodity density entries, Fresh straw mushroom, Table 1, Fresh whole ginger measurements, Garlic bulbs, variety-specific bulk density, ICAR litchi carton dimensions and capacities (+9 more)

### Community 70 - "Scanner and Frontend Dependencies"
Cohesion: 0.12
Nodes (13): eslint, eslint-config-next, qrcode, react-dom, @types/node, @types/qrcode, @types/react, @types/react-dom (+5 more)

### Community 72 - "Authenticated Test Sessions"
Cohesion: 0.17
Nodes (11): create_access_token(), Any, authenticated_client(), test_download_group_matrix_template(), test_download_nonexistent_template_404(), test_download_single_customer_template(), test_list_order_templates(), api_client() (+3 more)

### Community 73 - "Sealed Handoff Allocation"
Cohesion: 0.17
Nodes (7): HandoffService, Session, Signs the locked packing and advances the order; flushes, caller commits., Latest Handoff 02 for the order, after checking its signature., Creates immutable, hashed contracts between the two workflow agents., Distribute aggregate market shortages proportionally and deterministically., Internal transaction step; caller authorizes and locks the batch.

### Community 74 - "Operator Workflow Guide"
Cohesion: 0.12
Nodes (16): Fresh Produce Platform user guide, Final invoice creation, Final packing handoff, Internal role permissions, Order exceptions, Order-to-final-invoice workflow, Procurement confirmation, QR sticker box lookup (+8 more)

### Community 75 - "Packing Setup Workspace"
Cohesion: 0.16
Nodes (14): categories, Changes, columns, Draft, empty(), label(), Limit, PackingSetup() (+6 more)

### Community 76 - "Order Quantity Review Dialog"
Cohesion: 0.19
Nodes (12): OrderQuantityReview(), Props, QuantityDialog(), accept(), bulk(), chooseUnit(), save(), resultText() (+4 more)

### Community 77 - "Reliability Implementation Ledger"
Cohesion: 0.13
Nodes (15): Quantity meaning across stages, All-account logout, argon2-cffi API, Canonical dates and physical packs, Reliability implementation progress, Durable request receipts, Finite bounded values and password compatibility, Invoice-only exception outcomes (+7 more)

### Community 78 - "Frontend Development and Smoke Scripts"
Cohesion: 0.15
Nodes (9): ref_node_assert, ref_node_child_process, ref_node_crypto, ref_node_fs, ref_node_path, ref_node_url, child, projectDir (+1 more)

### Community 79 - "Order Exceptions API"
Cohesion: 0.23
Nodes (12): base_query(), exception_detail(), exceptions(), BaseModel, field_validator, get, post, Session (+4 more)

### Community 80 - "Adaptive Ingestion Orchestrator"
Cohesion: 0.15
Nodes (11): AdaptiveOrderOrchestrator, Any, Session, Universal intake: picks the parser adapter by file type, then hands drafts to…, Universal entrypoint for any order file format., Adaptive Multi-Modal Intake & Reconciliation Workflow Test Verifies end-to-end…, run_adaptive_intake_test(), parametrize (+3 more)

### Community 81 - "User Administration Tests"
Cohesion: 0.37
Nodes (12): client_for(), login(), make_user(), parametrize, test_category_downgrade_revokes_existing_session_permissions(), test_created_accounts_receive_category_permissions(), test_human_access_levels_are_hierarchical(), test_owner_can_change_role_reset_password_and_deactivate_user() (+4 more)

### Community 82 - "Durable Retry Design"
Cohesion: 0.14
Nodes (14): Commit-before-success transactions, Account-scoped browser pending intent, Atomic durable request receipts, R5: durable retries for order additions and payments, Historical payment replay protocol, Idempotency-Key request contract, Migration 0020 request results, Raw-file parent serialization (+6 more)

### Community 83 - "Storage Recovery Design"
Cohesion: 0.14
Nodes (14): database backup guidance, Database backups exclude Storage bytes, R6: private Supabase Storage and source recovery, Explicit source migration and restore target, Independent source backup manifests, official Storage REST contract, private bucket behavior, Private bucket readiness (+6 more)

### Community 84 - "Page Refinement and Browser Coverage"
Cohesion: 0.14
Nodes (8): Complete explicit-order procurement sources, Agriculture operations: per-page UI refinement plan, One dominant task action, Phone workspace menu, Physical-device validation, Record and queue context, Responsive packing box editor, Role-aware workflow navigation

### Community 85 - "Packing Profile Rollout Scripts"
Cohesion: 0.31
Nodes (11): alembic_config, main(), Back up and migrate to workbook external dimensions without changing locked…, verify_catalogue(), json_value(), locked_history(), main(), Back up app data, migrate atomically, and stage unreviewed reference profiles.… (+3 more)

### Community 86 - "Historical Resilience Audit Findings"
Cohesion: 0.15
Nodes (13): Audit evidence limits, System resilience and user-behavior audit — 26 October 2026, F09 stale client overwrite, F11 procurement quantity contradictions, F12 zero-fulfilment fallback, F13 missing request identity, F14 false-success transaction response, F15 nondurable local uploads (+5 more)

### Community 87 - "Release Improvement Gates"
Cohesion: 0.17
Nodes (12): Improvement plan and change ledger, Isolated release-validation gates, R0 honest transaction responses, R10 reproducible monitored release, R1 procurement quantity contract, R4 optimistic stale-write checks, R5 request identity, R6 durable source lifecycle (+4 more)

### Community 88 - "Operational Data Maintenance"
Cohesion: 0.25
Nodes (8): encode(), main(), Apply the name-checked volume proposal with backup, revision checks and read-…, validate_proposal(), write_json(), write_review_csv(), Reset Operational / Transactional Data Cleans all orders, purchasing lists,…, dotenv

### Community 89 - "Production Recovery Readiness"
Cohesion: 0.20
Nodes (11): Alembic schema authority, Business policy approval, Controlled migration runner, Coordinated database-object recovery, Custom JWT session version, Production readiness and operating plan, Durable-key retention, Historical readiness snapshot (+3 more)

### Community 90 - "Frontend Runtime Dependencies"
Cohesion: 0.18
Nodes (11): dependencies, jsqr, lucide-react, next, qrcode, react, react-dom, @types/node (+3 more)

### Community 91 - "Operational Register Indexing"
Cohesion: 0.20
Nodes (7): Bulk selection semantics, operational registers, Operational register indexing migration, Register validation coverage, Paginated summary endpoints, Register context in URL, Hidden box weight draft validation

### Community 92 - "Platform Scope and Settlement History"
Cohesion: 0.20
Nodes (10): Internal final-invoice boundary, Fresh Produce Platform overview, Finalized-packing invoicing, Handoff 01, Handoff 02, Historical settlement scope, Human-reviewed Excel and PDF intake, Internal team shipment workflow (+2 more)

### Community 93 - "Procurement and Packing Design"
Cohesion: 0.24
Nodes (10): Derived box weight, Atomic all-box weight save, Combined procurement confirmation, Completed-retry consistency, Procurement and packing implementation, Historical measured-floor workflow, Permissioned accounting continuation, Preparation and resumption (+2 more)

### Community 94 - "Product Table Activation Rules"
Cohesion: 0.22
Nodes (10): Explicit product settings confirmation, product packing table, Product field provenance and revisions, Product table safe rollout, Workbook classification precedence, Strict planning prerequisites, Temperature determines box material, 29 October product temperature review (+2 more)

### Community 95 - "Original Packing Guide"
Cohesion: 0.22
Nodes (10): Count and identifier acknowledgment, EL and packing compatibility, Shared procurement separate customer packing, Agricultural order management and packing guide 8a5ebd9e, Separate admin packing fast track, 9 October audit follow-ups, Box sequence and QR identity, Scale weighing and manager lock (+2 more)

### Community 96 - "Recurring Excel Import Policy"
Cohesion: 0.22
Nodes (9): Hosted migration 0024 update, Product-table reimport protection, Alembic revision 20261027_0024, Bounded serialized format snapshot, Customer Excel format versions, Recurring customer Excel orders, First-use quantity-unit confirmation, Garlic and onion foam cutoff (+1 more)

### Community 97 - "Development Work Log"
Cohesion: 0.22
Nodes (9): Data preservation backup, Agriculture Is Life: Daily Updates, 26-29 October 2026, Hosted database upgrade, Internal invoice-only scope, Modern Today dashboard, R0-R9 reliability programme, Release sign-off gates, Temperature classification gaps (+1 more)

### Community 98 - "Hosted Migration Preservation"
Cohesion: 0.25
Nodes (9): Application-data backup, Backend-only new tables, Hosted database upgrade — 28 October 2026, Hosted Alembic head 0024, Pending coordinated recovery, Remaining application deployment, Unreviewed garlic and onion cutoffs, Writer-locked preservation verification (+1 more)

### Community 99 - "Packing Measurement Research"
Cohesion: 0.33
Nodes (9): Bulk-density comparators, Packing weight and volume research, Evidence before profile approval, Exact kilogram-to-pack conversion, FAO density definitions, Finished-pack occupied volume, Finished-pack sample method, Fractional pack review (+1 more)

### Community 100 - "Project Architecture Overview"
Cohesion: 0.22
Nodes (9): Deterministic workflow identities, Fresh Produce Platform, Hosted migration 0024, Modular monolith, Private Supabase Storage, Recurring customer Excel formats, Session pooler, Supabase Database (+1 more)

### Community 101 - "Historical Payment Internals"
Cohesion: 0.36
Nodes (7): field_validator, Financial Processing: Staff manually approves payments to trigger receipt…, record_payment_and_generate_receipt(), apply(), RecordPaymentRequest, test_postgres_same_payment_key_creates_one_ledger_entry(), pay()

### Community 102 - "October Audit Fixes"
Cohesion: 0.25
Nodes (8): Disposable PostgreSQL concurrency tests, Docker public asset inclusion, Audit fixes — 9 October 2026, Packing constraint consistency, Persisted invoice currency, Physical item-weight reconciliation, Self-transfer prevention, Session-version invalidation

### Community 103 - "Invoice Scope Browser Discovery"
Cohesion: 0.25
Nodes (4): Request replay browser scenario listing, r9 internal workflow frontend checks, Invoice-only workflow test discovery, R9 historical frontend unit checks

### Community 104 - "Long Term Operating Reliability"
Cohesion: 0.25
Nodes (8): How users and years of live operation could break this system, Infrastructure failure scenarios, Long-lived operational reliability, Ongoing operating reliability, Ordinary operator failure scenarios, Production evidence requirements, Release decision evidence, Retained-history growth

### Community 105 - "Remaining Recovery Gates"
Cohesion: 0.25
Nodes (8): Coordinated recovery rehearsal gate, Remaining reliability work — 27 October 2026, Guarded database concurrency gate, Historical blocker precedence, Isolated Supabase staging gate, Private hosted source bucket gate, R10 release and monitoring operations, Thirteen legacy selling-price reviews

### Community 106 - "Client Journey Browser Evidence"
Cohesion: 0.29
Nodes (8): Disposable upload-to-invoice journey, Client journey browser audit — 29 October 2026, Exact locked-box scan navigation, Format-review-first guidance, Local verification boundary, Parser worker cleanup risk, Phone worksheet visibility, Tab-persistent hint pause

### Community 107 - "Compact Workspace Layout"
Cohesion: 0.25
Nodes (8): Compact desktop workspaces, Compact workspace layout — 29 October 2026, Grouped role-aware navigation, Product catalogue worksheet, Responsive touch controls, Sticky register controls, Synthetic browser layout evidence, Today queue dashboard

### Community 108 - "Database Setup Guidance"
Cohesion: 0.25
Nodes (8): Controlled release migrations, Supabase Database Setup Guide, Exposed-schema policy review, Managed PostgreSQL access, Schema verification, Server-side database credentials, Session connection pooling, Transaction connection pooling

### Community 109 - "Backend Container Readiness"
Cohesion: 0.29
Nodes (6): Database-backed readiness probe, Disabled storage garbage collection, Preparation mode, Private source bucket configuration, Production backend container, Startup Alembic migration

### Community 110 - "Local Deployment Stack"
Cohesion: 0.29
Nodes (6): Full local Compose stack, Preparation mode, Private source storage, Readiness-gated web startup, Same-origin backend routing, Startup Alembic migration

### Community 111 - "System Architecture Guide"
Cohesion: 0.29
Nodes (7): Backend authorization boundary, Canonical order drafts, Customer and source membership, How this codebase works, FastAPI modular monolith, Next.js operator portal, SQLAlchemy business write path

### Community 112 - "Audit Coverage Ledger"
Cohesion: 0.29
Nodes (7): Availability planning targets, Coordinated disaster recovery, Coverage ledger: system design and user behavior, Evidence coverage ledger, Hash integrity versus authenticity, Historical immutability, PostgreSQL concurrency proof

### Community 113 - "Physical Packing Rollout"
Cohesion: 0.33
Nodes (7): Packing profiles rollout, Idempotent draft staging, Physical packing activation check, Strict planning feature flag, Unreviewed reference staging, Volume measurement provenance, Workbook external dimension migration

### Community 114 - "Frontend Package Scripts"
Cohesion: 0.29
Nodes (7): scripts, build, dev, lint, start, test:container, test:unit

### Community 116 - "Historical Billing Desk Screenshot"
Cohesion: 0.53
Nodes (6): Billing desk screenshot, Billing status registers, Orders, purchasing and packing completed, Invoice draft editable until approval, Invoice draft from final packed quantities, Selling prices saved

### Community 117 - "Mobile Packing Screenshot"
Cohesion: 0.60
Nodes (6): Small Foam Box Packing Card, Packing Review and Finalize Bar, Mobile Packing Desk Screenshot, Holy Basil Pack Editing, BASIL-SHOP Packing Order, EL Rules and Small Foam Box Selectors

### Community 118 - "Production Frontend Build Evidence"
Cohesion: 0.33
Nodes (6): Isolated production frontend build evidence, Dynamic QR-code scan route, Proxy middleware, Seventeen static pages generated, TypeScript build checks passed, Next.js 16.3.4 webpack production build

### Community 119 - "Product Code Migration"
Cohesion: 0.80
Nodes (4): _columns(), downgrade(), _indexes(), upgrade()

### Community 120 - "Database Readiness Endpoint Tests"
Cohesion: 0.40
Nodes (3): test_readiness_endpoint_checks_the_database(), override_db(), test_readiness_endpoint_fails_closed_when_database_is_unreachable()

### Community 121 - "Historical Selling Price Screenshot"
Cohesion: 0.60
Nodes (5): Complete selling prices billing screen, Customer and effective shipment date price scope, Positive selling price per pack required for invoicing, Persistent operations navigation with active Billing, T001 Herb selling price per 1000g pack in THB

### Community 122 - "Frontend Development Dependencies"
Cohesion: 0.40
Nodes (5): devDependencies, eslint, eslint-config-next, @playwright/test, @types/qrcode

### Community 123 - "Agriculture Company Brand"
Cohesion: 0.40
Nodes (5): AGRICULTURE IS LIFE CO.,LTD., Agriculture Is Life brand logo, Gray double wave lines, Dark green A letterform, Lime green leaf motif

### Community 124 - "Compact Layout Browser Tests"
Cohesion: 0.40
Nodes (3): permissions, product, registers

### Community 126 - "Financial Gate Migration"
Cohesion: 0.83
Nodes (3): _columns(), downgrade(), upgrade()

### Community 127 - "Handoff Index Migration"
Cohesion: 0.83
Nodes (3): downgrade(), _inspector(), upgrade()

### Community 128 - "Master Invoice Source Migration"
Cohesion: 0.83
Nodes (3): downgrade(), _inspector(), upgrade()

### Community 130 - "Price Read Index Migration"
Cohesion: 0.83
Nodes (3): downgrade(), _exists(), upgrade()

### Community 131 - "Historical Mobile Harness Failure"
Cohesion: 0.50
Nodes (4): Isolated browser server connection refused on port 3106, packing-profiles.spec.ts >> packing setup and layer controls remain usable on a phone — historical browser context, packing setup and layer controls remain usable on a phone, Packing profile review and stale-plan safety contract

### Community 132 - "Historical Dimension Harness Failure"
Cohesion: 0.50
Nodes (4): Isolated browser server connection refused on port 3106, packing-profiles.spec.ts >> staff use fixed workbook external dimensions and review safe capacity without internal measurements — historical browser context, staff use fixed workbook external dimensions and review safe capacity without internal measurements, Packing profile review and stale-plan safety contract

### Community 133 - "Register Navigation Harness Failure"
Cohesion: 0.50
Nodes (4): Bounded register pages and persistent filters, Isolated browser server connection refused on port 3106, record-registers.spec.ts >> batch register preserves filters and page through opening, Back and reload — historical browser context, batch register preserves filters and page through opening, Back and reload

### Community 134 - "Generated Next Type References"
Cohesion: 0.50
Nodes (3): ext_root_params_d_ts, ext_routes_d_ts, NOTE: This file should not be edited

### Community 150 - "Blank Historical Billing Image"
Cohesion: 0.67
Nodes (3): No visible interface content, Blank historical audit screenshot, White viewport

### Community 151 - "Blank Historical Login Image"
Cohesion: 0.67
Nodes (3): No visible interface controls, Historical blank screenshot, Blank white viewport

### Community 152 - "Blank Historical Quantity Image"
Cohesion: 0.67
Nodes (3): Blank white viewport, No visible interface elements, Historical browser screenshot

### Community 153 - "Historical Logout Backend Evidence"
Cohesion: 0.67
Nodes (3): logout-price-policy-backend historical audit evidence, Disposable PostgreSQL test environment requirement, logout-price-policy-backend recorded pytest result

### Community 154 - "Historical R0 R1 Evidence"
Cohesion: 0.67
Nodes (3): r0-r1-backend historical audit evidence, Disposable PostgreSQL test environment requirement, r0-r1-backend recorded pytest result

### Community 155 - "Historical R0 R2 Evidence"
Cohesion: 0.67
Nodes (3): r0-r2-backend historical audit evidence, Disposable PostgreSQL test environment requirement, r0-r2-backend recorded pytest result

### Community 156 - "Historical R0 R3 Evidence"
Cohesion: 0.67
Nodes (3): r0-r3-backend historical audit evidence, Disposable PostgreSQL test environment requirement, r0-r3-backend recorded pytest result

### Community 157 - "Historical R0 R4 Evidence"
Cohesion: 0.67
Nodes (3): r0-r4-backend historical audit evidence, Disposable PostgreSQL test environment requirement, r0-r4-backend recorded pytest result

### Community 158 - "Historical R5 Retry Evidence"
Cohesion: 0.67
Nodes (3): r5-request-replay-backend historical audit evidence, Disposable PostgreSQL test environment requirement, r5-request-replay-backend recorded pytest result

### Community 159 - "Historical R6 Storage Evidence"
Cohesion: 0.67
Nodes (3): r6-source-storage-backend historical audit evidence, Disposable PostgreSQL test environment requirement, r6-source-storage-backend recorded pytest result

### Community 160 - "Historical R7 Budget Evidence"
Cohesion: 0.67
Nodes (3): r7-work-budgets-backend historical audit evidence, Disposable PostgreSQL test environment requirement, r7-work-budgets-backend recorded pytest result

### Community 161 - "Historical R8 Read Evidence"
Cohesion: 0.67
Nodes (3): r8 read scaling backend, Disposable PostgreSQL test gate, R8 historical backend test result

### Community 162 - "Historical Source Inventory"
Cohesion: 0.67
Nodes (3): inventory summary, Historical source inventory, Historical optimizer timing samples

### Community 164 - "Repository Agent Guidance"
Cohesion: 0.67
Nodes (3): AGENTS, Installed Next.js documentation first, CLAUDE

## Ambiguous Edges - Review These
- `Internal final-invoice boundary` → `Historical settlement scope`  [AMBIGUOUS]
  docs/PLATFORM_OVERVIEW.md · relation: conceptually_related_to
- `Historical NGA exception` → `Staff-selected EL mode`  [AMBIGUOUS]
  docs/PACKING_DECISION_FLOWCHARTS.md · relation: conceptually_related_to
- `Derived box weight` → `Historical measured-floor workflow`  [AMBIGUOUS]
  docs/PROCUREMENT_PACKING_IMPLEMENTATION.md · relation: conceptually_related_to

## Knowledge Gaps
- **234 isolated node(s):** `fresh-produce-platform`, `InvoiceLine`, `Invoice`, `BillingQueueItem`, `MissingItem` (+229 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1085 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **51 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Internal final-invoice boundary` and `Historical settlement scope`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Historical NGA exception` and `Staff-selected EL mode`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Derived box weight` and `Historical measured-floor workflow`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._

## Graph Health Warning

785 dangling-endpoint edges; 4 self-loop edges; 869 collapsed undirected edges.
The graph may be incomplete. These are extraction diagnostics, not application defects. Raw nodes and relationships are retained in `extraction-audit.json`; full diagnostic counts are in `graph-health.json`.
