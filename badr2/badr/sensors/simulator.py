"""Training feed: synthetic platforms sent as real packets over loopback UDP.

Nothing is injected into the picture directly - a MAVLink stream and a CoT
stream are transmitted to two ordinary connections, so the simulator exercises
exactly the path a real drone or TAK feed takes.
"""
import math
import socket
import threading
import time

from ..core import geodesy
from . import decoders as D


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Simulator:
    def __init__(self, manager, lat, lon, rate_hz=2.0):
        self.mgr, self.lat, self.lon, self.rate = manager, lat, lon, rate_hz
        self._stop = threading.Event()
        self.ids = []
        self.ports = (_free_port(), _free_port())

    def start(self):
        for name, port, dec, opts in (
                ("SIM UAS (MAVLink)", self.ports[0], "mavlink", {"name_prefix": "RAVEN"}),
                ("SIM patrol (CoT)", self.ports[1], "cot", {})):
            self.ids.append(self.mgr.add_connection({
                "name": name, "transport": "udp", "transport_opts": {"host": "127.0.0.1", "port": port},
                "decoder": dec, "decoder_opts": opts, "transient": True}, save=False))
        threading.Thread(target=self._run, name="badr-sim", daemon=True).start()

    def frames(self, t):
        """Packets for simulation time ``t`` seconds -> (mavlink_bytes, cot_bytes)."""
        ang = (t * 3.0) % 360                                   # UAS: 1.5 km orbit
        la, lo = geodesy.destination(self.lat, self.lon, ang, 1500)
        hdg = (ang + 90) % 360
        vx, vy = 22 * math.cos(math.radians(hdg)), 22 * math.sin(math.radians(hdg))
        mav = D.mav_heartbeat(2, 7) + D.mav_global_position(la, lo, 1450, 600, vx, vy, hdg, sysid=7)
        d = (t * 1.4) % 4000                                    # patrol: out and back on a 2 km leg
        d = d if d < 2000 else 4000 - d
        pla, plo = geodesy.destination(self.lat, self.lon, 235, 400 + d)
        cot = D.encode_cot("SIM-PTL-1", pla, plo, "a-f-G-U-C-I", "PATROL 1", alt=0,
                           course=235 if (t * 1.4) % 4000 < 2000 else 55, speed=1.4)
        cot += D.encode_cot("SIM-CAM-1", *geodesy.destination(self.lat, self.lon, 20, 900), "a-f-G-E-S",
                            "TOWER CAM", sensor=((t * 6) % 360, 35, 700))
        return mav, cot

    def _run(self):
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        t0 = time.time()
        while not self._stop.wait(1.0 / self.rate):
            mav, cot = self.frames(time.time() - t0)
            try:
                tx.sendto(mav, ("127.0.0.1", self.ports[0]))
                for ev in cot.split(b"<?xml")[1:]:
                    tx.sendto(b"<?xml" + ev, ("127.0.0.1", self.ports[1]))
            except OSError:
                break
        tx.close()

    def stop(self):
        self._stop.set()
        for cid in self.ids:
            self.mgr.remove_connection(cid, save=False)
        self.ids = []
