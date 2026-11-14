"""Transports: how bytes arrive.  Independent of what the bytes mean.

A connection = one transport + one decoder, so any protocol can ride on any
link (MAVLink over UDP or serial radio, CoT over multicast or TCP, NMEA from a
serial GPS or a TCP feed, JSON from an HTTP API or a file ...).

All transports are receive-only.
"""
import os
import socket
import struct
import threading
import time
import urllib.request


class Transport:
    name = "base"
    label = "Base"
    options = {}                     # option -> (default, help)

    def __init__(self, **opts):
        self.opts = {k: v[0] for k, v in self.options.items()}
        self.opts.update({k: v for k, v in opts.items() if v not in (None, "")})
        self._stop = threading.Event()
        self._thread = None
        self.on_data = None          # callable(bytes)
        self.on_status = None        # callable(state, detail)

    def start(self, on_data, on_status=None):
        self.on_data, self.on_status = on_data, on_status
        self._stop.clear()
        self._thread = threading.Thread(target=self._guard, name=f"badr-{self.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def _status(self, state, detail=""):
        if self.on_status:
            self.on_status(state, detail)

    def _guard(self):
        """Run the transport, reconnecting with back-off until stopped."""
        delay = 1.0
        while not self._stop.is_set():
            try:
                self._run()
                delay = 1.0
            except Exception as exc:
                self._status("error", f"{type(exc).__name__}: {exc}")
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 15.0)
        self._status("stopped")

    def _run(self):
        raise NotImplementedError

    def _close(self):
        pass


class UdpTransport(Transport):
    name = "udp"
    label = "UDP listen (unicast / multicast)"
    options = {"host": ("0.0.0.0", "local address to bind"),
               "port": (14550, "UDP port"),
               "group": ("", "multicast group to join, e.g. 239.2.3.1 (blank for unicast)")}
    sock = None

    def _run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.opts["host"] if not self.opts["group"] else "", int(self.opts["port"])))
        if self.opts["group"]:
            mreq = struct.pack("4sl", socket.inet_aton(self.opts["group"]), socket.INADDR_ANY)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.settimeout(0.5)
        self.sock = s
        self._status("listening", f"udp/{self.opts['port']}")
        try:
            while not self._stop.is_set():
                try:
                    data, _addr = s.recvfrom(65535)
                except socket.timeout:
                    continue
                if data:
                    self.on_data(data)
        finally:
            s.close()

    def _close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass


class TcpClientTransport(Transport):
    name = "tcp"
    label = "TCP client (connect to a server)"
    options = {"host": ("127.0.0.1", "server address"), "port": (8087, "TCP port")}
    sock = None

    def _run(self):
        s = socket.create_connection((self.opts["host"], int(self.opts["port"])), timeout=8)
        s.settimeout(0.5)
        self.sock = s
        self._status("connected", f"{self.opts['host']}:{self.opts['port']}")
        try:
            while not self._stop.is_set():
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError("closed by peer")
                self.on_data(data)
        finally:
            s.close()

    _close = UdpTransport._close


class TcpServerTransport(Transport):
    name = "tcp_server"
    label = "TCP server (devices connect to BADR)"
    options = {"host": ("0.0.0.0", "local address to bind"), "port": (8088, "TCP port")}
    sock = None

    def _run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.opts["host"], int(self.opts["port"])))
        srv.listen(8)
        srv.settimeout(0.5)
        self.sock = srv
        self._status("listening", f"tcp/{self.opts['port']}")
        try:
            while not self._stop.is_set():
                try:
                    conn, _addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._client, args=(conn,), daemon=True).start()
        finally:
            srv.close()

    def _client(self, conn):
        conn.settimeout(0.5)
        with conn:
            while not self._stop.is_set():
                try:
                    data = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not data:
                    return
                self.on_data(data)

    _close = UdpTransport._close


class SerialTransport(Transport):
    name = "serial"
    label = "Serial / USB / telemetry radio"
    options = {"port": ("/dev/ttyUSB0", "device, e.g. COM5 or /dev/ttyUSB0"), "baud": (57600, "baud rate")}
    ser = None

    def _run(self):
        try:
            import serial
        except ImportError as exc:
            self._stop.set()
            raise RuntimeError("pyserial is not installed (pip install pyserial)") from exc
        self.ser = serial.Serial(self.opts["port"], int(self.opts["baud"]), timeout=0.5)
        self._status("connected", f"{self.opts['port']} @ {self.opts['baud']}")
        try:
            while not self._stop.is_set():
                data = self.ser.read(4096)
                if data:
                    self.on_data(data)
        finally:
            self.ser.close()

    def _close(self):
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass


class HttpPollTransport(Transport):
    name = "http"
    label = "HTTP(S) poll (REST / JSON API)"
    options = {"url": ("http://127.0.0.1:8000/tracks.json", "URL to poll"),
               "interval": (5.0, "seconds between polls"),
               "header": ("", "optional header, 'Name: value' (e.g. an API token)")}

    def _run(self):
        self._status("polling", self.opts["url"])
        while not self._stop.is_set():
            req = urllib.request.Request(self.opts["url"], headers={"User-Agent": "badr-gis"})
            if ":" in str(self.opts["header"]):
                k, v = self.opts["header"].split(":", 1)
                req.add_header(k.strip(), v.strip())
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.on_data(resp.read() + b"\n")
            if self._stop.wait(float(self.opts["interval"])):
                break


class FileTransport(Transport):
    name = "file"
    label = "File replay / watched file"
    options = {"path": ("", "file to read"),
               "rate": (0.0, "replay rate in lines per second (0: read once, then follow appended data)")}

    def _run(self):
        path = os.path.expanduser(self.opts["path"])
        rate = float(self.opts["rate"])
        self._status("reading", os.path.basename(path))
        with open(path, "rb") as fh:
            while not self._stop.is_set():
                chunk = fh.readline() if rate > 0 else fh.read(65536)
                if chunk:
                    self.on_data(chunk)
                    if rate > 0:
                        time.sleep(1.0 / rate)
                elif rate > 0:
                    self._status("finished", os.path.basename(path))
                    self._stop.set()
                else:
                    time.sleep(0.5)


class MqttTransport(Transport):
    name = "mqtt"
    label = "MQTT subscribe (IoT sensors, UGS)"
    options = {"host": ("127.0.0.1", "broker address"), "port": (1883, "broker port"),
               "topic": ("sensors/#", "topic filter"), "username": ("", "optional"), "password": ("", "optional")}
    client = None

    def _run(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            self._stop.set()
            raise RuntimeError("paho-mqtt is not installed (pip install paho-mqtt)") from exc
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:           # paho-mqtt 1.x
            c = mqtt.Client()
        if self.opts["username"]:
            c.username_pw_set(self.opts["username"], self.opts["password"])
        c.on_message = lambda _c, _u, msg: self.on_data(msg.payload + b"\n")
        c.connect(self.opts["host"], int(self.opts["port"]), 30)
        c.subscribe(self.opts["topic"])
        self.client = c
        self._status("connected", f"{self.opts['host']} {self.opts['topic']}")
        while not self._stop.is_set():
            c.loop(timeout=0.5)
        c.disconnect()


TRANSPORTS = {t.name: t for t in (UdpTransport, TcpClientTransport, TcpServerTransport, SerialTransport,
                                  HttpPollTransport, FileTransport, MqttTransport)}


def make_transport(name, **opts):
    if name not in TRANSPORTS:
        raise ValueError(f"unknown transport {name!r}; available: {sorted(TRANSPORTS)}")
    return TRANSPORTS[name](**opts)
