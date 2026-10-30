"""Build an explicit SKU-to-web-evidence proposal; never infer identity from row order.

Run from the repository root after exporting products to
outputs/product-volume-research/catalogue-before.json. This script only writes local
research artifacts. Applying a proposal is a separate, guarded transaction.
"""

import csv
import hashlib
import json
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/product-volume-research"
DATE = "2026-10-28"
VERSION = "2026-10-28-name-checked-v1"
D = Decimal
BUSHEL_L = D("35.23907016688")
LB_KG = D("0.45359237")

SOURCES = {
    "fao": {
        "title": "FAO/INFOODS Density Database v2 (2012)",
        "url": "https://www.fao.org/fileadmin/templates/food_composition/documents/density_DB_v2_0_01.pdf",
        "caveat": "Food-form density; whole/cut preparation and export packaging are not specified for every entry. Not a finished-pack measurement.",
    },
    "uga": {
        "title": "University of Georgia Extension C780, Tables 2-3",
        "url": "https://extension.uga.edu/publications/detail.html?number=C780",
        "caveat": "Nominal US retail container volume/net weight; cultivar, grading and retail packaging differ. Dry volume units are used for produce containers.",
    },
    "msu": {
        "title": "Michigan State University E1565, Table 2",
        "url": "https://msu-prod.dotcmscloud.com/resources/yields_of_michigan_vegetable_crops_e1565",
        "caveat": "Nominal US commercial container volume/net weight; not a measurement of the Thai SKU or its finished retail pack.",
    },
    "iima": {
        "title": "Sharan and Rawale, Physical Characteristics of Some Vegetables Grown in Ahmedabad Region",
        "url": "https://www.iima.ac.in/sites/default/files/rnpfiles/2003-12-01GirjaSharan.pdf",
        "caveat": "Farm-fresh Indian cultivars and grades measured in a rigid container. Thai cultivar and packaging require local checking.",
    },
    "ginger": {
        "title": "Jayashree and Visvanathan, Physical and biochemical parameters of fresh and dry ginger",
        "url": "https://updatepublishing.com/journal/index.php/josac/article/view/5142",
        "caveat": "Fresh whole Zingiber officinale cv. Himachal only; excludes galangal, fingerroot, shoots, leaves and sliced ginger.",
    },
    "herbs": {
        "title": "Scientific Reports 13:4434 (2023), Table 1",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10023810/",
        "caveat": "Fresh separated leaves, not stems/roots or a retail bunch. Basil is Ocimum basilicum; lemongrass is Cymbopogon flexuosus. Other species cannot be silently substituted.",
    },
    "oyster": {
        "title": "Effect of dehydration on physical properties of oyster mushroom, IJAE 1(2), 90-92",
        "url": "https://researchjournal.co.in/online/IJAE/IJAE%201(2)/1_A-90-92.pdf",
        "caveat": "Initial fresh Pleurotus sajor-caju bulk-density range; other oyster species remain a species proxy, not a direct match. Excludes dried and boiled mushrooms.",
    },
    "straw": {
        "title": "Development of a packaging, storage and transportation cabinet for paddy straw mushroom, Table 1",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC8076411/",
        "caveat": "Fresh Volvariella volvacea only. Container/packaging geometry requires local checking.",
    },
    "tropical": {
        "title": "Athmaselvi et al., Physical and biochemical properties of selected tropical fruits (2014), Table 1",
        "url": "https://www.international-agrophysics.org/pdf-104086-35155?filename=Physical+and+biochemical.pdf",
        "caveat": "Ripe fresh whole guava, sapota and papaya; study cultivars/grade differ. Rounded Table 1 values differ slightly from running text; this proposal consistently uses the table.",
    },
    "mango_raw": {
        "title": "Karate et al., Physical and engineering properties of raw mango fruit (2023)",
        "url": "https://zenodo.org/records/8190345",
        "caveat": "Whole raw wild-variety Mangifera indica from Maharashtra. Thai cultivars are commodity comparators; do not apply to ripe mango, leaves, trees or plum mango.",
    },
    "longan": {
        "title": "Effects of operating practices on performance of a fixed-bed convection dryer and quality of dried longan, Materials and methods",
        "url": "https://academic.oup.com/ijfst/article/43/11/1979/7864513",
        "caveat": "INITIAL fresh unpeeled Daw longan, 20-22 mm, before drying: 587 kg/m3. Branches, baskets and retail packaging are additional unmeasured variables.",
    },
    "mangosteen": {
        "title": "Hidayat et al., Studies on geometrical, physical, mechanical and colour properties of mangosteen fruits (2020)",
        "url": "https://doi.org/10.1088/1755-1315/462/1/012045",
        "access_url": "https://www.researchgate.net/publication/340740594_Studies_on_geometrical_physical_mechanical_and_colour_properties_of_mangosteen_fruits",
        "caveat": "Whole mangosteen mean bulk density 0.61 g/cm3, SD 0.02; SD is not a measured min/max. Wrapping and box grading differ. Publisher access was blocked; paper text was checked in its author-posted copy.",
    },
    "garlic": {
        "title": "Mengistu et al., Physical Properties of Garlic Bulbs and Cloves: Effect of Variety and Location (2024), Table 3",
        "url": "https://ajbs.scione.com/cms/fulltext.php?id=988",
        "caveat": "Whole fresh garlic bulbs: variety means 0.28-0.40 g/cm3. Excludes peeled cloves, powder, leaves and shoots; Thai cultivar not specified.",
    },
    "lime": {
        "title": "Pawar et al., Physical and mechanical properties of Citrus aurantiifolia var. Pramalini (2015)",
        "url": "https://bioinfopublication.org/pages/article.php?id=BIA0002438",
        "caveat": "Fresh whole Citrus aurantiifolia cultivar Pramalini; Thai lime is a commodity comparator. Excludes kaffir lime, lime leaves and frozen fruit.",
    },
    "turmeric": {
        "title": "The Pharma Innovation Journal 2022;11(9):2587-2590, section 3.4",
        "url": "https://www.thepharmajournal.com/archives/2022/vol11issue9/PartAF/11-9-229-496.pdf",
        "caveat": "Fresh whole Curcuma longa rhizomes; observed range 418-425 kg/m3. Excludes white curcuma, leaves, chips, powder and other rhizome species. Table/section checked in indexed publisher text; direct retrieval blocked.",
    },
    "banana": {
        "title": "Wasala et al., Physical and Mechanical Properties of Three Commercially Grown Banana Cultivars in Sri Lanka (2012), Tables 5-7",
        "url": "https://doi.org/10.4038/tar.v24i1.7988",
        "access_url": "https://pdfs.semanticscholar.org/d9c6/c51a65484adda5effbb47a05f86048d9522b.pdf",
        "caveat": "Mature-green Seeni/Embul/Kolikuttu bananas. Whole bunches, hands and separate fruit have distinct densities; Thai cultivar and ripeness unmeasured. Paper checked in a full-text mirror.",
    },
    "pomegranate": {
        "title": "Jithender et al., Determination of Physical and Mechanical Properties of Pomegranate Fruit and Aril (2017), Table 1",
        "url": "https://www.ijcmas.com/abstractview.php?ID=4539&SNo=105&vol=6-10-2017",
        "caveat": "Whole Bhagwa pomegranate fruit, observed six-trial range 620.37-692.12 kg/m3. Aril density is separate and is not used.",
    },
    "lychee": {
        "title": "ICAR Indian Horticulture May-June 2022, Litchi grading and packaging table",
        "url": "https://icar.org.in/sites/default/files/inline-files/IH-May-June-2022-2.pdf",
        "caveat": "Recommended litchi CFB-box dimensions/capacity; derives a whole-carton comparator that includes walls and free space. Not a measured Thai retail pack.",
    },
    "taro": {
        "title": "Optimization of machine parameters for the peeling of taro mother corms, revised research manuscript, section 2.2",
        "url": "https://sdiopr.s3.ap-south-1.amazonaws.com/2023/July/07-jul-23/2023_IJPSS_102807/Revised-ms_IJPSS_102807_v1.pdf",
        "caveat": "Fresh whole Muktakeshi mother corms, initial bulk density 516 kg/m3; excludes flour, leaves, stalks, peeled and carved products. Revised manuscript checked in indexed primary text.",
    },
}

