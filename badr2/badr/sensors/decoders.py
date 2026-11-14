"""Protocol decoders: bytes in, Observations out.

All decoders are *receive-only* - BADR is a planning and analysis tool and
never commands a platform.  Each decoder is a small stream parser with its own
framing, so it works identically over UDP, TCP, serial, HTTP or a replay file.

    cot      Cursor-on-Target XML (ATAK / WinTAK / TAK Server, many UAS GCS)
    mavlink  MAVLink v1/v2 telemetry (ArduPilot, PX4 and most open autopilots)
    nmea     NMEA 0183 GGA/RMC (GPS receivers, trackers, radios)
    sbs      SBS-1 / BaseStation (ADS-B receivers, dump1090)
    json     JSON / GeoJSON with a user-defined field mapping (anything else)

Adding a protocol means writing one class with ``feed(data) -> [Observation]``
and registering it in ``DECODERS``.
"""
import json
import re
import struct
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .model import Observation

KNOT = 0.514444


class Decoder:
    name = "base"
    label = "Base"
    options = {}                    # option name -> (default, help)

    def __init__(self, **opts):
        self.opts = {k: v[0] for k, v in self.options.items()}
        self.opts.update({k: v for k, v in opts.items() if v not in (None, "")})
        self.buf = b""

    def feed(self, data):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Cursor on Target
# ---------------------------------------------------------------------------
_COT_AFF = {"f": "friend", "h": "hostile", "n": "neutral", "u": "unknown", "s": "suspect",
            "a": "assumed_friend", "p": "pending", "j": "suspect", "k": "hostile"}


def cot_type_to_kind(cot_type):
    """'a-f-A-M-F-Q' -> ('friend', 'uas')."""
    parts = (cot_type or "").split("-")
    aff = _COT_AFF.get(parts[1].lower(), "unknown") if len(parts) > 1 else "unknown"
    dim = parts[2].upper() if len(parts) > 2 else ""
    rest = "-".join(parts[3:]).upper()
    if dim == "A":
        if "Q" in parts[3:] or rest.endswith("-Q"):
            kind = "uas"
        elif rest.startswith("M-H") or rest.startswith("C-H"):
            kind = "rotary"
        elif rest.startswith("C"):
            kind = "civil_air"
        elif rest.startswith("M-F"):
            kind = "fixed_wing"
        else:
            kind = "air"
    elif dim == "S":
        kind = "sea"
    elif dim == "G":
        if rest.startswith("E-S"):
            kind = "sensor"
        elif rest.startswith("E-V") or rest.startswith("E"):
            kind = "vehicle"
        elif rest.startswith("U-C-I"):
            kind = "infantry"
        else:
            kind = "ground"
    else:
        kind = "unknown"
    return aff, kind


class CotDecoder(Decoder):
    name = "cot"
    label = "Cursor-on-Target (TAK)"

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            start = self.buf.find(b"<event")
            if start < 0:
                self.buf = self.buf[-16:]
                break
            end = self.buf.find(b"</event>", start)
            if end < 0:
                self.buf = self.buf[start:]
                if len(self.buf) > 1 << 20:
                    self.buf = b""
                break
            chunk = self.buf[start:end + 8]
            self.buf = self.buf[end + 8:]
            obs = self._parse(chunk)
            if obs:
                out.append(obs)
        return out

    @staticmethod
    def _parse(chunk):
        try:
            ev = ET.fromstring(chunk)
        except ET.ParseError:
            return None
        pt = ev.find("point")
        if pt is None:
            return None
        try:
            lat, lon = float(pt.get("lat")), float(pt.get("lon"))
        except (TypeError, ValueError):
            return None
        aff, kind = cot_type_to_kind(ev.get("type", ""))
        o = Observation(uid=ev.get("uid", ""), lat=lat, lon=lon, kind=kind, affiliation=aff)
        try:
            hae = float(pt.get("hae", "nan"))
            if hae == hae and hae < 9000000:
                o.alt = hae
        except ValueError:
            pass
        det = ev.find("detail")
        if det is not None:
            c = det.find("contact")
            if c is not None:
                o.name = c.get("callsign", "")
            tr = det.find("track")
            if tr is not None:
                try:
                    o.heading = float(tr.get("course"))
                    o.speed = float(tr.get("speed"))
                except (TypeError, ValueError):
                    pass
            se = det.find("sensor")
            if se is not None:
                try:
                    o.fov = (float(se.get("azimuth")), float(se.get("fov", 40)), float(se.get("range", 500)))
                except (TypeError, ValueError):
                    pass
            vid = det.find("__video")
            if vid is not None:
                o.video_url = vid.get("url", "")
            rem = det.find("remarks")
            if rem is not None and rem.text:
                o.attrs["remarks"] = rem.text.strip()[:200]
        o.name = o.name or o.uid
        o.attrs["cot_type"] = ev.get("type", "")
        return o if o.valid() else None


