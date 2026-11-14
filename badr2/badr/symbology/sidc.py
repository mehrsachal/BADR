"""APP-6D / MIL-STD-2525D Symbol Identification Codes (20-digit, number based).

Layout (1-based positions):
    1-2   version (10)
    3     context          0 reality, 1 exercise, 2 simulation
    4     standard identity (affiliation)
    5-6   symbol set
    7     status
    8     HQ / task force / dummy
    9-10  amplifier (echelon / mobility)
    11-16 entity, type, subtype
    17-18 sector 1 modifier
    19-20 sector 2 modifier
"""
import json
import os
import re

AFFILIATIONS = {
    "pending": "0", "unknown": "1", "assumed_friend": "2", "friend": "3",
    "neutral": "4", "suspect": "5", "hostile": "6",
}
AFFILIATION_ALIASES = {
    "friendly": "friend", "blue": "friend", "own": "friend", "blufor": "friend",
    "enemy": "hostile", "red": "hostile", "opfor": "hostile", "insurgent": "hostile",
    "green": "neutral", "civilian": "neutral", "joker": "suspect", "white": "unknown",
}
STATUS = {"present": "0", "planned": "1", "anticipated": "1", "capable": "2",
          "damaged": "3", "destroyed": "4", "full": "5"}
HQTFD = {"none": "0", "dummy": "1", "hq": "2", "dummy_hq": "3", "task_force": "4",
         "dummy_task_force": "5", "task_force_hq": "6"}
ECHELONS = {
    "none": "00", "team": "11", "crew": "11", "squad": "12", "section": "13",
    "platoon": "14", "detachment": "14", "company": "15", "battery": "15", "troop": "15",
    "battalion": "16", "squadron": "16", "regiment": "17", "group": "17", "brigade": "18",
    "division": "21", "corps": "22", "army": "23", "army_group": "24", "region": "25",
    "command": "26",
}
SYMBOL_SETS = {
    "air": "01", "air_missile": "02", "space": "05", "land_unit": "10",
    "land_civilian": "11", "land_equipment": "15", "land_installation": "20",
    "control_measure": "25", "dismounted": "27", "sea_surface": "30",
    "sea_subsurface": "35", "mine_warfare": "36", "activity": "40",
}

_REV = {
    "affiliation": {v: k for k, v in AFFILIATIONS.items()},
    "set": {v: k for k, v in SYMBOL_SETS.items()},
}


def norm_affiliation(value):
    v = str(value or "unknown").strip().lower().replace(" ", "_")
    v = AFFILIATION_ALIASES.get(v, v)
    if v in AFFILIATIONS:
        return v
    if v in _REV["affiliation"]:
        return _REV["affiliation"][v]
    raise ValueError(f"unknown affiliation {value!r}; use one of {sorted(AFFILIATIONS)}")


def build(symbol_set, entity="000000", affiliation="unknown", echelon="none", status="present",
          hq="none", mod1="00", mod2="00", context="0"):
    """Compose a 20-digit SIDC from named or numeric parts."""
    ss = SYMBOL_SETS.get(str(symbol_set), str(symbol_set))
    if not re.fullmatch(r"\d{2}", ss):
        raise ValueError(f"unknown symbol set {symbol_set!r}")
    entity = str(entity)
    if not re.fullmatch(r"\d{6}", entity):
        raise ValueError(f"entity must be 6 digits, got {entity!r}")
    aff = AFFILIATIONS[norm_affiliation(affiliation)]
    ech = ECHELONS.get(str(echelon).lower(), str(echelon))
    if not re.fullmatch(r"\d{2}", ech):
        raise ValueError(f"unknown echelon {echelon!r}")
    st = STATUS.get(str(status).lower(), str(status))
    h = HQTFD.get(str(hq).lower(), str(hq))
    return f"10{context}{aff}{ss}{st}{h}{ech}{entity}{str(mod1).zfill(2)}{str(mod2).zfill(2)}"


def is_valid(sidc):
    return bool(re.fullmatch(r"\d{20}", str(sidc or "")))


def parse(sidc):
    if not is_valid(sidc):
        raise ValueError(f"not a 20-digit SIDC: {sidc!r}")
    return {
        "context": sidc[2], "affiliation": _REV["affiliation"].get(sidc[3], "unknown"),
        "symbol_set": sidc[4:6], "status": sidc[6], "hq": sidc[7], "echelon": sidc[8:10],
        "entity": sidc[10:16], "mod1": sidc[16:18], "mod2": sidc[18:20],
    }


def with_affiliation(sidc, affiliation):
    return sidc[:3] + AFFILIATIONS[norm_affiliation(affiliation)] + sidc[4:]