REFERENCES = {}
MAPPINGS = {}


def density(key, commodity, source, low, high=None, locator="", **extra):
    REFERENCES[key] = {
        "commodity": commodity, "source_id": source, "locator": locator,
        "density_low_kg_per_l": str(low), "density_high_kg_per_l": str(high or low),
        **extra,
    }


def bushel(key, commodity, source, pounds_low, pounds_high=None, bushels="1", locator=""):
    volume = BUSHEL_L * D(bushels)
    density(key, commodity, source, D(pounds_low) * LB_KG / volume,
            D(pounds_high or pounds_low) * LB_KG / volume, locator,
            raw_net_lb_low=str(pounds_low), raw_net_lb_high=str(pounds_high or pounds_low),
            raw_container_bushels=bushels, raw_container_l=str(volume))


def assign(reference, codes, note="", match="SAME_COMMODITY_FORM"):
    for code in codes.split():
        if code in MAPPINGS:
            raise ValueError(f"Duplicate explicit assignment: {code}")
        MAPPINGS[code] = {"reference_id": reference, "identity_note": note,
                          "match": match}


# Every number below was checked against the named source in this research pass.
density("chilli", "Fresh red/green chilli fruit", "fao", ".5", locator="printed p15 / PDF page18; Chili, red/green")
density("papaya_green", "Unripe raw papaya fruit", "fao", ".56", locator="printed p15 / PDF page18; Paw Paw, unripe")
density("sweet_potato", "Raw sweet potato tuber", "fao", ".44", locator="printed p14 / PDF page17")
density("cassava", "Raw cassava root", "fao", ".63", locator="printed p14 / PDF page17")
density("lettuce_leaves", "Raw loose green salad leaves", "fao", ".06", locator="printed p15 / PDF page18")
density("spinach", "Raw Spinacia oleracea leaves", "fao", ".08", locator="printed p16 / PDF page19")
density("ginger", "Fresh whole ginger rhizome", "ginger", ".47149", locator="Abstract; fresh rhizomes 471.49 kg/m3")
density("basil_leaves", "Fresh Ocimum basilicum leaves", "herbs", ".04863", locator="Table 1; 48.63 +/- 4.88 kg/m3; mean used")
density("coriander_leaves", "Fresh Coriandrum sativum leaves", "herbs", ".089", locator="Table 1; 89 +/- 3.66 kg/m3; mean used")
density("oyster", "Fresh Pleurotus sajor-caju mushroom", "oyster", ".218", ".232", locator="Abstract; initial fresh mushroom range")
density("straw", "Fresh paddy straw mushroom", "straw", ".401", locator="Table 1; average bulk density")
density("guava", "Ripe whole fresh guava", "tropical", ".55", locator="Table 1, PDF p3; mean 0.55 g/cm3")
density("sapodilla", "Ripe whole fresh sapota/sapodilla", "tropical", ".61", locator="Table 1, PDF p3; mean 0.61 g/cm3")
density("papaya_ripe", "Ripe whole fresh papaya", "tropical", ".44", locator="Table 1, PDF p3; mean 0.44 g/cm3")
density("mango_raw", "Raw whole mango fruit", "mango_raw", ".59577", locator="Abstract; 595.77 kg/m3")
density("longan", "Fresh unpeeled longan fruit", "longan", ".587", locator="Materials and methods; initial fresh fruit load")
density("mangosteen", "Whole fresh mangosteen fruit", "mangosteen", ".61", locator="Abstract and physical properties; mean 0.61 g/cm3")
density("garlic", "Fresh whole garlic bulbs", "garlic", ".28", ".40", locator="Table 3; BD column, variety means")
density("lime", "Whole fresh acid lime", "lime", ".55180", ".58583", locator="Abstract; observed fruit bulk-density range")
density("turmeric", "Fresh whole yellow turmeric rhizomes", "turmeric", ".418", ".425", locator="Section 3.4, observed range; excludes dry rhizomes")
density("bitter_gourd", "Fresh whole bitter gourd fruit", "iima", ".400", locator="PDF p10, bitter gourd section")
density("bottle_gourd", "Fresh whole bottle gourd fruit", "iima", ".565", locator="PDF p6 / Table 6")
density("cauliflower", "Fresh cauliflower curds", "iima", ".201", ".269", locator="PDF p6 / Table 5, grade 2 and grade 1")
density("onion", "Fresh whole onion bulbs", "iima", ".596", ".643", locator="PDF p9, onion section")
density("banana_bunch", "Fresh whole banana bunches with connecting stalk", "banana", ".15477", ".20107", locator="Table 5, PDF p8; cultivar means, not hands or fingers")
density("banana_unspecified", "Fresh banana fruit, form unspecified", "banana", ".33544", ".49445", locator="Tables 6-7, hands vs individual fingers; reference only pending form")
density("pomegranate", "Fresh whole pomegranate fruit", "pomegranate", ".62037", ".69212", locator="Table 1, whole-fruit trial range")
density("lychee", "Fresh lychee fruit in recommended carton", "lychee", D("2") / D("7.48"), D("10") / D("20.727"), locator="CFB box table: 340x220x100 mm/2kg; 340x220x190 mm/4kg; 420x235x210 mm/8-10kg")
density("taro", "Fresh whole taro mother corms", "taro", ".516", locator="Section 2.2; raw-corm bulk density")
bushel("eggplant", "Whole fresh Solanum melongena eggplant", "uga", "33", "35", locator="Table 3, Eggplants")
bushel("cucumber", "Whole fresh cucumber", "uga", "48", "50", locator="Table 3, Cucumbers")
bushel("okra", "Whole fresh okra pods", "uga", "26", "30", locator="Table 3, Okra, tall bushel hamper")
bushel("asparagus", "Fresh asparagus spears", "uga", "24", locator="Table 3, Asparagus, bushel")
bushel("peas_pod", "Unshelled fresh garden peas", "uga", "28", "30", locator="Table 3, English green unshelled peas")
bushel("peas_edible", "Fresh edible-pod peas", "uga", "8", "10", bushels=".25", locator="Table 3, Peas, Edible Pod, peck")
bushel("zucchini", "Whole fresh summer squash/zucchini", "uga", "40", "44", locator="Table 3, Squash, Summer")
bushel("turnip", "Fresh whole turnip roots without tops", "uga", "50", "56", locator="Table 3, Turnips without tops, bushel")
bushel("grape", "Fresh whole grapes with stems", "uga", "44", "50", locator="Table 2, Grapes with stems, bushel")
bushel("peach", "Fresh whole peach fruit", "uga", "48", "52", locator="Table 2, Peaches, bushel")
bushel("plum", "Fresh whole Prunus plum fruit", "uga", "50", "56", locator="Table 2, Plums, bushel")
bushel("mustard", "Fresh mustard greens", "uga", "18", "20", locator="Table 3, Greens; includes mustard")
bushel("cabbage", "Fresh whole round cabbage heads", "msu", "50", "60", bushels="1.75", locator="Table 2, Cabbage, 1 3/4-bushel crate")
bushel("lettuce_head", "Fresh whole head lettuce", "msu", "45", "50", bushels="1.75", locator="Table 2, Lettuce, head, 1 3/4-bushel carton")
bushel("cantaloupe", "Fresh whole muskmelon/cantaloupe", "msu", "60", "70", locator="Table 2, Muskmelon, bushel basket")
bushel("strawberry", "Fresh strawberry fruit", "msu", "24", bushels=".5", locator="Table 2, Strawberry, 16-dry-quart crate")

