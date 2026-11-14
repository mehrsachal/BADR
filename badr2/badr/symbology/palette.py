"""Curated planning palette.

The full APP-6D catalog (1,600+ entities) is always reachable through search
and through the AI.  This palette is the short list a planner reaches for in
conventional and - especially - sub-conventional (COIN / CT / stability)
operations, grouped the way the work is done rather than the way the standard
is numbered.

Each item is either a point symbol (``set`` + ``entity`` [+ modifiers]) or a
tactical graphic drawn on the ``lines`` / ``areas`` layers (``layer`` + ``kind``).
"""

def _u(label, entity, aff="friend", set_="10", **kw):
    d = {"label": label, "set": set_, "entity": entity, "affiliation": aff}
    d.update(kw)
    return d


PALETTE = {
    "Own forces": [
        _u("Infantry", "121100"),
        _u("Mechanized infantry", "121102"),
        _u("Armour", "120500"),
        _u("Recce / Cavalry", "121300"),
        _u("Special forces", "121700"),
        _u("SOF", "121800"),
        _u("Sniper team", "121500", echelon="team"),
        _u("UAS unit", "121900"),
        _u("Field artillery", "130300"),
        _u("Mortar", "130800"),
        _u("Engineer", "140700"),
        _u("EOD", "140800"),
        _u("Military police", "141200"),
        _u("Intelligence", "151000"),
        _u("Signal", "111000"),
        _u("Medical", "161300"),
        _u("Supply", "163400"),
        _u("Civil affairs / CIMIC", "110300"),
        _u("Headquarters", "110000", hq="hq"),
    ],
    "Partner & security forces": [
        _u("Police", "200700", aff="assumed_friend"),
        _u("Border patrol", "200200", aff="assumed_friend"),
        _u("Internal security force", "201300", aff="assumed_friend"),
        _u("Security (guard) unit", "141700", aff="assumed_friend"),
    ],
    "Threat - irregular": [
        _u("Insurgent / terrorist cell", "110400", aff="hostile", set_="11", mod1="19"),
        _u("Insurgent leader", "110300", aff="hostile", set_="11", mod1="19", mod2="01"),
        _u("Foreign fighters", "110400", aff="hostile", set_="11", mod1="10"),
        _u("Armed gang", "110400", aff="hostile", set_="11", mod1="11"),
        _u("Hostile infantry (irregular)", "121100", aff="hostile"),
        _u("Hostile sniper", "121500", aff="hostile", echelon="team"),
        _u("Hostile mortar team", "130800", aff="hostile", echelon="team"),
        _u("Suspected spy / informant", "110800", aff="suspect", set_="11"),
        _u("Known insurgent vehicle", "160800", aff="hostile", set_="15"),
        _u("Technical (pickup)", "160201", aff="hostile", set_="15"),
        _u("IED (emplaced)", "210400", aff="hostile", set_="15"),
        _u("Booby trap", "200300", aff="hostile", set_="15"),
        _u("Antipersonnel mine", "210200", aff="hostile", set_="15"),
        _u("Hostile UAS", "121900", aff="hostile"),
    ],
    "Threat - sites": [
        _u("Safe house", "111700", aff="hostile", set_="20"),
        _u("Ammunition / weapons cache", "110300", aff="hostile", set_="20"),
        _u("Training camp", "111902", aff="hostile", set_="20"),
        _u("Black-list location", "110500", aff="suspect", set_="20"),
        _u("Tunnel", "121313", aff="suspect", set_="20"),
        _u("Illegal drug lab", "110502", aff="hostile", set_="40"),
        _u("IED cache", "110303", aff="hostile", set_="40"),
    ],
    "Incidents (SIGACTS)": [
        _u("IED explosion", "110301", aff="hostile", set_="40", layer="incidents"),
        _u("IED found & cleared", "110300", aff="hostile", set_="40", mod1="13", layer="incidents"),
        _u("Suicide bomber", "110304", aff="hostile", set_="40", layer="incidents"),
        _u("VBIED / vehicle explosion", "160500", aff="hostile", set_="40", layer="incidents"),
        _u("Shooting / small-arms fire", "110400", aff="hostile", set_="40", layer="incidents"),
        _u("Sniping", "110401", aff="hostile", set_="40", layer="incidents"),
        _u("Mortar / indirect fire", "110604", aff="hostile", set_="40", layer="incidents"),
        _u("Rocket attack", "110605", aff="hostile", set_="40", layer="incidents"),
        _u("Grenade attack", "110601", aff="hostile", set_="40", layer="incidents"),
        _u("Kidnapping", "110100", aff="hostile", set_="40", mod1="05", layer="incidents"),
        _u("Assassination", "110108", aff="hostile", set_="40", mod1="01", layer="incidents"),
        _u("Arson", "110102", aff="hostile", set_="40", layer="incidents"),
        _u("Sabotage", "110122", aff="hostile", set_="40", layer="incidents"),
        _u("Smuggling", "110119", aff="hostile", set_="40", layer="incidents"),
        _u("Extortion", "110106", aff="hostile", set_="40", layer="incidents"),
        _u("Suspicious activity", "110123", aff="suspect", set_="40", layer="incidents"),
        _u("Demonstration", "120100", aff="neutral", set_="40", layer="incidents"),
        _u("Civil rioting", "110110", aff="neutral", set_="40", layer="incidents"),
        _u("Propaganda", "130201", aff="hostile", set_="40", layer="incidents"),
        _u("Recruitment (coerced)", "130402", aff="hostile", set_="40", layer="incidents"),
        _u("Infiltration", "130900", aff="hostile", set_="40", layer="incidents"),
        _u("Arrest / detention", "110101", aff="friend", set_="40", layer="incidents"),
        _u("Raid on house", "131100", aff="friend", set_="40", layer="incidents"),
        _u("Meeting / KLE", "131000", aff="neutral", set_="40", layer="incidents"),
        _u("UXO", "150115", aff="unknown", set_="40", layer="incidents"),
    ],
    "Human terrain": [
        _u("Civilian group", "110400", aff="neutral", set_="11"),
        _u("Displaced persons", "110400", aff="neutral", set_="11", mod1="09"),
        _u("Community / tribal leader", "110300", aff="neutral", set_="11", mod1="13"),
        _u("Religious leader", "180100", aff="neutral", set_="40"),
        _u("NGO", "110400", aff="neutral", set_="11", mod1="14"),
        _u("Government office", "110200", aff="neutral", set_="11"),
        _u("Religious institution", "121004", aff="neutral", set_="20"),
        _u("School", "120402", aff="neutral", set_="20"),
        _u("Hospital", "120702", aff="neutral", set_="20"),
        _u("IDP / refugee camp", "111901", aff="neutral", set_="20"),
        _u("Polling place", "131001", aff="neutral", set_="40"),
        _u("Police station", "112107", aff="assumed_friend", set_="20"),
        _u("Telecom tower", "121203", aff="neutral", set_="20"),
        _u("Bridge", "110701", aff="neutral", set_="20"),
        _u("Water supply", "121410", aff="neutral", set_="20"),
        _u("Electric power", "120501", aff="neutral", set_="20"),
    ],
    "Control points": [
        _u("Checkpoint", "130300", set_="25"),
        _u("Observation post", "160100", set_="25"),
        _u("Listening / sensor post", "160204", set_="25"),
        _u("Combat outpost", "160205", set_="25"),
        _u("Rally point", "131400", set_="25"),
        _u("Start point", "131600", set_="25"),
        _u("Release point", "131500", set_="25"),
        _u("Contact point", "130500", set_="25"),
        _u("Decision point", "130700", set_="25"),
        _u("Point of interest", "131300", set_="25"),
        _u("Detainee collection point", "320700", set_="25"),
    ],
    "Control lines": [
        {"label": "Boundary", "layer": "lines", "kind": "boundary"},
        {"label": "Phase line", "layer": "lines", "kind": "phase_line"},
        {"label": "Line of departure", "layer": "lines", "kind": "line_of_departure"},
        {"label": "Limit of advance", "layer": "lines", "kind": "limit_of_advance"},
        {"label": "Route (MSR/ASR)", "layer": "lines", "kind": "route"},
        {"label": "Patrol route", "layer": "lines", "kind": "patrol_route"},
        {"label": "Axis of advance", "layer": "lines", "kind": "axis_of_advance"},
        {"label": "Infiltration lane", "layer": "lines", "kind": "infiltration_lane"},
        {"label": "Threat route (suspected)", "layer": "lines", "kind": "threat_route"},
        {"label": "Obstacle / wire", "layer": "lines", "kind": "obstacle"},
    ],
    "Control areas": [
        {"label": "Area of operations", "layer": "areas", "kind": "ao"},
        {"label": "Named area of interest", "layer": "areas", "kind": "nai"},
        {"label": "Objective", "layer": "areas", "kind": "objective"},
        {"label": "Assembly area", "layer": "areas", "kind": "assembly_area"},
        {"label": "Landing zone (HLZ)", "layer": "areas", "kind": "lz"},
        {"label": "Cordon - inner", "layer": "areas", "kind": "cordon_inner"},
        {"label": "Cordon - outer", "layer": "areas", "kind": "cordon_outer"},
        {"label": "Search area", "layer": "areas", "kind": "search_area"},
        {"label": "Base / FOB", "layer": "areas", "kind": "base"},
        {"label": "Restricted / no-fire area", "layer": "areas", "kind": "restricted"},
        {"label": "Restricted operating zone (air)", "layer": "areas", "kind": "roz"},
        {"label": "Guerrilla base (suspected)", "layer": "areas", "kind": "guerrilla_base"},
        {"label": "Threat support zone", "layer": "areas", "kind": "threat_zone"},
        {"label": "Detainee holding area", "layer": "areas", "kind": "detainee_area"},
    ],
}

