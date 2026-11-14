#!/usr/bin/env python3
"""Build badr/symbology/data/app6d.json from the open APP-6D tables.

Source tables: the MIT-licensed ``stanag-app6`` package
(https://github.com/spatialillusions/stanag-app6), directory
``tsv-tables/app6d``.  Usage::

    npm pack stanag-app6 && tar xzf stanag-app6-*.tgz
    python tools/build_symbol_catalog.py package/tsv-tables/app6d

The output is a compact JSON catalog of every entity and modifier in every
symbol set, which BADR uses for symbol search, the AI ``find_symbol`` tool and
SIDC validation.
"""
import csv
import json
import os
import sys

# table file stem -> APP-6D symbol-set code
SYMBOL_SETS = {
    "Air": "01",
    "Air missile": "02",
    "Space": "05",
    "Land unit": "10",
    "Land civilian": "11",
    "Land equipment": "15",
    "Land installation": "20",
    "Control Measures": "25",
    "Dismounted individual": "27",
    "Sea surface": "30",
    "Sea subsurface": "35",
    "Mine warfare": "36",
    "Activities": "40",
}


def read_tsv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def build(src_dir):
    catalog = {"standard": "APP-6D", "sets": {}}
    for stem, code in SYMBOL_SETS.items():
        main = os.path.join(src_dir, stem + ".tsv")
        if not os.path.exists(main):
            continue
        entities = []
        parents = ["", "", ""]
        for row in read_tsv(main):
            sidc = (row.get("Code") or "").strip()
            if len(sidc) != 6 or not sidc.isdigit():
                continue
            for i, col in enumerate(("Entity", "Entity Type", "Entity Subtype")):
                val = (row.get(col) or "").strip()
                if val:
                    parents[i] = val
                    for j in range(i + 1, 3):
                        parents[j] = ""
            name = " / ".join(p for p in parents if p)
            ent = {"code": sidc, "name": name}
            geom = (row.get("Geometric Rendering") or "").strip()
            if geom:
                ent["geom"] = geom.lower()
            entities.append(ent)
        mods = {}
        for n in (1, 2):
            p = os.path.join(src_dir, f"{stem} sector {n}.tsv")
            if os.path.exists(p):
                col = "First Modifier" if n == 1 else "Second Modifier"
                mods[str(n)] = [
                    {"code": r["Code"].strip(), "name": r[col].strip()}
                    for r in read_tsv(p)
                    if (r.get("Code") or "").strip().isdigit()
                ]
        catalog["sets"][code] = {"name": stem, "entities": entities, "modifiers": mods}
    return catalog


if __name__ == "__main__":
    src = sys.argv[1]
    out = os.path.join(os.path.dirname(__file__), "..", "badr", "symbology", "data", "app6d.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cat = build(src)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(cat, fh, separators=(",", ":"), ensure_ascii=False)
    n = sum(len(s["entities"]) for s in cat["sets"].values())
    print(f"wrote {os.path.normpath(out)}: {len(cat['sets'])} symbol sets, {n} entities")