# Explicit codes, checked with both the English and Thai identity in the current
# catalogue. No substring, prefix, row-number or fuzzy joins are used on apply.
assign("chilli", "11-022 11-023 11-024 11-025 11-026 11-027 11-179 11-188 16-045 16-060 16-069 T118 T119")
assign("papaya_green", "11-028 1208 T017 T017-1")
assign("sweet_potato", "12-121 15-110", "Named colour/Japanese cultivars are same-commodity comparators, not cultivar measurements.")
assign("sweet_potato", "11-186", "Purple sweet-potato/taro/purple-yam naming is not a resolved botanical identity; check the supplied tuber first.", "IDENTITY_CONFLICT_NOT_APPLIED")
assign("sweet_potato", "16-037 16-038", "English says sweet potato, but Thai says purple/yellow sweet-leaf vegetable (phak wan). Resolve the identity first.", "IDENTITY_CONFLICT_NOT_APPLIED")
assign("cassava", "11-247")
assign("spinach", "11-143", "Puay leng is spinach; excludes Chinese amaranth and Malabar spinach.")
assign("ginger", "11-043 11-044 T083", "Maturity/cultivar unmeasured; whole rhizome comparator only.")
assign("basil_leaves", "11-005 11-561 T024-1", "Sweet basil only; retail stems/bunching unmeasured.", "SAME_SPECIES_LEAF_COMPARATOR")
assign("coriander_leaves", "11-036 11-453 11-455 11-456 16-044 EL-453", "Leaves comparator; retail stems/bunching unmeasured. Excludes culantro and coriander roots.", "SAME_SPECIES_LEAF_COMPARATOR")
assign("straw", "11-110 T347")
assign("oyster", "11-112 T349", "Thai nang-fah mushroom is an oyster species comparator; exact species needs supplier confirmation.", "SPECIES_PROXY_NOT_APPLIED")
assign("guava", "22-209 22-269 22-270 22-291 22-442 22-443 22-557 22-586 22-592 T305", "Whole fruit comparator; study used ripe fruit. Thai firm export maturity/cultivar and box packaging need checking.", "MATURITY_COMPARATOR_NOT_APPLIED")
assign("sapodilla", "22-222")
assign("papaya_ripe", "22-211 22-401 22-401-662")
assign("mango_raw", "22-201 22-202 22-235 22-245 22-251 22-268 22-271 22-278 22-411 22-452 22-563 T056 T056-1 T344", "Whole raw mango comparator; cultivar-specific geometry is unmeasured.")
assign("longan", "22-213 22-260 22-587 22-589", "Fruit bulk only; branches/basket are not specified in this catalogue.")
assign("mangosteen", "22-205 22-581 22-590 T033-1 T302")
assign("garlic", "11-035 11-169 16-035", "Whole garlic bulbs; if supplied as loose or peeled cloves use a different measurement.")
assign("garlic", "16-159", "Thai explicitly says peeled Thai garlic 500g; whole-bulb density cannot be used.", "FORM_MISMATCH_NOT_APPLIED")
assign("lime", "11-061", "Thai cultivar unmeasured; acid lime comparator. Never apply to kaffir lime or leaves.")
assign("turmeric", "11-083 11-556 T081-1", "Yellow Curcuma longa only. White curcuma is a different commodity.")
assign("bitter_gourd", "11-016 11-075 16-058", "Whole fruit only, excluding bitter-gourd shoots/leaves.")
assign("bitter_gourd", "12-113", "Sweet bitter melon name may denote chayote; resolve species before using Momordica charantia.", "IDENTITY_CONFLICT_NOT_APPLIED")
assign("bottle_gourd", "11-177")
assign("cauliflower", "11-192", "Curds only; excludes Brussels sprouts, cabbage side shoots and broccoli.")
assign("onion", "11-398", "Whole onion bulbs; excludes shallot and spring-onion leaves.")
assign("eggplant", "11-013 11-089 11-090 11-092 11-093 11-094 11-097 11-401 11-458 14-013 16-061 16-067 16-071 16-134 16-135 16-147 16-147-511 EL-450 EL-452 T007 T007-1 T008 T008-1 T009 T066-1 T067 T067-2 T108 T284", "Solanum melongena commodity comparator; Thai cultivar/size and bare vs bagged packaging need checking. Excludes pea eggplant and ma-uek.")
assign("cucumber", "11-078")
assign("okra", "11-053")
assign("okra", "T142", "Thai short name krachiap does not distinguish okra from roselle without supplier confirmation.", "IDENTITY_CONFLICT_NOT_APPLIED")
assign("asparagus", "11-232 T175", "Grade A/B and spear length require local checking.")
assign("peas_pod", "11-085", "Confirm English garden peas vs edible-pod snow peas before selecting the density.", "FORM_UNCONFIRMED_NOT_APPLIED")
assign("peas_edible", "T086", "Sweet-pea name needs edible-pod confirmation.", "FORM_UNCONFIRMED_NOT_APPLIED")
assign("zucchini", "11-357")
assign("zucchini", "16-116", "English says zucchini but Thai says young luffa (ma noi); resolve identity.", "IDENTITY_CONFLICT_NOT_APPLIED")
assign("turnip", "11-195", "Thai says daikon/radish, not turnip. The old salad-leaf value is also unsuitable; resolve identity/source.", "IDENTITY_CONFLICT_NOT_APPLIED")
assign("grape", "22-309 22-417", "Whole fresh grapes on stems; cultivar and protective packing unmeasured.")
assign("peach", "22-314")
assign("plum", "22-312 22-313", "Prunus plums only; excludes plum mango, hog plum and jujube.")
assign("mustard", "11-127 11-194 11-569", "Whole mustard greens commodity comparator.")
assign("cabbage", "11-098", "Round cabbage; not napa cabbage, choy sum or bok choi.")
assign("lettuce_head", "11-196", "Iceberg/head lettuce; loose-leaf and romaine heads differ.")
assign("cantaloupe", "22-256", "Thai melon cultivar differs from standard muskmelon; compare geometry locally.", "CULTIVAR_COMPARATOR_NOT_APPLIED")
assign("cantaloupe", "22-419", "Thai explicitly says carved cantaloupe. Whole-fruit volume does not establish the carved product.", "FORM_MISMATCH_NOT_APPLIED")
assign("banana_bunch", "22-264 22-446", "Thai explicitly identifies an entire banana bunch (khruea); not a hand/finger value. Thai cultivar/ripeness still require checking.")
assign("banana_unspecified", "22-210 22-218 22-240 22-243 22-262 22-534", "Specify whole bunch, hands or fingers and maturity before choosing a banana volume.", "FORM_UNCONFIRMED_NOT_APPLIED")
assign("pomegranate", "22-257")
assign("lychee", "22-212", "Whole-fruit commercial carton comparator, not the measured occupied volume of a Thai retail bag.")
assign("taro", "11-031", "Fresh whole taro corm comparator; excludes leafy plant, peeled/cut/carved taro.")
assign("taro", "22-421", "Thai name specifies a carved taro product; raw whole-corm density cannot establish its final form.", "FORM_MISMATCH_NOT_APPLIED")
assign("strawberry", "22-263")