# kind -> (abbreviation for the label, colour key, line style, hostile?)
GRAPHIC_KINDS = {
    # lines
    "boundary": ("", "friend", "solid"),
    "phase_line": ("PL", "friend", "solid"),
    "line_of_departure": ("LD", "friend", "solid"),
    "limit_of_advance": ("LOA", "friend", "solid"),
    "route": ("RTE", "friend", "solid"),
    "patrol_route": ("PTL", "friend", "dash"),
    "axis_of_advance": ("AXIS", "friend", "arrow"),
    "infiltration_lane": ("INFIL", "friend", "arrow_dash"),
    "threat_route": ("", "hostile", "arrow_dash"),
    "obstacle": ("", "obstacle", "solid"),
    # areas
    "ao": ("AO", "friend", "solid"),
    "nai": ("NAI", "friend", "solid"),
    "objective": ("OBJ", "friend", "solid"),
    "assembly_area": ("AA", "friend", "solid"),
    "lz": ("LZ", "friend", "solid"),
    "cordon_inner": ("INNER CORDON", "friend", "solid"),
    "cordon_outer": ("OUTER CORDON", "friend", "dash"),
    "search_area": ("SEARCH", "friend", "dash"),
    "base": ("FOB", "friend", "solid"),
    "restricted": ("NFA", "restricted", "solid"),
    "roz": ("ROZ", "restricted", "dash"),
    "guerrilla_base": ("GUERRILLA BASE", "hostile", "dash"),
    "threat_zone": ("", "hostile", "dash"),
    "detainee_area": ("DHA", "friend", "solid"),
    "range_ring": ("", "analysis", "dash"),
    "grg": ("", "analysis", "solid"),
    "hlz_candidate": ("HLZ?", "analysis", "solid"),
    "hotspot": ("HOTSPOT", "hostile", "solid"),
    "los_visible": ("", "visible", "solid"),
    "los_hidden": ("", "hidden", "solid"),
    "analysis": ("", "analysis", "solid"),
}

COLORS = {
    "friend": "#00A8DC", "hostile": "#FF3031", "neutral": "#00E200", "unknown": "#FFFF00",
    "obstacle": "#00C000", "restricted": "#FF3031", "analysis": "#FFB000",
    "visible": "#32FF64", "hidden": "#FF3031",
}


def item_sidc(item, affiliation=None, echelon=None):
    from . import sidc as S
    return S.build(item["set"], item["entity"], affiliation or item.get("affiliation", "friend"),
                   echelon=echelon or item.get("echelon", "none"), hq=item.get("hq", "none"),
                   mod1=item.get("mod1", "00"), mod2=item.get("mod2", "00"))
