---
type: "query"
date: "2026-11-02T08:03:09.485110+00:00"
question: "How many Excel files per customer have we supplied so the system can learn their structures?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["ExcelTemplate", "excel_profiles.py"]
---

# Q: How many Excel files per customer have we supplied so the system can learn their structures?

## Answer

Vocabulary: excel template samples customer. Graph located ExcelTemplate and excel_profiles. Current filesystem hash inventory verified 35 supplied order workbooks, 32 unique contents, excluding 11 packing/catalogue/blank-template files. Counts supplied/unique/configured reference samples: NGA 16/15/13; TONIO 9/7/1; LY-THANH 4/4/4; CHAOTHAI 2/2/2; HUNGFAT 1/1/1; P-PUI 1/1/1; PINTO-THAI 1/1/1; YACHI 1/1/1. Configured references total 24 from backend/app/assets/excel_templates.json. Read-only live database has four Excel uploads: Tonio 1, P-PUI 1, LY 2, with one Hungfat file assigned to LY. Saved template rows are automatically seeded copies of all 24 reference definitions for LY and Tonio, not 48 independently learned formats. Runtime uses saved layout mappings rather than automatic model training.

## Outcome

- Signal: useful

## Source Nodes

- ExcelTemplate
- excel_profiles.py