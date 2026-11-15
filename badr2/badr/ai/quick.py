"""Zero-token fast path.

Routine orders have a fixed shape ("range rings 1, 3, 5 km at OBJ FOX").
Sending those to a language model costs seconds and tokens for no benefit, so
they are parsed here and executed directly through the same Toolbox.  Anything
that does not match exactly falls through to the assistant untouched.

``route(text)`` returns ``(tool_name, args)`` or ``None``.
"""
import re

from ..core.geodesy import parse_distance
from ..core.mgrs import parse_coordinate

_DIST = r"(\d+(?:\.\d+)?\s*(?:km|m|nm|mi))"
_MOVER = r"(wheeled|tracked|dismounted)"
_AT = r"(?:at|from|around|on|of|near)"


def _radii(text):
    """'1, 3 and 5 km' -> [1000, 3000, 5000]; '500 m, 2 km' -> [500, 2000]."""
    m = re.fullmatch(r"\s*((?:\d+(?:\.\d+)?\s*(?:km|m|nm|mi)?\s*(?:,|and|/|\s)\s*)*\d+(?:\.\d+)?\s*(km|m|nm|mi))\s*", text)
    if not m:
        return None
    last_unit = m.group(2)
    out = []
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(km|m|nm|mi)?", m.group(1)):
        out.append(parse_distance(num + (unit or last_unit)))
    return out


_RULES = []


def rule(pattern):
    def deco(fn):
        _RULES.append((re.compile(pattern, re.I), fn))
        return fn
    return deco


@rule(rf"(?:go ?to|goto|zoom to|centre on|center on|show me)\s+(.+)")
def _goto(m):
    return "go_to", {"location": m.group(1).strip()}


@rule(rf"(?:range )?rings?\s+(.+?)\s+{_AT}\s+(.+)")
def _rings(m):
    radii = _radii(m.group(1))
    return ("terrain", {"analysis": "range_rings", "radii_m": radii, "location": m.group(2).strip()}) if radii else None


@rule(rf"viewshed\s+{_AT}\s+(.+?)(?:[, ]+(?:radius\s+)?{_DIST})?")
def _viewshed(m):
    args = {"analysis": "viewshed", "location": m.group(1).strip()}
    if m.group(2):
        args["radius_m"] = parse_distance(m.group(2))
    return "terrain", args


@rule(r"(?:los|line of sight)\s+(?:from\s+)?(.+?)\s+to\s+(.+)")
def _los(m):
    return "terrain", {"analysis": "line_of_sight", "location": m.group(1).strip(), "location2": m.group(2).strip()}


@rule(rf"(?:{_MOVER}\s+)?route\s+from\s+(.+?)\s+to\s+(.+)")
def _route(m):
    args = {"analysis": "route", "location": m.group(2).strip(), "location2": m.group(3).strip()}
    if m.group(1):
        args["mover"] = m.group(1).lower()
    return "terrain", args


@rule(rf"(?:{_MOVER}\s+)?mobility(?:\s+(?:for\s+)?{_MOVER})?")
def _mobility(m):
    mover = m.group(1) or m.group(2)
    return "terrain", {"analysis": "mobility", **({"mover": mover.lower()} if mover else {})}


@rule(r"slope(?: map)?")
def _slope(m):
    return "terrain", {"analysis": "slope"}


@rule(r"(?:find |show )?(?:landing zones?|hlzs?|lzs?)")
def _lz(m):
    return "terrain", {"analysis": "landing_zones"}


@rule(rf"(?:grg|(?:reference )?grid)(?:\s+{_DIST})?")
def _grid(m):
    return "terrain", {"analysis": "grid", **({"cell_m": parse_distance(m.group(1))} if m.group(1) else {})}


@rule(rf"cordon\s+{_AT}\s+(.+)")
def _cordon(m):
    return "terrain", {"analysis": "cordon", "location": m.group(1).strip()}


@rule(r"(?:get |load |download )?(?:elevation|dem|terrain data)(?: for (?:this )?(?:view|area|here))?")
def _dem(m):
    return "get_elevation", {}


@rule(r"(?:incident )?hot ?spots?")
def _hot(m):
    return "incident_analysis", {"analysis": "hotspots"}


@rule(r"route (?:threat|exposure)")
def _threat(m):
    return "incident_analysis", {"analysis": "route_threat"}


@rule(r"(?:pattern of life|temporal pattern|incident timing)")
def _temporal(m):
    return "incident_analysis", {"analysis": "temporal"}


@rule(r"basemap\s+(.+)")
def _basemap(m):
    return "reference_data", {"action": "basemap", "name": m.group(1).strip()}


@rule(r"(?:import |load |add )?osm\s+(\w+)")
def _osm(m):
    return "reference_data", {"action": "osm", "name": m.group(1).strip()}


_SMALL_TALK = re.compile(r"(hi+|hello+|hey+|yo|salam|assalam\w*( o)? ?alaikum|good (morning|afternoon|evening)|"
                         r"thanks?( you)?|thank you|thx|ok(ay)?|cool|great|nice|test(ing)?|ping)[ !.]*", re.I)


def small_talk(text):
    """A greeting or acknowledgement needs no model call at all."""
    t = text.strip()
    if not _SMALL_TALK.fullmatch(t):
        return None
    if re.match(r"th(anks|ank you|x)", t, re.I):
        return "Any time."
    if re.match(r"(ok|okay|cool|great|nice)", t, re.I):
        return "Standing by."
    return "Ready. Give me an order or a question - try \"brief me\"."


def route(text):
    t = text.strip().rstrip(".!")
    if t.startswith("/"):
        t = t[1:]
    try:                                    # a bare coordinate means "go there"
        parse_coordinate(t)
        return "go_to", {"location": t}
    except ValueError:
        pass
    for rx, fn in _RULES:
        m = rx.fullmatch(t)
        if m:
            out = fn(m)
            if out:
                return out
    return None
