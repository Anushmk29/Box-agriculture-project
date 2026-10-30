import difflib
import re

from sqlalchemy.orm import Session, joinedload

from app.models.master_data import ClientSkuMapping, ControlStatus, Product


class NameFirstMatcher:
    def __init__(self, db: Session):
        self.db = db
        self._reviewed_products = {}
        self.products = db.query(Product).filter(Product.is_active.is_(True)).all()
        # Precompute lookup tables
        self.exact_vi = {}
        self.exact_en = {}
        self.exact_th = {}
        self.exact_code = {}
        for p in self.products:
            if p.name_vi:
                self.exact_vi[p.name_vi.strip().lower()] = p
            if p.name_en:
                self.exact_en[p.name_en.strip().lower()] = p
            if p.name_th:
                self.exact_th[p.name_th.strip().lower()] = p
            if p.t_code:
                self.exact_code[p.t_code.strip().upper()] = p
            if p.numeric_code:
                self.exact_code[p.numeric_code.strip()] = p
            if p.barcode:
                self.exact_code[p.barcode.strip()] = p
            if p.norway_code:
                self.exact_code[p.norway_code.strip().upper()] = p
            # Support EL-prefixed lookup e.g. EL010 for 11-010
            is_el = (
                (p.control_status == ControlStatus.EL)
                if hasattr(p, "control_status") and p.control_status
                else False
            )
            if is_el and p.numeric_code:
                clean_num = (
                    p.numeric_code.replace("11", "", 1)
                    if p.numeric_code.startswith("11")
                    else p.numeric_code
                )
                self.exact_code[f"EL{clean_num}".upper()] = p
                self.exact_code[f"EL-{clean_num}".upper()] = p

            # Support 4-digit shorthand for 5-digit code e.g. 1109 for 11-009 (11009)
            if p.numeric_code and len(p.numeric_code) == 5 and p.numeric_code[2] == "0":
                shorthand = p.numeric_code[:2] + p.numeric_code[3:]
                self.exact_code[shorthand.upper()] = p

        # Load pre-fed standardized client SKU mappings
        self.client_sku_by_code = {}  # (main_cust_id or 'GLOBAL', code) -> Product
        self.client_sku_by_name = {}  # (main_cust_id or 'GLOBAL', name) -> Product
        all_mappings = (
            db.query(ClientSkuMapping).options(joinedload(ClientSkuMapping.product))
            .filter(ClientSkuMapping.is_verified.is_(True)).all()
        )
        for m in all_mappings:
            if not m.product or not m.product.is_active:
                continue
            cust_key = m.main_customer_id or "GLOBAL"
            if m.client_sku_code:
                self.client_sku_by_code[(cust_key, m.client_sku_code.strip().upper())] = m.product
            if m.client_raw_name:
                self.client_sku_by_name[(cust_key, m.client_raw_name.strip().lower())] = m.product

        # Trade alias dictionary
        self.aliases = {
            "water spinach": "morning glory",
            "rau muống": "morning glory",
            "ผักบุ้ง": "morning glory",
            "cha om": "acacia leaf",
            "chà ôm": "acacia leaf",
            "ชะอม": "acacia leaf",
            "kaempfer": "krachai",
            "krachai": "krachai",
            "củ ngải": "krachai",
            "white round eggplant": "cà sọc",
            "small green eggplant": "cà nút áo",
            "thai banana": "gối chuối",
            "cải ngọt": "chinese cabbage",
            "pak kwan tung": "chinese cabbage",
            "kwan tung": "chinese cabbage",
            # --- French Produce Names (ALINDAR / Paris Store) ---
            "pousse de bamboo": "bamboo shoot",
            "pousse de bamboo frais": "bamboo shoot",
            "feuille tito": "tito leaf",
            "feuille diep": "fish mint",
            "piment vert": "green chilli",
            "bong he": "chives flower",
            "ngo gai": "culantro",
            "galanga": "young galangal",
            "curcuma": "yellow turmeric",
            "gian dai": "krachai",
            "aubergine blanche": "white round eggplant",
            "petit aubergine": "pea eggplant",
            "feuille parfumee": "pandan leave",
            "feuille de betel": "betel leave",
            "tamarin vert": "young sour tamarind",
            "feuille de banane": "banana leave",
            "ciboulette": "spring onion",
            "papaye": "green papaya",
            "feuille yanan": "yanang leave",
            "feuille saom": "acacia leaf",
            "feuille ylot": "chaplu leaf",
            "liseron d'eau": "morning glory",
            "epis de mais": "baby corn",
            "feuille he": "chinese leek leave",
            "piment rouge": "red chilli",
            "tigre de lotus": "lotus stem",
            "tigre ma-uk": "kayang leaf",
            "herbe rao ram": "pak prawe",
            "fleur dok sa-no": "sesbania flower",

            # --- Norwegian & Nordic Variations (NGA) ---
            "grønn papaya": "green papaya",
            "sitron gress": "lemongrass",
            "thaigress": "lemongrass",

            # --- Colloquial Thai Street Food & Chat Produce ---
            "ปลาระเบิด": "look chin pla",
            "ลูกชิ้นปลาระเบิด": "look chin pla",
            "ไส้กรอกแหลม": "kai sausage",
            "ไส้กรอกแหลมทอง": "kai sausage",
            "โกเด้งหมู": "look chin moo",
            "โกเด้งเนื้อ": "look chin neur",
            "มะพร้าวเจีย": "young coconut",
            "มะพร้าว ทึมทึก": "mature coconut",
            "มะพร้าวทึนทึก": "mature coconut",
            "มะพร้าวอ่อน": "young coconut",
            "ลอดช่อง": "short vermicelli",
            "ลอดช่องไทย": "short vermicelli",
            "หมี่เหลืองไข่": "egg noodles",
            "น้อยหน่า": "sugar apple",
            "ลูกเนียงแกะเปลือก": "luk niang",
            "ลูกเนียง": "luk niang",
            "กระเทียมไทย": "garlic",
            "กระชายซอย": "krachai",
            "ใบมะกรูดสด": "leech lime leaf",
            "ข่าอ่อน": "young galangal",
            "ผักบุ้งจีน": "morning glory",
            "ตะไคร้": "lemongrass",
        }

    def resolve_product(
        self,
        raw_name: str,
        raw_code: str | None = None,
        language_hint: str | None = None,
        main_customer_id: str | None = None,
    ) -> tuple[Product | None, str, float]:
        """
        6-Tier SKU Standardization Hierarchy
        Tier 0: Pre-Fed Standardized Client SKU Database (Client-specific & Global)
        Tier 1: Exact Master Product Match (VI, EN, TH)
        Tier 2: Trade Alias Match
        Tier 3: Fuzzy Similarity Match (>= 85%)
        Tier 4: Product Code Fallback
        Tier 5: Unresolved
        Returns: (Product, match_tier, confidence_score)
        """
        cust_key = main_customer_id or "GLOBAL"

        # Tier 0: Check Pre-fed Client SKU Database (eliminates typos and duplicate entries)
        if raw_code and raw_code.strip():
            clean_code = raw_code.strip().upper()
            if (cust_key, clean_code) in self.client_sku_by_code:
                return self.client_sku_by_code[(cust_key, clean_code)], "PRE_FED_CLIENT_SKU", 1.0
            if ("GLOBAL", clean_code) in self.client_sku_by_code:
                return self.client_sku_by_code[("GLOBAL", clean_code)], "PRE_FED_CLIENT_SKU", 1.0

        clean = raw_name.strip().lower() if raw_name else ""
        if clean:
            if (cust_key, clean) in self.client_sku_by_name:
                return self.client_sku_by_name[(cust_key, clean)], "PRE_FED_CLIENT_SKU", 1.0
            if ("GLOBAL", clean) in self.client_sku_by_name:
                return self.client_sku_by_name[("GLOBAL", clean)], "PRE_FED_CLIENT_SKU", 1.0

        if not raw_name:
            return self._resolve_code(raw_code)

        # Tier 1: Exact Match (VI, EN, TH)
        if clean in self.exact_vi:
            return self.exact_vi[clean], "NAME_EXACT_VI", 1.0
        if clean in self.exact_en:
            return self.exact_en[clean], "NAME_EXACT_EN", 1.0
        if clean in self.exact_th:
            return self.exact_th[clean], "NAME_EXACT_TH", 1.0

        # Tier 2: Trade Alias Match
        alias_target = self.aliases.get(clean)
        if alias_target:
            if alias_target in self.exact_en:
                return self.exact_en[alias_target], "NAME_ALIAS", 0.95
            if alias_target in self.exact_vi:
                return self.exact_vi[alias_target], "NAME_ALIAS", 0.95

        # Tier 3: Fuzzy Similarity Match (>= 85%)
        # Check against English names
        all_en_names = list(self.exact_en.keys())
        close_en = difflib.get_close_matches(clean, all_en_names, n=1, cutoff=0.85)
        if close_en:
            ratio = difflib.SequenceMatcher(None, clean, close_en[0]).ratio()
            return self.exact_en[close_en[0]], "NAME_FUZZY", round(ratio, 2)

        # Check against Vietnamese names
        all_vi_names = list(self.exact_vi.keys())
        close_vi = difflib.get_close_matches(clean, all_vi_names, n=1, cutoff=0.85)
        if close_vi:
            ratio = difflib.SequenceMatcher(None, clean, close_vi[0]).ratio()
            return self.exact_vi[close_vi[0]], "NAME_FUZZY", round(ratio, 2)

        # Tier 4: Code Fallback (Only if name unmatched)
        return self._resolve_code(raw_code)

    def _resolve_code(self, raw_code: str | None) -> tuple[Product | None, str, float]:
        """Exact code, then separator-stripped ('11-001' -> '11001'), then base ('11001-080')."""
        code = (raw_code or "").strip().upper()
        for candidate in (code, re.sub(r"[^\w]", "", code), code.split("-")[0]):
            if candidate and candidate in self.exact_code:
                return self.exact_code[candidate], "CODE_FALLBACK", 0.75
        return None, "UNRESOLVED", 0.0

    def reviewed_product(self, names: list[str | None], *, raw_code: str | None = None,
                         main_customer_id: str | None = None) -> Product | None:
        """Only exact names/codes or staff-verified mappings may enter purchasing."""
        key = (tuple(names), raw_code, main_customer_id)
        if key in self._reviewed_products:
            return self._reviewed_products[key]
        reviewed = None
        for name in [*names, ""]:
            product, tier, _ = self.resolve_product(name or "", raw_code=raw_code, main_customer_id=main_customer_id)
            if tier == "PRE_FED_CLIENT_SKU" or tier.startswith("NAME_EXACT_"):
                reviewed = product
                break
            if tier == "CODE_FALLBACK" and (raw_code or "").strip().upper() in self.exact_code:
                reviewed = product
                break
        self._reviewed_products[key] = reviewed
        return reviewed

    def suggestions(self, names: list[str | None], code: str | None = None) -> list[dict]:
        terms = [n.strip().casefold() for n in names if n and n.strip()]
        if code:
            terms.append(code.strip().casefold())
        ranked = []
        for product in self.products:
            candidates = [product.name_en, product.name_vi, product.name_th, product.t_code, product.numeric_code]
            score = max((difflib.SequenceMatcher(None, term, candidate.casefold()).ratio()
                         for term in terms for candidate in candidates if candidate), default=0)
            if score >= 0.35:
                ranked.append((score, product))
        return [{"id": p.id, "t_code": p.t_code, "name_en": p.name_en,
                 "name_vi": p.name_vi, "score": round(score, 3)}
                for score, p in sorted(ranked, key=lambda row: (-row[0], row[1].t_code))[:5]]