# ---------------------------------------------------------------------------
# MAVLink (telemetry subset, CRC-checked)
# ---------------------------------------------------------------------------
# msgid -> (crc_extra, min payload length)
_MAV = {0: (50, 9), 24: (24, 30), 30: (39, 28), 33: (104, 28), 74: (20, 20), 1: (124, 31)}
_MAV_TYPE_KIND = {1: "fixed_wing", 2: "uas", 3: "uas", 4: "rotary", 10: "vehicle", 11: "usv",
                  12: "sea", 13: "uas", 14: "uas", 15: "uas", 19: "uas", 20: "uas", 21: "uas", 22: "uas"}


def mav_crc(data, extra):
    crc = 0xFFFF
    for b in bytes(data) + bytes([extra]):
        tmp = b ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


class MavlinkDecoder(Decoder):
    name = "mavlink"
    label = "MAVLink telemetry (ArduPilot / PX4)"
    options = {"affiliation": ("friend", "affiliation assigned to vehicles on this link"),
               "name_prefix": ("UAS", "callsign prefix; system id is appended")}

    def __init__(self, **opts):
        super().__init__(**opts)
        self.state = {}

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = next((k for k, b in enumerate(self.buf) if b in (0xFD, 0xFE)), -1)
            if i < 0:
                self.buf = b""
                break
            self.buf = self.buf[i:]
            if len(self.buf) < 10:
                break
            v2 = self.buf[0] == 0xFD
            plen = self.buf[1]
            if v2:
                hdr = 10
                sysid, msgid = self.buf[5], self.buf[7] | (self.buf[8] << 8) | (self.buf[9] << 16)
                total = hdr + plen + 2 + (13 if self.buf[2] & 0x01 else 0)
            else:
                hdr = 6
                sysid, msgid = self.buf[3], self.buf[5]
                total = hdr + plen + 2
            spec = _MAV.get(msgid)
            if spec is None or plen > 64:
                # Not a message we decode (or a false sync byte inside other
                # data).  Its CRC cannot be checked, so never trust its length
                # field: step one byte and look for the next start marker.
                self.buf = self.buf[1:]
                continue
            if len(self.buf) < total:
                break
            frame = self.buf[:total]
            crc_pos = hdr + plen
            crc = frame[crc_pos] | (frame[crc_pos + 1] << 8)
            if mav_crc(frame[1:crc_pos], spec[0]) != crc:
                self.buf = self.buf[1:]      # false sync byte: resynchronise
                continue
            self.buf = self.buf[total:]
            payload = frame[hdr:crc_pos].ljust(spec[1], b"\0")   # v2 trims trailing zeros
            obs = self._handle(sysid, msgid, payload)
            if obs:
                out.append(obs)
        return out

    def _handle(self, sysid, msgid, p):
        st = self.state.setdefault(sysid, {"kind": "uas"})
        if msgid == 0:
            _mode, mtype = struct.unpack_from("<IB", p, 0)
            st["kind"] = _MAV_TYPE_KIND.get(mtype, "uas")
            if mtype == 6:            # ground control station, not a vehicle
                st["gcs"] = True
            return None
        if msgid == 30:
            _t, roll, pitch, yaw = struct.unpack_from("<Ifff", p, 0)
            st["att"] = (roll, pitch, yaw)
            return None
        if msgid == 74:
            _air, ground, _alt, _climb, _hdg, _thr = struct.unpack_from("<ffffhH", p, 0)
            st["ground_speed"] = ground
            return None
        if msgid == 1:
            st["battery"] = struct.unpack_from("<b", p, 30)[0]
            return None
        if st.get("gcs"):
            return None
        if msgid == 33:
            _t, lat, lon, alt, rel, vx, vy, _vz, hdg = struct.unpack_from("<IiiiihhhH", p, 0)
            o = Observation(uid=f"mav-{sysid}", lat=lat / 1e7, lon=lon / 1e7, alt=alt / 1000.0,
                            heading=None if hdg == 0xFFFF else hdg / 100.0,
                            speed=(vx * vx + vy * vy) ** 0.5 / 100.0)
            o.attrs["agl"] = rel / 1000.0
        elif msgid == 24:
            _t, lat, lon, alt, _eph, _epv, vel, cog, fix, sats = struct.unpack_from("<QiiiHHHHBB", p, 0)
            if fix < 2:
                return None
            o = Observation(uid=f"mav-{sysid}", lat=lat / 1e7, lon=lon / 1e7, alt=alt / 1000.0,
                            heading=None if cog == 0xFFFF else cog / 100.0,
                            speed=None if vel == 0xFFFF else vel / 100.0)
            o.attrs["sats"] = sats
        else:
            return None
        o.kind = st["kind"]
        o.affiliation = self.opts["affiliation"]
        o.name = f"{self.opts['name_prefix']}-{sysid}"
        if "battery" in st and st["battery"] >= 0:
            o.attrs["battery"] = st["battery"]
        return o if o.valid() else None


