---
type: "query"
date: "2026-11-01T17:37:33.316117+00:00"
question: "How should duplicate Riềng trắng Galanga white catalog matches be handled?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Product", "merge_products()"]
---

# Q: How should duplicate Riềng trắng Galanga white catalog matches be handled?

## Answer

Expanded graph vocabulary: product matching duplicate merge identity. Graph located Product at backend/app/models/master_data.py:L62 and merge_products at backend/app/modules/orders/reconciliation.py:L11. Source inspection confirms merge retires the source and preserves aliases and confirmed history. A separate read-only database comparison found 11-002 has the same Vietnamese and Thai names as T082-1, with 100g tray versus 200g tray defaults; T082-2 has a malformed Thai name containing 500g tray and no default pack sizes. Recommend 11-002 for the current 100g tray item and review consolidation with pack sizes retained separately. No product data changed.

## Outcome

- Signal: useful

## Source Nodes

- Product
- merge_products()