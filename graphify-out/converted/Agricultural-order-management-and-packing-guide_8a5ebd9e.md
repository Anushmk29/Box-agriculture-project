<!-- converted from Agricultural-order-management-and-packing-guide.docx -->

Agricultural order management and packing guide
A guide for explaining the system to sales and operations teams
Prepared from this conversation on 9 October 2026. This document uses only information discussed in this session. It does not add new research, product settings or business rules.
The software combines customer demand to simplify purchasing, then plans packing separately for each sub-customer. Products are grouped using saved packing classifications, boxes are filled within configured weight and volume limits, and staff weigh the boxes before manager approval.
This guide covers the customer hierarchy, intake and acknowledgment, purchasing and buffers, SKU review, the complete packing flow, EL and combinable rules, a worked example, box capacity and numbering, staff instructions, the requirement audit and the diagram inventory.
The implementation findings are from the source-code audit discussed in the session, not a live end-to-end test. Example quantities and box capacities are illustrative. They are not settings verified in the actual product catalogue.
## A short explanation for a customer meeting
We combine orders to buy efficiently, but pack separately for each customer. The system checks which products are allowed together, fills suitable boxes within weight and volume limits, and creates numbered labels. Staff then pack and weigh the boxes, and a manager approves the final shipment.

# Business model and system overview
The project is custom order management and aggregation software for an agricultural export business based in Thailand. International customers send produce orders in Excel or PDF files. The stated buyer markets include Norway, Denmark, France, Switzerland and Finland.
A main customer collects orders from their own sub-customers. The examples discussed were Himanshu in Norway, Sophie in Finland and James in Africa, with Anush, Pratap and Priyanshu as example sub-customers. These are business examples; we did not establish that those records are populated in the database.
The central objective is to combine itemized demand into a master purchasing list for the internal buying team. A manual dashboard action can then combine main-customer orders into a unified market purchase. Packing remains organized by each sub-customer order.
The architecture overview discussed staff and an AI agent using a Next.js frontend, a FastAPI backend, authentication and permissions, operational modules and PostgreSQL. The operational areas include order intake, procurement, packing, invoicing, user administration, master data, scanning, handoffs and the operations dashboard.
01  Main customer sends a file containing sub-customer orders
â†“
02  System identifies sub-customers and prepares acknowledgment
â†“
03  Staff resolves unknown SKUs and validates orders
â†“
04  System aggregates demand for purchasing
â†“
05  Buyer confirms quantities and passes them to packing
â†“
06  System plans boxes separately for each sub-customer
â†“
07  Staff packs and weighs boxes
â†“
08  Manager approves and locks packing
â†“
09  Handoff 02 returns final packing details for invoicing
Buying together does not mean putting different sub-customers' products into one shared customer box.

# Order intake and acknowledgment
Excel intake supports matrix and universal uploads, producing separate sub-customer orders. PDF intake exists, but the audited parser returns one customer draft per PDF. Multi-sub-customer PDF support was not established, and some PDF parser paths use fixed customer codes.
The acknowledgment behavior requested for Agent 1 is implemented in the intake service. Upload intake prepares acknowledgment data and exposes a downloadable CSV; it is not a separately named Agent 1 component.
The CSV contains serial number, sub-customer name, customer code, order ID, order reference and total sub-customer count. It deliberately excludes itemized product details.
This lets a main customer compare the reported count and IDs with their own list. If they expect 50 sub-customers and see 48, they can identify missing submissions. The system does not automatically compare against an expected roster of 50 customers.
Unknown or misspelled names must be reviewed by staff under the stated business policy. The dashboard provides unresolved-product review, suggestions, search, manual selection and the option to create and map a master product. Unresolved rows block splitting.
## SKU acceptance and the current limitation
Fuzzy suggestions are not automatically accepted into purchasing. Staff-verified mappings and exact names are accepted. An exact recognized SKU code can also resolve a row even when its name is unrecognized. Therefore, a policy requiring every name/code mismatch to receive human review is not fully enforced by the current acceptance rules.