# ---------------------------------------------------------------------------
# NMEA 0183
# ---------------------------------------------------------------------------
def _nmea_deg(value, hemi):
    if not value:
        return None
    dot = value.index(".") if "." in value else len(value)
    deg = float(value[:dot - 2])
    minutes = float(value[dot - 2:])
    v = deg + minutes / 60
    return -v if hemi in ("S", "W") else v


class NmeaDecoder(Decoder):
    name = "nmea"
    label = "NMEA 0183 (GPS / tracker)"
    options = {"uid": ("gps-1", "track id for this receiver"),
               "name": ("", "callsign shown on the map"),
               "kind": ("ground", "platform kind"),
               "affiliation": ("friend", "affiliation")}

    def feed(self, data):
        self.buf += data
        out = []
        *lines, self.buf = self.buf.split(b"\n")
        for raw in lines:
            line = raw.decode("ascii", "ignore").strip()
            if not line.startswith("$") or "*" not in line:
                continue
            body, _, cks = line[1:].partition("*")
            calc = 0
            for ch in body:
                calc ^= ord(ch)
            try:
                if calc != int(cks[:2], 16):
                    continue
            except ValueError:
                continue
            f = body.split(",")
            typ = f[0][2:]
            o = None
            try:
                if typ == "GGA" and len(f) > 9 and f[6] not in ("", "0"):
                    o = self._obs(_nmea_deg(f[2], f[3]), _nmea_deg(f[4], f[5]))
                    if o and f[9]:
                        o.alt = float(f[9])
                elif typ == "RMC" and len(f) > 8 and f[2] == "A":
                    o = self._obs(_nmea_deg(f[3], f[4]), _nmea_deg(f[5], f[6]))
                    if o:
                        o.speed = float(f[7]) * KNOT if f[7] else None
                        o.heading = float(f[8]) if f[8] else None
            except (ValueError, IndexError):
                o = None
            if o and o.valid():
                out.append(o)
        return out

    def _obs(self, lat, lon):
        if lat is None or lon is None:
            return None
        return Observation(uid=self.opts["uid"], lat=lat, lon=lon, name=self.opts["name"] or self.opts["uid"],
                           kind=self.opts["kind"], affiliation=self.opts["affiliation"])


