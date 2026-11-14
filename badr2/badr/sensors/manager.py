"""Universal device manager.

    Connection  = transport (how bytes arrive)  +  decoder (what they mean)
    Device      = one tracked platform/sensor seen on a connection

Connections are plain dict profiles, so they can be saved, shared and created
by the AI.  Presets are only starting points for common set-ups - there is no
vendor-specific code path anywhere.
"""
import time
import uuid

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

from ..core import geodesy
from ..core.settings import Settings
from ..core.store import now_dtg
from .decoders import DECODERS, make_decoder
from .model import Observation
from .transports import TRANSPORTS, make_transport

PRESETS = {
    "MAVLink drone (UDP 14550)": {"transport": "udp", "transport_opts": {"port": 14550}, "decoder": "mavlink"},
    "MAVLink telemetry radio (serial)": {"transport": "serial", "transport_opts": {"baud": 57600}, "decoder": "mavlink"},
    "TAK / CoT multicast (SA)": {"transport": "udp", "transport_opts": {"port": 6969, "group": "239.2.3.1"},
                                 "decoder": "cot"},
    "TAK server stream (TCP)": {"transport": "tcp", "transport_opts": {"port": 8087}, "decoder": "cot"},
    "CoT over UDP": {"transport": "udp", "transport_opts": {"port": 4242}, "decoder": "cot"},
    "GPS receiver (serial NMEA)": {"transport": "serial", "transport_opts": {"baud": 9600}, "decoder": "nmea"},
    "NMEA over TCP": {"transport": "tcp", "transport_opts": {"port": 10110}, "decoder": "nmea"},
    "ADS-B receiver (SBS-1 TCP 30003)": {"transport": "tcp", "transport_opts": {"port": 30003}, "decoder": "sbs"},
    "REST / JSON API (poll)": {"transport": "http", "decoder": "json"},
    "MQTT sensor network (JSON)": {"transport": "mqtt", "decoder": "json", "decoder_opts": {"kind": "ugs"}},
    "Replay a log file": {"transport": "file", "transport_opts": {"rate": 5}, "decoder": "cot"},
}

STALE_S = 30.0
DROP_S = 600.0
TRAIL_POINTS = 240


class Device:
    def __init__(self, obs, conn_id):
        self.uid = obs.uid
        self.conn_id = conn_id
        self.name = obs.name or obs.uid
        self.obs = obs
        self.first_seen = obs.time
        self.count = 0
        self.trail = []
        self.video_url = obs.video_url
        self.alias = ""
        self.overrides = {}          # user-set kind / affiliation / sidc

    @property
    def age(self):
        return time.time() - self.obs.time

    @property
    def status(self):
        return "live" if self.age < STALE_S else "stale"


class Connection:
    def __init__(self, profile):
        self.profile = profile
        self.id = profile["id"]
        self.transport = None
        self.decoder = None
        self.state = "stopped"
        self.detail = ""
        self.bytes = 0
        self.messages = 0
        self.last_rx = 0.0


def normalise_profile(profile):
    p = dict(profile)
    p.setdefault("id", uuid.uuid4().hex[:8])
    p.setdefault("name", f"{p.get('decoder', '?')} via {p.get('transport', '?')}")
    p.setdefault("transport_opts", {})
    p.setdefault("decoder_opts", {})
    p.setdefault("enabled", True)
    if p.get("transport") not in TRANSPORTS:
        raise ValueError(f"unknown transport {p.get('transport')!r}; available: {sorted(TRANSPORTS)}")
    if p.get("decoder") not in DECODERS:
        raise ValueError(f"unknown protocol {p.get('decoder')!r}; available: {sorted(DECODERS)}")
    return p