# Confirmed catalogue/source-form mismatches. Withdrawing an unreviewed wrong
# estimate is distinct from guessing its replacement. Prior values are backed up.
WITHDRAW = {
    "11-133": "Napa cabbage had the fresh-ginger ratio (2.12095 L/kg); rhizome evidence does not establish cabbage volume.",
    "11-134": "Leucaena flower had a ginger-rhizome ratio; wrong commodity and form.",
    "11-135": "Sliced ginger had the loose-salad-leaf ratio; neither whole-rhizome nor lettuce evidence validates sliced ginger.",
    "11-136": "Fresh raw peanut had spring-onion-leaf ratio; wrong commodity.",
    "11-148": "Ambiguous cabbage side shoot/Brussels sprout identity had broccoli ratio; identity and form must be resolved.",
    "11-171": "Bamboo shoot leaf had ginger-rhizome ratio; wrong commodity/form.",
    "11-198": "Cherry tomato had loose-salad-leaf ratio; fruit is not a leaf.",
    "11-212": "Cherry tomato had broccoli ratio; wrong commodity.",
    "11-244": "Mixed baby corn/asparagus/pea had spring-onion-leaf ratio; mixture weights and pack measurement required.",
    "11-256": "Young Thai melon had eggplant ratio and conflicting Vietnamese name; identity must be resolved.",
    "11-303": "Lotus root had loose-salad-leaf ratio; wrong plant part.",
    "11-358": "Old galangal had oyster-mushroom ratio; wrong commodity.",
}