# ---------------------------------------------------------------------------
# SBS-1 / BaseStation (ADS-B)
# ---------------------------------------------------------------------------
class SbsDecoder(Decoder):
    name = "sbs"
    label = "ADS-B (SBS-1 / BaseStation)"
    options = {"affiliation": ("unknown", "affiliation assigned to aircraft")}

    def __init__(self, **opts):
        super().__init__(**opts)
        self.ac = {}

    def feed(self, data):
        self.buf += data
        out = []
        *lines, self.buf = self.buf.split(b"\n")
        for raw in lines:
            f = raw.decode("ascii", "ignore").strip().split(",")
            if len(f) < 16 or f[0] != "MSG":
                continue
            icao = f[4].strip().upper()
            if not icao:
                continue
            a = self.ac.setdefault(icao, {})
            if f[10].strip():
                a["callsign"] = f[10].strip()
            try:
                if f[12]:
                    a["speed"] = float(f[12]) * KNOT
                if f[13]:
                    a["heading"] = float(f[13])
                if f[11]:
                    a["alt"] = float(f[11]) * 0.3048
                if f[14] and f[15]:
                    o = Observation(uid=f"icao-{icao}", lat=float(f[14]), lon=float(f[15]),
                                    alt=a.get("alt"), heading=a.get("heading"), speed=a.get("speed"),
                                    name=a.get("callsign", icao), kind="air",
                                    affiliation=self.opts["affiliation"])
                    o.attrs["icao"] = icao
                    if o.valid():
                        out.append(o)
            except ValueError:
                continue
        return out


# ---------------------------------------------------------------------------
# Generic JSON / GeoJSON
# ---------------------------------------------------------------------------
def _dig(obj, path):
    """Follow a dotted path ('position.lat', 'coords.0') through dicts/lists."""
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class JsonDecoder(Decoder):
    name = "json"
    label = "JSON / GeoJSON (field mapping)"
    options = {
        "records": ("", "dotted path to the list of records (blank: root list / GeoJSON features / single object)"),
        "id": ("id", "field holding the track id"),
        "lat": ("lat", "latitude field"),
        "lon": ("lon", "longitude field"),
        "alt": ("alt", "altitude field (m)"),
        "heading": ("heading", "heading field (deg)"),
        "speed": ("speed", "speed field (m/s)"),
        "name": ("name", "callsign field"),
        "sidc": ("sidc", "optional SIDC field"),
        "video": ("video_url", "optional video URL field"),
        "kind": ("unknown", "platform kind for this feed"),
        "affiliation": ("unknown", "affiliation for this feed"),
    }

    def feed(self, data):
        self.buf += data
        out = []
        text = self.buf.decode("utf-8", "ignore").strip()
        if not text:
            return out
        docs = []
        try:
            docs.append(json.loads(text))
            self.buf = b""
        except ValueError:
            # newline-delimited JSON: keep the trailing partial line
            *lines, tail = self.buf.split(b"\n")
            self.buf = tail
            for line in lines:
                line = line.strip()
                if line:
                    try:
                        docs.append(json.loads(line.decode("utf-8", "ignore")))
                    except ValueError:
                        continue
            if len(self.buf) > 4 << 20:
                self.buf = b""
        for doc in docs:
            for rec in self._records(doc):
                o = self._one(rec)
                if o:
                    out.append(o)
        return out

    def _records(self, doc):
        if self.opts["records"]:
            doc = _dig(doc, self.opts["records"])
        if isinstance(doc, dict) and doc.get("type") == "FeatureCollection":
            return doc.get("features", [])
        if isinstance(doc, list):
            return doc
        return [doc] if isinstance(doc, dict) else []

    def _one(self, rec):
        if not isinstance(rec, dict):
            return None
        o_ = self.opts
        lat = lon = alt = None
        if rec.get("type") == "Feature":
            geom = rec.get("geometry") or {}
            if geom.get("type") == "Point" and len(geom.get("coordinates", [])) >= 2:
                lon, lat = _num(geom["coordinates"][0]), _num(geom["coordinates"][1])
                alt = _num(geom["coordinates"][2]) if len(geom["coordinates"]) > 2 else None
            props = dict(rec.get("properties") or {})
            props.setdefault("id", rec.get("id"))
            rec = props
        lat = lat if lat is not None else _num(_dig(rec, o_["lat"]))
        lon = lon if lon is not None else _num(_dig(rec, o_["lon"]))
        if lat is None or lon is None:
            return None
        uid = _dig(rec, o_["id"])
        name = _dig(rec, o_["name"])
        o = Observation(uid=str(uid if uid is not None else name or f"{lat:.5f},{lon:.5f}"), lat=lat, lon=lon,
                        alt=alt if alt is not None else _num(_dig(rec, o_["alt"])),
                        heading=_num(_dig(rec, o_["heading"])), speed=_num(_dig(rec, o_["speed"])),
                        name=str(name or uid or ""), kind=o_["kind"], affiliation=o_["affiliation"],
                        sidc=str(_dig(rec, o_["sidc"]) or ""), video_url=str(_dig(rec, o_["video"]) or ""))
        return o if o.valid() else None