# Purchasing aggregation and yield buffers
After an upload is split into validated sub-customer orders, the system automatically creates a consolidated purchasing batch. It sums quantities and kilograms by product and pack size. Different pack sizes remain separate demand lines.
Main-customer relationships are retained and displayed in source information. However, the buyer-sheet export discussed is headed by batch reference, date and route rather than the main customer's name. Issuing the purchase document explicitly under that name remains a partial requirement.
Procurement staff can manually select complete main-client order files and combine them. Files must share a shipment date. Source batches are replaced to prevent duplicate buying; confirmed sources cannot be freely rebuilt.
Staff can enter a yield buffer for a product line, such as 10%, 12% or 15%, or apply one percentage across a batch. The buffer is applied to base demand rather than compounded every time it is edited.
An individual-line edit can save the percentage as the product's default. Future batches use this remembered value until staff update it. Bulk batch edits change the current batch but do not update each product's remembered default.
## Buffer example
For 3.00 kg of bananas and a 10% buffer, the purchasing target is 3.30 kg. The extra buying allowance is not automatically an instruction to give a sub-customer 10% extra bananas. Packing uses confirmed quantities passed to it. Critical actions such as combining purchases, changing buffers and mapping unknown SKUs remain human-controlled.

# Boxing flow from confirmed order to final handoff
01  Start with a sub-customer order after Handoff 01
â†“
02  Read confirmed pack counts, net weights and product volume
â†“
03  If required data is missing, correct it before planning
â†“
04  Ignore zero-quantity lines and calculate weight per pack
â†“
05  Process individual packs from heavier to lighter
â†“
06  Build the compatibility group for the next pack
â†“
07  Does an existing compatible box fit its weight and volume?
â†“
08  YES: choose the fitting box with least remaining weight capacity
â†“
09  NO: choose a fitting active catalogue option and create a box
â†“
10  If no option fits, stop and correct data or box availability
â†“
11  Add the pack and update weight, count and volume
â†“
12  Repeat until all packs have been allocated
â†“
13  Save boxes in creation order and number them from 1
â†“
14  Generate stickers and QR identities
â†“
15  Staff physically packs, corrects contents and weighs every box
â†“
16  Staff requests manager approval
â†“
17  If rejected, correct packing and resubmit
â†“
18  If accepted, validate weights and lock final packing
â†“
19  Return Handoff 02 with final packing details
This is the normal packing workflow. The separate admin fast-track route is described later. Box selection checks compatibility first, then both weight and estimated volume.

# Product grouping rules including EL

EL is a saved business classification. The inspected implementation does not define what the letters stand for or independently determine a product's legal classification. Staff must maintain correct master data. COMBINABLE does not mean a product may be mixed with anything. The NGA exception needs confirmation against the client's packing policy.

# How box weight and size are determined
The system uses two data sources: the box catalogue and confirmed product/order data. Each box option has material, volume and maximum safe net weight, stored as max_safe_net_weight_kg. Staff must configure this correctly; the software does not physically test or discover a box's strength.
Weight per pack equals confirmed product net weight divided by confirmed pack count. For example, 10 banana packs with a confirmed total weight of 5 kg give 0.50 kg per pack.
For each pack, the planner checks that current box weight plus the next pack does not exceed the safe net-weight limit. It also checks that used estimated volume plus the next pack's volume does not exceed 92% of box volume. The remaining 8% is spare volume.
If several existing compatible boxes fit, the planner selects the one with the least remaining weight capacity. If a new box is required, it chooses the fitting active catalogue option with the lowest safe net-weight capacity. It does not compare complete arrangements to guarantee the fewest boxes.
A box may fill by volume before reaching its weight limit. Estimated volume does not simulate product shape or physical arrangement. Staff still need to check the real fit.

Another compatible 0.50 kg pack fits only if volume also allows it. Actual net weight is checked against the configured capacity with a 0.01 kg tolerance. Gross weight must be at least net weight, but the discussed logic does not enforce a separate maximum gross-weight limit.

