"""Exact multilingual identity evidence; absence is not disagreement."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

LANGUAGES = ("th", "vi", "en")


def name_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip().casefold()


def code_key(value: str | None) -> str:
    # Separator equivalents only; never infer EL status or remove pack suffixes.
    return re.sub(r"[\s.-]", "", value or "").upper()


def names_of(value) -> dict[str, str]:
    return {
        language: str((value.get(f"name_{language}") if isinstance(value, dict)
                       else getattr(value, f"name_{language}", "")) or "").strip()
        for language in LANGUAGES
    }


@dataclass
class IdentityMatch:
    product: object | None = None
    status: str = "UNRESOLVED"
    matched_languages: list[str] = field(default_factory=list)
    missing_languages: list[str] = field(default_factory=list)
    conflicting_languages: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    code_matched: bool = False

    def evidence(self):
        return {
            "status": self.status,
            "matched_languages": self.matched_languages,
            "missing_languages": self.missing_languages,
            "conflicting_languages": self.conflicting_languages,
            "candidate_ids": self.candidate_ids,
            "code_matched": self.code_matched,
        }


class ProductIdentityIndex:
    """A code or unique exact name selects candidates; all comparable names verify them."""

    def __init__(self, products):
        self.products = {p.id: p for p in products}
        self.names = {language: defaultdict(set) for language in LANGUAGES}
        self.codes = defaultdict(set)
        for product in self.products.values():
            for language, name in names_of(product).items():
                if name_key(name):
                    self.names[language][name_key(name)].add(product.id)
            for attr in ("t_code", "numeric_code", "barcode", "norway_code"):
                key = code_key(getattr(product, attr, None))
                if key:
                    self.codes[key].add(product.id)

    def match(self, source: dict, *, raw_code: str | None = None) -> IdentityMatch:
        names = names_of(source)
        hits = {language: self.names[language].get(name_key(name), set())
                for language, name in names.items() if name_key(name)}
        code_hits = self.codes.get(code_key(raw_code), set())
        candidates = set(code_hits)
        for ids in hits.values():
            candidates.update(ids)
        result = IdentityMatch(candidate_ids=sorted(candidates))
        evidence_sets = [ids for ids in hits.values() if ids]
        if code_hits:
            evidence_sets.append(code_hits)
        agreed = set.intersection(*evidence_sets) if evidence_sets else set()
        # Shared names may be disambiguated by the other languages, never by language order.
        if len(agreed) != 1:
            result.status = "CONFLICT" if candidates else "UNRESOLVED"
            result.missing_languages = [language for language, name in names.items() if not name_key(name)]
            return result
        product = self.products[next(iter(agreed))]
        target_names = names_of(product)
        for language in LANGUAGES:
            source_key, target_key = name_key(names[language]), name_key(target_names[language])
            if not source_key or not target_key:
                result.missing_languages.append(language)
            elif source_key == target_key:
                result.matched_languages.append(language)
            else:
                result.conflicting_languages.append(language)
        result.code_matched = product.id in code_hits
        if result.conflicting_languages:
            result.status = "CONFLICT"
        else:
            result.product, result.status = product, "MATCHED"
        return result