class DeviceManager(QObject):
    devices_changed = pyqtSignal()
    connections_changed = pyqtSignal()
    observation = pyqtSignal(object)
    _rx = pyqtSignal(str, bytes)           # worker thread -> GUI thread
    _st = pyqtSignal(str, str, str)

    def __init__(self, workbench, parent=None):
        super().__init__(parent)
        self.wb = workbench
        self.connections = {}
        self.devices = {}
        self._rx.connect(self._on_bytes)
        self._st.connect(self._on_status)
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---- connections ---------------------------------------------------
    def load_saved(self, autostart=True):
        for prof in Settings.connections():
            try:
                self.add_connection(prof, save=False, start=autostart and prof.get("enabled", True))
            except Exception:
                continue

    def _save(self):
        Settings.set_connections([c.profile for c in self.connections.values()
                                  if not c.profile.get("transient")])

    def add_connection(self, profile, save=True, start=True):
        p = normalise_profile(profile)
        if p["id"] in self.connections:
            self.remove_connection(p["id"], save=False)
        conn = Connection(p)
        self.connections[p["id"]] = conn
        if start:
            self.start(p["id"])
        if save:
            self._save()
        self.connections_changed.emit()
        return p["id"]

    def remove_connection(self, conn_id, save=True):
        conn = self.connections.pop(conn_id, None)
        if conn is None:
            return False
        if conn.transport:
            conn.transport.stop()
        for uid in [u for u, d in self.devices.items() if d.conn_id == conn_id]:
            self._drop(uid)
        if save:
            self._save()
        self.connections_changed.emit()
        self.devices_changed.emit()
        return True

    def start(self, conn_id):
        conn = self.connections[conn_id]
        if conn.transport:
            conn.transport.stop()
        p = conn.profile
        conn.decoder = make_decoder(p["decoder"], **p["decoder_opts"])
        conn.transport = make_transport(p["transport"], **p["transport_opts"])
        conn.state, conn.detail = "starting", ""
        conn.transport.start(lambda data, cid=conn_id: self._rx.emit(cid, bytes(data)),
                             lambda state, detail="", cid=conn_id: self._st.emit(cid, state, detail))
        p["enabled"] = True
        self.connections_changed.emit()

    def stop(self, conn_id):
        conn = self.connections[conn_id]
        if conn.transport:
            conn.transport.stop()
            conn.transport = None
        conn.state = "stopped"
        conn.profile["enabled"] = False
        self.connections_changed.emit()

    def shutdown(self):
        self._timer.stop()
        for conn in self.connections.values():
            if conn.transport:
                conn.transport.stop()

    def _on_status(self, conn_id, state, detail):
        conn = self.connections.get(conn_id)
        if conn:
            conn.state, conn.detail = state, detail
            self.connections_changed.emit()

    def _on_bytes(self, conn_id, data):
        conn = self.connections.get(conn_id)
        if conn is None or conn.decoder is None:
            return
        conn.bytes += len(data)
        conn.last_rx = time.time()
        try:
            observations = conn.decoder.feed(data)
        except Exception as exc:      # a malformed packet must never take the app down
            conn.detail = f"decode error: {exc}"
            return
        for obs in observations:
            conn.messages += 1
            self.ingest(obs, conn_id)

    # ---- devices -------------------------------------------------------
    def ingest(self, obs: Observation, conn_id="manual"):
        """Fuse one observation into the live picture (GUI thread)."""
        if not obs.valid():
            return
        op = self.wb.operation
        dev = self.devices.get(obs.uid)
        new = dev is None
        if new:
            dev = Device(obs, conn_id)
            self.devices[obs.uid] = dev
        for k, v in dev.overrides.items():
            setattr(obs, k, v)
        dev.obs = obs
        dev.count += 1
        if obs.video_url:
            dev.video_url = obs.video_url
        if not dev.trail or geodesy.distance(dev.trail[-1][1], dev.trail[-1][0], obs.lat, obs.lon) > 3:
            dev.trail.append((obs.lon, obs.lat))
            del dev.trail[:-TRAIL_POINTS]
        name = dev.alias or obs.name or obs.uid
        conn = self.connections.get(conn_id)
        info = []
        if obs.alt is not None:
            info.append(f"{obs.alt:.0f}m")
        if "battery" in obs.attrs:
            info.append(f"BAT {obs.attrs['battery']}%")
        attrs = {"sidc": obs.symbol(), "name": name, "info": " ".join(info), "dtg": now_dtg(),
                 "heading": obs.heading, "speed": None if obs.speed is None else obs.speed * 3.6,
                 "alt": obs.alt, "device": conn.profile["name"] if conn else conn_id, "stale": 0}
        geom = QgsGeometry.fromPointXY(QgsPointXY(obs.lon, obs.lat))
        if new or not op.update("tracks", obs.uid, attrs, geom):
            attrs["uid"] = obs.uid
            op.add("tracks", geom, attrs, source="sensor")
        if len(dev.trail) >= 2:
            line = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in dev.trail])
            if not op.update("trails", obs.uid, None, line):
                op.add("trails", line, {"uid": obs.uid, "device": name}, source="sensor")
        ring = obs.footprint
        if ring is None and obs.fov:
            ring = geodesy.sector(obs.lat, obs.lon, obs.fov[2], obs.fov[0], obs.fov[1])
        if ring:
            poly = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in ring]])
            if not op.update("footprints", obs.uid, None, poly):
                op.add("footprints", poly, {"uid": obs.uid, "device": name, "name": name}, source="sensor")
        self._dirty = True
        self.observation.emit(obs)
        if new:
            self.devices_changed.emit()

    def _drop(self, uid):
        self.devices.pop(uid, None)
        op = self.wb.operation
        for key in ("tracks", "trails", "footprints"):
            op.remove(key, uid)

    def remove_device(self, uid):
        self._drop(uid)
        self.devices_changed.emit()

    def configure_device(self, uid, alias=None, kind=None, affiliation=None, sidc=None, video_url=None):
        """Operator overrides: what a track *is* is a human decision, not the link's."""
        dev = self.devices[uid]
        if alias is not None:
            dev.alias = alias
        if video_url is not None:
            dev.video_url = video_url
        for k, v in (("kind", kind), ("affiliation", affiliation), ("sidc", sidc)):
            if v:
                dev.overrides[k] = v
        self.ingest(dev.obs, dev.conn_id)
        self.devices_changed.emit()

    def _tick(self):
        op = self.wb.operation
        changed = False
        for uid, dev in list(self.devices.items()):
            if dev.age > DROP_S:
                self._drop(uid)
                changed = True
            elif dev.age > STALE_S:
                f = op.find("tracks", uid)
                if f is not None and f["stale"] != 1:
                    op.update("tracks", uid, {"stale": 1})
                    changed = True
        if changed or self._dirty:
            self._dirty = False
            self.devices_changed.emit()

    # ---- summaries -----------------------------------------------------
    def describe(self):
        conns = [{"id": c.id, "name": c.profile["name"], "transport": c.profile["transport"],
                  "protocol": c.profile["decoder"], "state": c.state, "detail": c.detail,
                  "messages": c.messages} for c in self.connections.values()]
        devs = [{"uid": d.uid, "name": d.alias or d.name, "kind": d.obs.kind, "affiliation": d.obs.affiliation,
                 "status": d.status, "lat": round(d.obs.lat, 6), "lon": round(d.obs.lon, 6),
                 "alt_m": d.obs.alt, "heading": d.obs.heading, "speed_mps": d.obs.speed,
                 "age_s": round(d.age, 1), "video": bool(d.video_url)} for d in self.devices.values()]
        return {"connections": conns, "devices": devs}