def with_status(sidc, status):
    return sidc[:6] + STATUS.get(str(status).lower(), str(status)) + sidc[7:]


def with_echelon(sidc, echelon):
    return sidc[:8] + ECHELONS.get(str(echelon).lower(), str(echelon)) + sidc[10:]


# ---------------------------------------------------------------------------
# Catalog (every APP-6D entity) and search
# ---------------------------------------------------------------------------
_CATALOG = None


def catalog():
    global _CATALOG
    if _CATALOG is None:
        path = os.path.join(os.path.dirname(__file__), "data", "app6d.json")
        with open(path, encoding="utf-8") as fh:
            _CATALOG = json.load(fh)
    return _CATALOG


def describe(sidc):
    """Human-readable description, e.g. 'hostile Infantry (platoon)'."""
    try:
        p = parse(sidc)
    except ValueError:
        return str(sidc)
    cset = catalog()["sets"].get(p["symbol_set"])
    name = p["entity"]
    if cset:
        for e in cset["entities"]:
            if e["code"] == p["entity"]:
                name = e["name"].split(" / ")[-1]
                break
        for sector, code in (("1", p["mod1"]), ("2", p["mod2"])):
            if code != "00":
                for m in cset["modifiers"].get(sector, []):
                    if m["code"] == code:
                        name += f", {m['name']}"
    ech = next((k for k, v in ECHELONS.items() if v == p["echelon"] and k != "none"), "")
    return f"{p['affiliation'].replace('_', ' ')} {name}" + (f" ({ech})" if ech else "")


_SYNONYMS = {
    "ied": "improvised explosive device ied", "sf": "special forces", "sof": "special operations forces",
    "uav": "unmanned aerial", "uas": "unmanned aerial", "drone": "unmanned aerial",
    "recce": "reconnaissance", "recon": "reconnaissance", "arty": "field artillery",
    "armor": "armour", "armored": "armoured", "mech": "mechanized", "inf": "infantry",
    "mp": "military police", "hq": "command and control", "medic": "medical",
    "insurgent": "terrorist", "guerrilla": "guerrilla", "militant": "terrorist",
    "cp": "checkpoint", "op": "observation post", "lz": "landing zone", "hlz": "landing zone",
    "nai": "named area of interest", "msr": "main supply route", "cache": "cache",
    "vbied": "wheeled vehicle explosion", "sniper": "sniper sniping", "heli": "rotary wing",
    "helicopter": "rotary wing", "tank": "armour", "hospital": "medical treatment facility hospital",
}


def search(query, symbol_sets=None, limit=12):
    """Rank catalog entities against a free-text query.

    Returns [{symbol_set, set_name, entity, name, geom?, score}].
    """
    q = str(query).lower()
    # each query word is a group of alternatives (itself + its expansion); a
    # group scores the mean of its best-matching reading, so an abbreviation
    # like "IED" counts once however long its expansion is.
    tokens = re.findall(r"[a-z0-9]+", q)
    groups = [[[t]] + ([_SYNONYMS[t].split()] if t in _SYNONYMS else []) for t in tokens]
    if not tokens:
        return []

    def word_score(t, leaf_words, words):
        if t in leaf_words:
            return 3.0
        if t in words:
            return 1.0
        if len(t) > 3 and any(w.startswith(t) for w in leaf_words):
            return 1.5
        return 0.0
    results = []
    wanted = None
    if symbol_sets:
        wanted = {SYMBOL_SETS.get(s, s) for s in symbol_sets}
    for code, cset in catalog()["sets"].items():
        if wanted and code not in wanted:
            continue
        for e in cset["entities"]:
            name = e["name"].lower()
            leaf = name.split(" / ")[-1]
            leaf_words = set(re.findall(r"[a-z0-9]+", leaf))
            words = set(re.findall(r"[a-z0-9]+", name))
            score = 0.0
            for alts in groups:
                score += max(sum(word_score(t, leaf_words, words) for t in alt) / len(alt)
                             for alt in alts)
            if score <= 0:
                continue
            score -= 0.15 * max(0, len(leaf_words) - len(tokens))   # prefer tight matches
            if leaf.strip() == q.strip():
                score += 5
            # land units/activities are what planners place most often
            score += {"10": 0.4, "40": 0.3, "20": 0.2, "25": 0.1}.get(code, 0)
            r = {"symbol_set": code, "set_name": cset["name"], "entity": e["code"],
                 "name": e["name"], "score": round(score, 2)}
            if "geom" in e:
                r["geom"] = e["geom"]
            results.append(r)
    results.sort(key=lambda r: -r["score"])
    return results[:limit]