# Worked order example
Illustrative order: Himanshu is the main customer and Anush is a sub-customer. Assume the buyer confirms the original quantities. The following classifications, capacities and volumes are examples, not verified catalogue settings.

## Assumptions for this example
Bananas and guava are both COMBINABLE, NORMAL, CARDBOARD, FRUIT and HEAVY. Leafy greens are COLD, FOAM, LEAFY and LIGHT. EL products A and B share material, temperature, category and weight class, but have different botanical families. Route is GENERAL. Suitable cardboard and foam options each allow 2 kg net, and estimated volumes fit. These assumptions explain the next page's result.

# Example allocation and box numbering
The remaining space in a leafy or EL box cannot be filled with incompatible fruit. A product may appear in multiple boxes because individual packs are allocated until weight or volume limits are reached.
The explanatory labels fruit box A and fruit box B are not predicted Box #1 and Box #2. Actual numbering follows box creation order, including tie-breaking when pack weights match.
Each order starts with Box #1. Numbers do not identify a category or priority. A manually added box receives the highest existing number plus one. The sticker format is parent customer code–customer code / box number of total boxes. QR identity also includes route, shipment date and a unique identifier.
01  Anush's confirmed order: 18 packs and 7 kg
â†“
02  Separate into compatible fruit, leafy, EL family A and EL family B
â†“
03  Fruit group: 4 kg total requires two 2 kg boxes
â†“
04  Fruit box A: 4 banana packs = 2 kg
â†“
05  Fruit box B: 2 banana packs + 4 guava packs = 2 kg
â†“
06  Leafy group: 4 packs = 1 kg in a separate foam box
â†“
07  EL family A: 2 packs = 1 kg in a separate box
â†“
08  EL family B: 2 packs = 1 kg in a separate box
â†“
09  Result: 5 boxes and 7 kg planned product net weight
â†“
10  Assign numbers in actual creation order
â†“
11  Staff packs and weighs all boxes; manager approves
In the screenshots, coconut jelly split across boxes could be explained by capacity. Wax gourd, shallot, sweets and lemongrass together warranted checking their saved classifications and edit history. The screenshots alone did not establish why that particular order had 21 boxes.

# Packing staff instructions and approval
Follow the planned box contents and pack counts, then check physical fit and product condition. Use the available controls to correct quantities, substitute products, move items or change box size when needed.
Saving item changes clears the affected box's previously verified weights. Reweigh affected boxes after contents change. The discussed item-edit path checks active products, positive quantities and weights, material and content compatibility, safe weight and usable volume.
Record net weight for products only and gross weight for products plus packaging and other included material. Weights cannot be negative. Gross must be at least net, and net must remain within the safe box capacity.
Every box needs saved net and gross weights before staff request approval. The manager can reject packing with a correction note, after which staff correct and resubmit. Approval validates weights and locks the final boxes against normal edits.
Handoff 02 returns the final packing details for subsequent invoicing. Final output reflects the verified packing rather than only the original estimate.
## Separate admin fast track
The admin fast-track route reuses existing boxes or, if none exist, creates one box containing the ordered lines. It calculates template weights, checks safe net weight, approves and locks packing, and seals Handoff 02. It can bypass normal grouping and staff scale weighing. It must be explained separately from the normal workflow.

# Requirement audit and remaining gaps

The identified follow-ups were multi-sub-customer PDF support, main-customer names on purchasing exports, remembered defaults for bulk buffer edits if intended, review of name/code mismatches, and replacement of the silent 0.15 kg unknown-pack-size fallback. Additional cautions discussed were correct master classifications, the NGA exception, fast-track behavior, no minimum-box guarantee and no separate maximum gross-weight check. This guide records the audit; it does not claim those gaps have been fixed.

