# Staff acceptance before production

Run this on isolated staging with approved sample orders and separate Database/Storage credentials. Record the release commit, CI run, staging origin, device/browser, printer, label size, participants and date. Do not use production orders for destructive recovery or race tests.

## Prepare

Owner: choose one normal customer Excel, one multi-customer order, one PDF and one unfamiliar/changed layout. Record expected customer identities, every nonempty order quantity cell, SKU, unit, quantity, purchase total and invoice total independently from the source. Keep `440T.xlsx`, `51H-2.xlsx` and `86L.ods` on standby. `705H.xlsx` must stop for review until its current Order column is confirmed.

Confirm the seller and recipients' country/email with the customer, selling prices with accounting, packing measurements and safe capacities with the floor lead, and bank instructions with the owner. Do not mark estimated measurements reviewed without measuring them. Verify there are separate General and Accounting accounts; only the owner manages user access.

## Run without coaching

| Participant | Task | Pass evidence |
| --- | --- | --- |
| Order operator | Sign in on the actual phone, select the customer file, shipment date and route, review detected template/customer and every quantity, then confirm once. Switch Thai/English during review. | File chooser visible without scrolling; source names/quantities unchanged by language; source downloadable; every expected source cell accounted for once; unknown layout/customer or uncertain units stop for review; one resulting batch after retry. |
| Buyer | Open that batch, compare packs/kg, edit actual purchases and export the buyer sheet, then send to packing. | Correct customer/file/date; purchase quantities and totals match independent expectations; exported sheet legible; completed action cannot be repeated. |
| Packer | Open the order, inspect packing plan, change contents if needed, print a sticker and scan it on the actual device, then finalize. | Correct SKU/pack count; no capacity/temperature conflict; QR readable at the intended label size; changes preserved; final pack locked; scanner opens the same box. |
| Accountant | Generate draft from the final pack, resolve missing prices, correct missing customer country/email, review and issue final invoice. | Amounts independently match the source and final pack; blank details block issue; correction saves without losing draft edits; printable invoice readable; issued amounts and contact details remain stable after later master-data changes. |
| Owner + second staff member | Edit the same order/invoice from two browsers; retry after a connection interruption; sign out and revisit a protected URL. | Stale edit produces a recoverable conflict; no duplicate order/invoice; saved values retained; no access after logout; General cannot open Billing/Users. |

Include a changed-layout file containing an extra order column, a PDF with count units, and a cancelled order. Verify that a review is required when the system cannot safely interpret the source. Compare expected and extracted lines before accepting the template. Passing sample files does not guarantee every future layout is correct.

## Record results

| Release / task / participant | Elapsed time | Help needed | Expected versus actual | Error or confusing wording | Evidence | Pass / fail |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |

Allow a quiet first attempt. Note every point where someone needs help or asks what to do next. Fix blocking problems and repeat the affected task with that staff member. A technical browser run does not count as staff acceptance.

## Staging recovery and go-live decision

Deployment operator: back up staging Database and private Storage separately, restore both to an isolated target, check migrations, row counts, source references/hashes and a printable invoice. Record recovery duration and lost-data window. Rehearse a schema-compatible rollback using the previous immutable image bundle. Confirm public HTTPS readiness, login/logout, upload/source retrieval and monitoring alerts.

Record each role's sign-off and the owner-approved customer/price/packing scope. Production stays pending until hosted staging, physical printing/scanning, required master data and staff acceptance pass. Promote the same tested image digests; do not rebuild during promotion.

Sign-off: order operator ___ buyer ___ packer ___ accountant ___ deployment operator ___ owner ___
