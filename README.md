# Fresh Produce Platform

**8 November 2026, local audit fixes:** Imports detect new customer columns after
summary columns; generic PDFs preserve source units and require quantity review.
New customer records leave unknown billing emails and destination countries blank;
complete those fields before invoice dispatch. Existing customer records still need
source-based review. Seeded Excel template reads no longer lock the main customer,
and bulk billing loads prices together while retaining pack, date and conflict checks.

Authentication now uses shared database counters: at most 10 login attempts per
account and 50 per client address in each 15-minute window, with HTTP 429 and
`Retry-After` when exceeded. Apply Alembic migration `20261108_0026` before starting
the updated API; authentication fails closed if its counter table is unavailable.
The production gateway supplies the client address; the API does not trust arbitrary
forwarding headers itself. Hosted migration and application deployment remain pending.

**28 October 2026, hosted database:** The configured Supabase database was upgraded
from `20261025_0015` to `20261027_0024` and independently verified. Existing records
were preserved and all three referenced source files backed up. Application
deployment, customer-format approval and packing review remain pending. See
[migration evidence](docs/HOSTED_MIGRATION_2026-10-28.md).

**27 October 2026, recurring Excel formats:** All 24 supplied Excel references now
have versioned customer mappings, staff confirmation of missing unit conventions,
and source-cell review before procurement. Packing supports editable small-order
foam cutoffs for garlic/onion catalog records, initially 1 kg per product and
subcustomer shipment. Migration head is `20261027_0024`; application
deployment remains pending. See [Recurring Excel imports](docs/RECURRING_EXCEL_IMPORT.md).

**27 October 2026, R9:** Internal staff/admin/finance only; work ends at final invoice creation. Payment/refund/receipt workflows are excluded. Operational exceptions are verified locally: 589 backend passes, 16 guarded skips; candidate Supabase/Alembic head 0023, hosted evidence still 0015. See [R9 details](docs/audits/2026-10-26/R9_INTERNAL_WORKFLOW.md) and [remaining work](docs/audits/2026-10-26/REMAINING_WORK.md).

**27 October 2026, R6:** Private Supabase Storage and upload/source recovery are implemented locally. See the [storage configuration, legacy cutover and recovery runbook](docs/audits/2026-10-26/R6_SOURCE_STORAGE.md). Production now requires the server-only Storage settings in the environment examples; hosted cutover is pending.

A modular monolith for the order-to-procurement-to-packing workflow described in
`PRD_BRD_DOCS/PRD_Fresh_Produce_Platform.md`.

Production planning uses **Supabase Database** as the main database and private
Supabase Storage as the planned durable document store. See the
[production baseline and report index](docs/audits/2026-10-26/PRODUCTION_BASELINE.md)
for the full reliability requirements, open findings and release gates. Storage
migration and application remediation remain planned.

## Documentation for users

The **ไทย / EN** buttons on the sign-in page and app header switch the interface
between Thai and English without clearing forms. The choice is saved in the browser;
Thai browsers start in Thai when no choice is saved. Filenames, customer/product
records, SKUs and submitted quantities keep their original values. PDF exports keep
their existing document language. Interface translations live in `web/lib/translations.ts`;
new server messages fall back to their original wording until translated there.

- [Documentation index](docs/README.md): where to start and which guide to use.
- [User guide](docs/USER_GUIDE.md): bullet instructions for signing in, uploading
  orders, buying produce, packing boxes, printing stickers, and billing.
- [Platform overview](docs/PLATFORM_OVERVIEW.md): workflow, responsibilities,
  records, and current capability boundaries.

New operators should start with the user guide's daily workflow checklist.

## Architecture

- `backend/app/modules`: business capabilities and lifecycle invariants
- `backend/app/api`: FastAPI transport adapters
- `backend/app/models`: SQLAlchemy persistence model
- `web`: Next.js operator portal and packing-floor interface

Phase 4 has started with the order-to-cash settlement slice: Handoff 02-backed
invoice drafts, accounting approval and dispatch, partial/full payment gates,
hashed receipt records, and an operator billing screen.

The two agents are bounded workflow identities. Calculations, state transitions,
packing constraints, and financial gates remain deterministic and auditable.

See [security hardening and release checks](SECURITY_HARDENING_2026-11-12.md)
for manual account seeding, current packing/note permissions, and owner release gates.

## Local development

For a Hostinger VPS deployment, see the [deployment plan](deploy/hostinger/PLAN.md)
and [server/CI/CD setup guide](deploy/hostinger/README.md). The production bundle
uses Docker Compose with Caddy HTTPS, private app ports, existing Supabase services,
and tested GHCR images. It runs migrations as a controlled release step.

The application uses Supabase for its managed PostgreSQL database. PostgreSQL no
longer runs in the local Docker Compose stack.

1. Create a Supabase project and open **Connect** in its dashboard.
2. Copy the **Session pooler** connection URI. Session mode on port `5432` is the
   safest default for this long-running FastAPI service and works on IPv4 networks.
3. Copy `.env.example` to `.env` at the project root, set
   `SUPABASE_DATABASE_URL` to that URI, and replace `SECRET_KEY`. Percent-encode
   special characters in the database password. Keep `sslmode=require` in the URI.
4. Run `docker compose up --build`. The current local stack applies Alembic
   migrations to Supabase before API startup. The production plan moves migration
   execution into one controlled release step. Redis is not a current Compose service.

For development without Docker:

1. Copy `backend/.env.example` to `backend/.env` and add the same Supabase URI.
2. In `backend`, run `python -m pip install --group dev -e .`,
   `alembic upgrade head`, then `uvicorn app.main:app --reload`.
3. In `web`, copy `.env.example` to `.env.local`, run `npm install`, then
   `npm run dev`.

On Windows, `npm run dev` stores Next.js development output under
`%LOCALAPPDATA%\nextjs-dev` and links `web/.next/dev` to it with a directory
junction. This keeps compilation and cache writes on the system drive when the
source folder is on a slower disk. The first run preserves the previous cache
inside `web/.next/dev-before-ssd-*`; stop any existing dev server before switching.
Each checkout has its own persistent cache. Production builds still use
`web/.next`, and other operating systems use the standard development directory.
CLI options continue to work, for example `npm run dev -- --port 3001`.

Use Supabase's direct URI for one-off administration tools when IPv6 is available.
If the application is later deployed as serverless workers, use the transaction
pooler on port `6543`; the backend automatically disables its local SQLAlchemy pool
for that mode.

Run backend tests with `python -m pytest` from `backend`.

See [`SUPABASE_SETUP.md`](SUPABASE_SETUP.md) for the complete setup, verification,
security, and troubleshooting checklist.
