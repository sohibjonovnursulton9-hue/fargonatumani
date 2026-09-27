"""MFY names transcribed from the 58 user-provided one-page roster scans.

Only the MFY column is used. The source scans list social-service employees in
other columns; none of those personal details are copied here. This list is
marked provisional until the Hokimlik confirms it as the complete registry.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


MFY_SOURCE = "user_supplied_mfy_roster_scans"

MFY_NAMES_UZ = (
    "Avval MFY",
    "Bahor MFY",
    "Barqaror Hamjihatlik MFY",
    "Birdamlik MFY",
    "Boʻston MFY",
    "Boymahalla MFY",
    "Chimyon MFY",
    "Damkoʻl MFY",
    "Dehqonobod MFY",
    "Dilkusho MFY",
    "Doʻstlik MFY",
    "Fargʻona MFY",
    "Guliston MFY",
    "Gulpiyon MFY",
    "Gulshan MFY",
    "Guzar MFY",
    "Kaptarxona MFY",
    "Konchilar MFY",
    "Langar MFY",
    "Logʻon MFY",
    "Margʻilon MFY",
    "Markaz MFY",
    "Mashʼal MFY",
    "Maydon MFY",
    "Mehnatobod MFY",
    "Mindon MFY",
    "Mindonobod MFY",
    "Navroʻz MFY",
    "Obod MFY",
    "Oq oltin MFY",
    "Oqbilol MFY",
    "Oqtepa MFY",
    "Oqtom MFY",
    "Oʻzbekiston MFY",
    "Qoʻrgʻontepa MFY",
    "Qoʻriq MFY",
    "Qorasuv MFY",
    "Qurilish MFY",
    "Satkak MFY",
    "Sharq xaqiqati MFY",
    "Shifokor MFY",
    "Shohimardonobod MFY",
    "Soy boʻyi MFY",
    "Toshqoʻrgʻoni Aziz MFY",
    "Ulugʻbek MFY",
    "Vaziyo MFY",
    "Xoʻja Ahmad Vale MFY",
    "Xonqiz MFY",
    "Xoʻroba MFY",
    "Xurshidi tobon MFY",
    "Yangi asr MFY",
    "Yangi yoʻl MFY",
    "Yordon MFY",
    "Yoyilma MFY",
    "Yuksalish MFY",
    "Yuqori Gulshan MFY",
    "Yuqori Vodil MFY",
    "Zilol MFY",
)


_APOSTROPHES = "'’‘ʻʼ`ʼ"
_CYRILLIC = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "ҳ", "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м",
    "n": "н", "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с",
    "t": "т", "u": "у", "v": "в", "x": "х", "y": "й", "z": "з",
}
_DIGRAPHS = {"sh": "ш", "ch": "ч", "ng": "нг"}


def _cased(value: str, source: str) -> str:
    return value.upper() if source.isupper() else value.capitalize() if source[0].isupper() else value


def uzbek_latin_to_cyrillic(value: str) -> str:
    """Transliterate Uzbek names while keeping apostrophe letters distinct."""
    value = unicodedata.normalize("NFC", value).replace("ō", "oʻ").replace("Ō", "Oʻ")
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        lower = value[index:index + 2].casefold()

        if char.casefold() in {"o", "g"} and index + 1 < len(value) and value[index + 1] in _APOSTROPHES:
            letter = "ў" if char.casefold() == "o" else "ғ"
            result.append(_cased(letter, char))
            index += 2
            continue

        if index + 1 < len(value) and lower in _DIGRAPHS:
            result.append(_cased(_DIGRAPHS[lower], value[index:index + 2]))
            index += 2
            continue

        at_word_start = index == 0 or value[index - 1].isspace() or value[index - 1] == "-"
        if at_word_start and index + 1 < len(value) and char.casefold() == "y":
            pair = value[index:index + 2].casefold()
            if pair in {"ya", "yu"}:
                result.append(_cased({"ya": "я", "yu": "ю"}[pair], char))
                index += 2
                continue
            if pair == "yo" and (index + 2 == len(value) or value[index + 2] not in _APOSTROPHES):
                result.append(_cased("ё", char))
                index += 2
                continue

        if char in _APOSTROPHES:
            result.append("ъ")
        else:
            cyrillic = _CYRILLIC.get(char.casefold(), char)
            result.append(_cased(cyrillic, char) if char.casefold() in _CYRILLIC else char)
        index += 1
    return "".join(result)


def normalize_mfy_name(value: str) -> str:
    """Build a duplicate-safe comparison key across common Uzbek apostrophes."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("ō", "oʻ").replace("ʻ", "'").replace("ʼ", "'")
    value = value.replace("‘", "'").replace("’", "'").replace("`", "'")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+(?:mfy|мфй|мсг)$", "", value)
    return value


@dataclass(frozen=True)
class MfyCatalogEntry:
    name_uz: str
    name_uz_cyrillic: str
    name_ru: str


def build_mfy_catalog() -> tuple[MfyCatalogEntry, ...]:
    entries = []
    for name_uz in MFY_NAMES_UZ:
        name_cyrillic = uzbek_latin_to_cyrillic(name_uz)
        base_cyrillic = re.sub(r"\s+МФЙ$", "", name_cyrillic)
        entries.append(MfyCatalogEntry(
            name_uz=name_uz,
            name_uz_cyrillic=name_cyrillic,
            # Source files contain Uzbek names only; show the local name in Cyrillic.
            name_ru=f"МСГ {base_cyrillic}",
        ))
    return tuple(entries)


MFY_CATALOG = build_mfy_catalog()