# Architecture diagrams discussed in this session
The earlier Archify work delivered 14 diagrams in docs/diagrams: one architecture overview and 13 feature sequence diagrams. The earlier delivery reported all 70 /api/v3 endpoints represented once, showcase validation passing and browser checks passing in light and dark mode. This guide repeats that session report; it does not independently revalidate coverage.
The feature areas were authentication, user administration, master data and SKU mappings, order intake and splitting, procurement, packing design and approval, floor packing and manifests, invoice drafting, invoice settlement, agent handoffs and the operations dashboard.

Orders, procurement and floor packing were split for readability. The simplified overview drew Procurement to PostgreSQL; Orders, Packing and Invoicing also write to the database. The generator was saved as docs/diagrams/generate.mjs, with manually maintained endpoint lists. Only overview and master-data screenshots were reported as visually reviewed. The core packing references discussed were backend/app/modules/packing/optimizer.py, service.py, floor.py and boxes.py.
| Rule | Current behavior |
| --- | --- |
| Customer ownership | Normal planning is performed for one sub-customer order at a time. |
| EL and combinable | Outside the NGA exception, EL and COMBINABLE products are separated. The code uses COMBINABLE rather than NON_EL. |
| EL family | EL products may share only if botanical family and all other grouping fields match. |
| Missing EL family | An EL product without family data is isolated by product ID. |
| Temperature | COLD and NORMAL products are separated. |
| Material | FOAM and CARDBOARD requirements are separated and matched to the box material. |
| Product category | LEAFY, FRUIT, HERB, ROOT, VEGETABLE, SPICE, PACKAGED_FOOD, PERSONAL_CARE and OTHER are grouping categories. Categories must match. |
| Weight class | LIGHT and HEAVY classifications are separated. |
| OTHER products | Unclassified OTHER products are isolated by product ID. |
| NGA route | For route code NGA, control-status and botanical-family separation is relaxed. Other grouping rules still apply. |
| Example contents in a 5 kg box | Net weight |
| --- | --- |
| 6 banana packs x 0.50 kg | 3.00 kg |
| 4 guava packs x 0.25 kg | 1.00 kg |
| Total product weight | 4.00 kg |
| Remaining weight capacity | 1.00 kg |
| Product | Packs | Kg per pack | Total kg |
| --- | --- | --- | --- |
| Bananas | 6 | 0.50 | 3.00 |
| Guava | 4 | 0.25 | 1.00 |
| Leafy greens | 4 | 0.25 | 1.00 |
| EL product A | 2 | 0.50 | 1.00 |
| EL product B | 2 | 0.50 | 1.00 |
| Total | 18 |  | 7.00 |
| Requirement | Session audit result |
| --- | --- |
| Excel intake and customer hierarchy | Implemented. |
| PDF intake | Partial: one draft per PDF; multi-sub-customer PDFs not established. |
| Count and ID acknowledgment without products | Implemented through intake and downloadable CSV. |
| Missing-order prevention | Supports manual checking; no expected-roster comparison. |
| Aggregated master purchase demand | Implemented by product and pack size. |
| Main-customer name on purchasing document | Partial: source relationship exists; export heading lacks name. |
| Manual combining and buffer inputs | Implemented. |
| Remember last item buffer | Individual-line default supported; bulk edits do not remember defaults. |
| No automatic misspelled-name processing | Partial under strict policy: exact code can resolve unknown name. |
| Human SKU mapping | Implemented; unresolved rows block splitting. |
| Total weight and box calculations | Implemented, subject to data and planner limitations. |
| Diagram group | Files |
| --- | --- |
| System overview | 00-system.html |
| Authentication and users | 01-auth.html; 02-admin-users.html |
| Master data | 03-master-data.html |
| Orders | 04-orders-a.html; 04-orders-b.html |
| Procurement | 05-procurement-a.html; 05-procurement-b.html |
| Packing design | 06-packing-design.html |
| Floor packing | 07-packing-floor-a.html; 07-packing-floor-b.html |
| Invoicing | 08-invoicing-draft.html; 09-invoicing-settle.html |
| Agents and dashboard | 10-agents-ops.html |