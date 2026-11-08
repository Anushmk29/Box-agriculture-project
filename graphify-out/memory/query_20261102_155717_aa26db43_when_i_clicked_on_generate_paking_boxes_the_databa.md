---
type: "query"
date: "2026-11-02T15:57:17.371763+00:00"
question: "when i clicked on generate paking boxes the database generation failed"
contributor: "graphify"
outcome: "useful"
source_nodes: ["PackingPlanningService", "planning.py"]
---

# Q: when i clicked on generate paking boxes the database generation failed

## Answer

Expanded graph vocabulary: packing prepare database order planning service profile boxes. Graph located PackingPlanningService at backend/app/modules/packing/service.py L19 and planning.py. Live diagnosis reproduced PostgreSQL UndefinedTable for packing_history_reviews: database was at 20261027_0024, while current planning code requires migration 20261031_0025. Applied existing Alembic migration with python -m alembic upgrade head. Both new evidence tables exist with RLS enabled. Verified prepare route HTTP 200 and committed four NON_EL boxes totaling 13.5 kg for order e481b1fd-73c2-5327-93fd-cb8985558407; status FLOOR_PACKING, revision 7. Migration, packing history and lifecycle tests: 27 passed.

## Outcome

- Signal: useful

## Source Nodes

- PackingPlanningService
- planning.py