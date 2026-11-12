# Release readiness — 8 November 2026

Production is pending. Application fixes and local rehearsals do not replace verified hosted staging, approved master data or acceptance by the staff using the actual devices.

| Requested work | Result |
| --- | --- |
| Correct missing invoice customer details in context | Implemented and browser verified: country/email correction, invoice revision checks, concurrent-edit protection and audit history. Later edits through this flow preserve issued invoice contact details. |
| Complete repository checks and release CI | Full backend Ruff clean; 951 backend tests pass including 19 PostgreSQL races; frontend lint/type checks and 16 unit tests pass. Linux image builds pass. Final browser and Linux results are recorded below. |
| Make phone upload easier | Optional guidance collapses on small screens, the file chooser is visible without scrolling, and help remains available. |
| Complete Thai recovery messages and login retry | Billing correction/recovery messages translated. Sign-in shows the server's retry countdown, disables early retries and preserves entered fields. |
| Review launch master data | Hosted application data inspected in a PostgreSQL READ ONLY transaction; no customer details, prices or measurements changed. Business confirmation is outstanding. |
| Rehearse staging and recovery | Empty local PostgreSQL migrated to `20261108_0026`; local backup/restore compared 30 tables and verified source integrity. Production containers, proxy configuration and deployment rollback sequencing checked. Hosted staging remains pending access/configuration. |
| Staff pilot | [Acceptance script and sign-off sheet](STAFF_PILOT.md) prepared for the operator, buyer, packer, accountant and owner. Human/device acceptance is outstanding. |

## Master data requiring confirmation

Read-only audit of the application's configured hosted database, currently at migration `20261031_0025`:

- 158 customers; these seven lack both country and billing email: `CD-MAIN`, `DTD-MAIN`, `ITM-MAIN`, `JCT-MAIN`, `LYT-MAIN`, `MLD-MAIN`, `PARIS-MAIN`.
- 339 open-order lines checked using each shipment's date and the application's price resolution rules; 38 lack an applicable price. No contradictory prices found for those lines; no nonpositive prices among 4,040 general and 32,334 customer selling-price records. Existing valid prices still need accounting approval.
- 829 active products; 828 have unreviewed packing settings and volume. All 15 active boxes and all 3,180 pack profiles remain unreviewed. This is a review status, not proof that every value is wrong. Confirm the intended launch subset and measure it before marking it reviewed.
- The local environment uses local Storage, legacy packing mode and empty invoice bank settings. These are local settings, not evidence of the hosted server configuration. Production requires private Supabase Storage; bank instructions and packing rollout settings need owner confirmation.

The detailed local audit and missing-price CSV are ignored work artifacts, available in `.codex-artifact/launch-data-audit.json` and `.codex-artifact/missing-launch-prices.csv`. They are intentionally excluded from release images and published CI artifacts. Do not invent customer contacts, selling prices or measurements to clear a warning.

Customer format holds remain: `440T.xlsx`, `51H-2.xlsx`, `86L.ods` on standby; `705H.xlsx` requires manual review of which Order column is current. Filename/customer-code matching is checked alongside source structure; changed or uncertain files require review.

## Verification evidence

- Backend full local suite: **951 passed**, including PostgreSQL concurrency checks; no skips. After a Windows parser timeout exposed a child-process cleanup race, cleanup now waits for the worker job before removing its temporary directory; the focused resource/intake suite passes (38 tests).
- Frontend: ESLint has zero errors and two existing logo-image optimization warnings; TypeScript passes; all **16 unit tests pass**. UI detector reports no findings for the changed screens.
- Next.js upgraded to **16.4.5**, with compatible dependency updates. The [framework security advisory](https://github.com/advisories/GHSA-2464-fhr4-w9xw) identifies the patched release line. `npm audit --omit=dev` reports **zero vulnerabilities**. Eight moderate development-only advisory entries remain in the linter's minimatch dependency chain; no forced framework downgrade or unverified major dependency override was applied.
- Both Linux production images build successfully using locked dependencies. API `/ready`, frontend `/login`, frontend-to-API `/backend/ready` return HTTP 200 on disposable local services; container logo hash matches the source file.
- Caddy configuration validates. Deployment sequencing passes for initial release, migration failure, promotion, application rollback, HTTPS failure, Hostinger hostname and rejection of a bare IP address.
- Empty local PostgreSQL upgrades through all migrations to `20261108_0026`. A synthetic database backup restored to a separate database with identical counts in all 30 tables and verified source file hash; local rehearsal duration about three seconds. This is not an estimate for production recovery time.
- Full Linux backend suite against the production API image and disposable PostgreSQL: **951 passed**, no skips. The PostgreSQL test fixtures now explicitly select their intended legacy packing mode instead of inheriting the developer's local configuration.
- Private-workbook inventory/regression cases run locally with the source folder and are explicitly optional in CI. Synthetic 19T/931L examples exercise moved headers, identity conflicts, missing columns, Thai pack units, markers/formulas and isolated-worker intake in every CI run.
- Browser release gate: **40 passed**, with one explicitly optional live-account test skipped. Includes 18 smoke, 9 real API regressions, 2 full client/workspace journeys, 8 language and 3 release-readiness cases. The skipped case requires real account credentials; synthetic workflow and access tests run against disposable data.

## Hosted staging and production gate

The connected Supabase tool is configured for another application; it was not used as the Box staging database. The Hostinger SSH attempt was stopped by strict host-key checking because its ED25519 key is not yet trusted locally. Required inputs: the separate Box staging project reference and the SSH host-key fingerprint independently read from the Hostinger console. Keep passwords/private keys out of chat.

Once these are available, use the existing [deployment runbook](README.md): complete GitHub CI, deploy the tested immutable images to staging, validate private bucket/source retrieval and real HTTPS authentication, rehearse database plus Storage restoration and previous-image rollback, then execute [the staff pilot](STAFF_PILOT.md). Verify real printing and QR scanning. Confirm owner/accounting/customer and packing data for the launch scope before production.

No production deployment, hosted migration, staff sign-off, measured physical packing calibration or real printer/camera acceptance is claimed here.