def quantize(value):
    return str(value.quantize(D(".000001"), rounding=ROUND_HALF_UP))


def missing_reason(product):
    name = " ".join(str(product.get(k) or "") for k in ("name_en", "name_th")).lower()
    if any(x in name for x in ("+", " set", "mixed", "mix ", "ชุด")):
        return "Mixture/composite pack: need component net-weight shares and a finished-pack volume; do not average densities by product count."
    if any(x in name for x in ("frozen", "boiled", "steamed", "griled", "pickled", "sliced", "slice", "ต้ม", "แช่แข็ง", "ดอง", "หั่น")):
        return "Processed/sliced/frozen form: a raw whole-commodity density does not establish this form's occupied volume."
    if any(x in name for x in ("peeled", "แกะ", "ปลอก", "ปอก", "vacuum", "tray")):
        return "Peeled/trimmed/tray/vacuum form: need an appropriate same-form source or finished-pack measurement."
    if any(x in name for x in ("leaf", "leave", "shoot", "flower", "ใบ", "ยอด", "ดอก", "stem", "stalk")):
        return "No verified density for this species and plant part in its catalogue form. Generic lettuce/herb values are not identity evidence."
    if product["t_code"] in {"T142-1", "T261-1", "MISC-UNMATCHED", "16089", "11-255"}:
        return "Product identity is missing/generic or only a pack-size label; resolve the product before selecting a density."
    return "No suitable verified source for this exact commodity/form in this pass. Supplier specification or local pack measurement required."