DECODERS = {d.name: d for d in (CotDecoder, MavlinkDecoder, NmeaDecoder, SbsDecoder, JsonDecoder)}


def make_decoder(name, **opts):
    if name not in DECODERS:
        raise ValueError(f"unknown protocol {name!r}; available: {sorted(DECODERS)}")
    return DECODERS[name](**opts)


# ---------------------------------------------------------------------------
# Encoders used by the built-in simulator and by tests
# ---------------------------------------------------------------------------
def encode_cot(uid, lat, lon, cot_type="a-f-A-M-F-Q", callsign="", alt=0.0, course=0.0, speed=0.0,
               sensor=None, stale_s=30):
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    stale = datetime.fromtimestamp(time.time() + stale_s, timezone.utc)
    sens = (f'<sensor azimuth="{sensor[0]}" fov="{sensor[1]}" range="{sensor[2]}"/>' if sensor else "")
    return (f'<?xml version="1.0"?><event version="2.0" uid="{uid}" type="{cot_type}" how="m-g" '
            f'time="{now.strftime(fmt)}" start="{now.strftime(fmt)}" stale="{stale.strftime(fmt)}">'
            f'<point lat="{lat:.7f}" lon="{lon:.7f}" hae="{alt:.1f}" ce="10" le="10"/>'
            f'<detail><contact callsign="{callsign or uid}"/><track course="{course:.1f}" speed="{speed:.1f}"/>'
            f'{sens}</detail></event>').encode()


def encode_mavlink(msgid, payload, sysid=1, compid=1, seq=0, v2=True):
    if v2:
        hdr = bytes([0xFD, len(payload), 0, 0, seq & 0xFF, sysid, compid,
                     msgid & 0xFF, (msgid >> 8) & 0xFF, (msgid >> 16) & 0xFF])
    else:
        hdr = bytes([0xFE, len(payload), seq & 0xFF, sysid, compid, msgid])
    crc = mav_crc(hdr[1:] + payload, _MAV[msgid][0])
    return hdr + payload + struct.pack("<H", crc)


def mav_heartbeat(mav_type=2, sysid=1):
    return encode_mavlink(0, struct.pack("<IBBBBB", 0, mav_type, 3, 0, 4, 3), sysid)


def mav_global_position(lat, lon, alt_m, rel_m, vx=0.0, vy=0.0, hdg=0.0, sysid=1, v2=True):
    p = struct.pack("<IiiiihhhH", int(time.time() * 1000) & 0xFFFFFFFF, int(lat * 1e7), int(lon * 1e7),
                    int(alt_m * 1000), int(rel_m * 1000), int(vx * 100), int(vy * 100), 0, int(hdg * 100))
    return encode_mavlink(33, p, sysid, v2=v2)
