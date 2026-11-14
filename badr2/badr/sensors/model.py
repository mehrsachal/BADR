"""Vendor-neutral sensor data model.

Every protocol decoder turns its wire format into ``Observation`` objects.
Nothing above this layer knows (or cares) whether a position came from a
MAVLink autopilot, a TAK server, an ADS-B receiver or a JSON web hook.
"""
import time
from dataclasses import dataclass, field

from ..symbology import sidc as S

# platform kind -> (symbol set, entity)
KIND_SYMBOL = {
    "uas": ("01", "110300"),
    "rotary": ("01", "110200"),
    "fixed_wing": ("01", "110100"),
    "air": ("01", "110000"),
    "civil_air": ("01", "120000"),
    "ground": ("10", "000000"),
    "infantry": ("10", "121100"),
    "vehicle": ("15", "140100"),
    "person": ("27", "110000"),
    "sensor": ("15", "220100"),
    "radar": ("15", "220300"),
    "ugs": ("15", "220200"),
    "sea": ("30", "110000"),
    "usv": ("30", "120700"),
    "unknown": ("10", "000000"),
}
KINDS = sorted(KIND_SYMBOL)


@dataclass
class Observation:
    uid: str
    lat: float
    lon: float
    alt: float = None            # metres (HAE/MSL as supplied)
    heading: float = None        # degrees true
    speed: float = None          # m/s
    name: str = ""
    kind: str = "unknown"
    affiliation: str = "unknown"
    sidc: str = ""               # explicit SIDC from the source wins over kind/affiliation
    time: float = field(default_factory=time.time)
    fov: tuple = None            # (azimuth_deg, width_deg, range_m) sensor field of view
    footprint: list = None       # [(lon, lat), ...] sensor ground footprint
    video_url: str = ""
    attrs: dict = field(default_factory=dict)

    def valid(self):
        return (self.uid and self.lat is not None and self.lon is not None
                and -90 <= self.lat <= 90 and -180 <= self.lon <= 180
                and not (self.lat == 0 and self.lon == 0))

    def symbol(self):
        if S.is_valid(self.sidc):
            return self.sidc
        sset, entity = KIND_SYMBOL.get(self.kind, KIND_SYMBOL["unknown"])
        try:
            return S.build(sset, entity, self.affiliation or "unknown")
        except ValueError:
            return S.build(sset, entity, "unknown")