def main():
    original_bytes = (OUTPUT / "catalogue-before.json").read_bytes()
    products = json.loads(original_bytes)
    by_code = {p["t_code"]: p for p in products}
    assert len(by_code) == len(products), "Duplicate SKUs in snapshot"
    assert set(MAPPINGS) <= set(by_code), sorted(set(MAPPINGS) - set(by_code))
    assert set(WITHDRAW) <= set(by_code)
    assert not set(WITHDRAW) & set(MAPPINGS)
    records = []
    for product in products:
        code = product["t_code"]
        mapping = MAPPINGS.get(code)
        prior = product["packing_volume_l_per_kg"]
        record = {k: product[k] for k in ("id", "t_code", "name_en", "name_th", "name_vi", "packing_revision")}
        record.update(previous_volume_l_per_kg=prior, proposed_volume_l_per_kg=None,
                      action="KEEP", reviewed=False, estimated=True)
        if mapping:
            ref = REFERENCES[mapping["reference_id"]]
            source = SOURCES[ref["source_id"]]
            lower = D(1) / D(ref["density_high_kg_per_l"])
            upper = D(1) / D(ref["density_low_kg_per_l"])
            record.update(mapping, source=source, reference=ref,
                          estimate_low_l_per_kg=quantize(lower), estimate_high_l_per_kg=quantize(upper),
                          method="1 / bulk density in kg/L; use maximum reported volume (minimum reported density); no extra void multiplier",
                          status="COMMODITY_ESTIMATE", reason=mapping["identity_note"] or source["caveat"])
            if mapping["match"].endswith("NOT_APPLIED"):
                record.update(status="REFERENCE_ONLY", reason=mapping["identity_note"])
            elif product["packing_volume_reviewed"]:
                record.update(status="PRESERVE_REVIEWED", reason="Keep the operator-reviewed value; web research cannot overwrite it.")
            elif "volume" in (product.get("packing_source") or {}).get("table", {}).get("manual_fields", []):
                record.update(status="PRESERVE_MANUAL", reason="Keep staff's manual volume; web research cannot overwrite it.")
            else:
                record.update(action="SET_ESTIMATE", proposed_volume_l_per_kg=quantize(upper))
        elif code in WITHDRAW and prior is not None and not product["packing_volume_reviewed"]:
            record.update(status="WITHDRAWN_MISMATCH", action="WITHDRAW_INVALID", reason=WITHDRAW[code])
        else:
            record.update(status="EXISTING_NOT_REVALIDATED" if prior is not None else "NEEDS_MEASUREMENT", reason=missing_reason(product))
        record["research_version"] = VERSION
        record["checked_date"] = DATE
        records.append(record)
    result = {"version": VERSION, "checked_date": DATE, "catalogue_sha256": hashlib.sha256(original_bytes).hexdigest(),
              "project_ref": "ayaquhxlbqryfwmjuesm", "unit": "litres per net kilogram",
              "measurement_claim": "Commodity comparators and estimates only; no finished SKU is certified or operator-approved by this research.",
              "summary": dict(Counter(r["status"] for r in records)), "sources": SOURCES, "references": REFERENCES, "products": records}
    (OUTPUT / "volume-proposal.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["t_code", "name_en", "name_th", "status", "action", "previous_volume_l_per_kg", "proposed_volume_l_per_kg", "reference_id", "source_url", "reason"]
    with (OUTPUT / "product-volumes-818.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({k: record.get(k, "") if k != "source_url" else record.get("source", {}).get("url", "") for k in fields})
    print(json.dumps({"products": len(records), "mappings": len(MAPPINGS), "references": len(REFERENCES), **result["summary"]}))
    for record in records:
        if record["action"] != "KEEP":
            print(record["t_code"], record["name_en"], record["name_th"], record["action"], record["proposed_volume_l_per_kg"], record.get("reference_id", ""))


if __name__ == "__main__":
    main()
