# Supabase Database Setup Guide

**27 October 2026, R9:** Internal staff/admin/finance only; work ends at final invoice creation. Payment/refund/receipt workflows are excluded. Operational exceptions are verified locally: 589 backend passes, 16 guarded skips; candidate Supabase/Alembic head 0023, hosted evidence still 0015. See [R9 details](docs/audits/2026-10-26/R9_INTERNAL_WORKFLOW.md) and [remaining work](docs/audits/2026-10-26/REMAINING_WORK.md).

**27 October 2026, R6:** Private Supabase Storage and upload/source recovery are implemented locally. See the [storage configuration, legacy cutover and recovery runbook](docs/audits/2026-10-26/R6_SOURCE_STORAGE.md). Production now requires the server-only Storage settings in the environment examples; hosted cutover is pending.

Reconciled 26 October 2026 with the
[production baseline](docs/audits/2026-10-26/PRODUCTION_BASELINE.md) and
[Supabase reliability plan](docs/audits/2026-10-26/SUPABASE_RELIABILITY_PLAN.md).
These setup steps describe the existing stack; production requires the documented
correctness, Storage, backup/restore, concurrency, monitoring and release gates.

This project uses Supabase as its managed PostgreSQL database. FastAPI,
SQLAlchemy, psycopg2, and Alembic remain responsible for application logic,
database access, and schema migrations. The local Docker stack does not run a
PostgreSQL container.

## 1. Create the Supabase project

1. Sign in at <https://supabase.com/dashboard>.
2. Create a new project.
3. Select the organization and a region close to the application deployment.
4. Generate and securely save a strong database password.
5. Wait for the project to finish provisioning.

Do not commit the project password, database URI, service-role key, or secret API
key to Git.

## 2. Copy the database connection URI

1. Open the Supabase project.
2. Select **Connect** at the top of the dashboard.
3. Select **Session pooler**.
4. Copy the connection URI for port `5432`.

The URI will resemble:

```text
postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

Session pooler mode is the recommended default for this persistent FastAPI
backend, particularly when the development or deployment network is IPv4-only.

Add `?sslmode=require` to the URI if the copied value does not already contain an
SSL mode:

```text
postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
```

The backend also adds `sslmode=require` automatically for recognized Supabase
hosts, but keeping it in the environment value makes the requirement explicit.

### Password encoding

Reserved URL characters in the database password must be percent-encoded. Common
examples include:

| Character | Encoded value |
| --- | --- |
| `@` | `%40` |
| `:` | `%3A` |
| `/` | `%2F` |
| `#` | `%23` |
| `%` | `%25` |
| `?` | `%3F` |

Use the complete connection URI supplied by the Supabase dashboard whenever
possible.

## 3. Configure environment variables

### Docker Compose from the project root

Copy the root example file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and provide real values:

```env
SUPABASE_DATABASE_URL=postgresql://postgres.PROJECT_REF:ENCODED_PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
SECRET_KEY=replace-with-a-long-random-application-secret
```

The `.env` file is ignored by Git. Do not place credentials in `.env.example`.

### Backend without Docker

From the project root:

```powershell
Copy-Item backend/.env.example backend/.env
```

Edit `backend/.env`:

```env
ENVIRONMENT=development
SECRET_KEY=replace-with-at-least-32-random-characters
SUPABASE_DATABASE_URL=postgresql://postgres.PROJECT_REF:ENCODED_PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
CORS_ORIGINS=["http://localhost:3000"]
```

## 4. Start with Docker Compose

Ensure Docker Desktop is running, then execute from the project root:

```powershell
docker compose up --build
```

This starts:

- FastAPI on <http://localhost:8000>.
- Next.js on <http://localhost:3000>.

Redis is not defined in the current Compose stack and is not required by the
current backend. Supabase provides the database outside this local stack.

Before FastAPI starts, the API container executes:

```text
alembic upgrade head
```

This creates or updates the application tables in the configured Supabase database.
For production, use one controlled release migration step rather than running
Alembic from every API replica, as specified in the production plan.

To stop the local services:

```powershell
docker compose down
```

This does not delete the Supabase database.

## 5. Start without Docker

### Backend

Install and start the backend:

```powershell
Set-Location backend
python -m pip install --group dev -e .
alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend

In a second terminal:

```powershell
Set-Location web
Copy-Item .env.example .env.local
npm install
npm run dev
```

## 6. Verify the migration

Check the current Alembic revision:

```powershell
Set-Location backend
alembic current
```

Inspect the expected migration head from the checked-out source:

```text
alembic heads
```

The R0–R4 follow-up adds migrations `20261026_0016`, `20261026_0017`, `20261026_0018` and `20261027_0019`. Source expects the matching schema; configured hosted Supabase is still at `20261025_0015`. See the [implementation ledger](docs/audits/2026-10-26/IMPLEMENTATION_PROGRESS.md) for rollout and validation limits. Prepared migrations have not been applied to hosted Supabase by this work.

Compare it with `alembic current` for the intended project. At the 26 October
audit snapshot the source head is `20261025_0015`; do not reuse that value for a
later release without checking its source migrations.

You can also verify the tables in Supabase:

1. Open **Table Editor** in the Supabase dashboard.
2. Confirm that the application tables are visible.
3. Open **SQL Editor** and run:

```sql
select version_num from alembic_version;
```

The result should match the source migration head for the release being deployed.
Do not treat an older hardcoded revision example as the current schema version.

Run the automated backend tests separately:

```powershell
Set-Location backend
python -m pytest
```

Tests use an in-memory throwaway database and never connect to the Supabase
project.

## 7. Connection choices

Use the connection type that matches the runtime:

| Runtime | Connection | Port |
| --- | --- | --- |
| Current persistent FastAPI backend on IPv4 | Session pooler | `5432` |
| Persistent backend with working IPv6 | Direct connection | `5432` |
| Serverless or short-lived workers | Transaction pooler | `6543` |

The backend detects a Supabase transaction-pooler URL on port `6543` and disables
the additional SQLAlchemy client-side pool. The current Dockerized FastAPI service
should normally use session mode on port `5432`.

## 8. Security checklist

- Keep `SUPABASE_DATABASE_URL` server-side only.
- Never expose the database URI through a `NEXT_PUBLIC_` variable.
- Never place a Supabase service-role or secret key in browser code.
- Enable SSL enforcement in **Database Settings > SSL Configuration** when ready.
- Restrict database network access when deployment IP addresses are stable.
- Rotate the database password immediately if it is committed or shared publicly.
- Use separate Supabase projects for development, staging, and production.
- Back up production data before applying destructive migrations.

This application currently connects from its trusted FastAPI backend using a
PostgreSQL connection string. It does not use the Supabase Data API or Supabase
Auth in its application code. Review actual exposed-schema grants, RLS and callable
objects in the Supabase project even while the app uses only FastAPI, so an
alternate interface cannot bypass backend policy. A later direct-client or
Supabase Auth integration needs a deliberate identity and access-policy design.

## 9. Troubleshooting

### `SUPABASE_DATABASE_URL` is missing

Copy the appropriate example file to `.env` and replace the placeholder URI. The
backend intentionally refuses to start without a database URL.

### Password authentication failed

- Copy the connection URI again from **Connect**.
- Confirm the project reference and username.
- Reset the database password if necessary.
- Percent-encode reserved characters in the password.

### Connection refused or hostname unreachable

- Confirm that the Supabase project is active.
- Use **Session pooler** rather than the direct URI on IPv4-only networks.
- Check firewall, VPN, proxy, and Supabase network-restriction settings.
- Confirm that the host and port match the dashboard connection URI.

### SSL error

Confirm that the URI contains:

```text
?sslmode=require
```

For stricter certificate and hostname validation, download the database CA
certificate from Supabase and configure `sslmode=verify-full` with the appropriate
certificate path.

### Tables do not exist

Run from `backend`:

```powershell
alembic upgrade head
alembic current
```

Check that both commands are reading `backend/.env` and pointing at the intended
Supabase project.

### Docker cannot connect to the Docker API

Start Docker Desktop and wait until its Linux container engine is ready, then run:

```powershell
docker compose up --build
```

## 10. Relevant project files

- `.env.example` — root Docker environment template.
- `backend/.env.example` — direct backend-development template.
- `docker-compose.yml` — canonical full local stack.
- `backend/app/core/config.py` — environment configuration.
- `backend/app/core/database.py` — SQLAlchemy connection and pooling policy.
- `backend/alembic/env.py` — migration database configuration.
- `README.md` — shorter project startup guide.

## Official references

- <https://supabase.com/docs/guides/database/connecting-to-postgres>
- <https://supabase.com/docs/guides/platform/ssl-enforcement>
- <https://supabase.com/docs/guides/database/overview>
