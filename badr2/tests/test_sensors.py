import json
import socket
import time

import pytest

from badr.sensors import decoders as D
from badr.sensors.model import Observation


def test_cot_decoder_handles_split_and_concatenated_events():
    a = D.encode_cot("UAS-7", 34.01, 71.52, "a-f-A-M-F-Q", "HAWK 7", alt=1200, course=90, speed=22,
                     sensor=(180, 30, 800))
    b = D.encode_cot("PTL-1", 34.02, 71.53, "a-f-G-U-C-I", "PATROL 1")
    dec = D.CotDecoder()
    stream = a + b
    out = dec.feed(stream[:100]) + dec.feed(stream[100:])
    assert [o.uid for o in out] == ["UAS-7", "PTL-1"]
    uas, ptl = out
    assert (uas.kind, uas.affiliation, uas.name) == ("uas", "friend", "HAWK 7")
    assert uas.alt == 1200 and uas.heading == 90 and uas.fov == (180.0, 30.0, 800.0)
    assert ptl.kind == "infantry"
    assert uas.symbol()[3] == "3" and uas.symbol()[4:6] == "01"


def test_cot_decoder_ignores_garbage_and_hostile_type():
    dec = D.CotDecoder()
    assert dec.feed(b"\x00\x01 not xml <event broken") == []
    o = dec.feed(b"...>" + D.encode_cot("X", 1.5, 2.5, "a-h-G"))
    assert o == [] or o[0].affiliation == "hostile"
    assert D.cot_type_to_kind("a-h-G-E-V")[0] == "hostile"


@pytest.mark.parametrize("v2", [True, False])
def test_mavlink_decoder_v1_and_v2_with_noise(v2):
    dec = D.MavlinkDecoder(affiliation="friend", name_prefix="RQ")
    hb = D.mav_heartbeat(mav_type=2, sysid=5)
    pos = D.mav_global_position(34.123456, 71.654321, 1500, 120, vx=10, vy=0, hdg=45, sysid=5, v2=v2)
    stream = b"\xfd\xfe\x03junk" + hb + b"\x00\xff" + pos
    out = []
    for i in range(0, len(stream), 7):          # arbitrary fragmentation
        out += dec.feed(stream[i:i + 7])
    assert len(out) == 1
    o = out[0]
    assert o.uid == "mav-5" and o.name == "RQ-5" and o.kind == "uas"
    assert abs(o.lat - 34.123456) < 1e-6 and abs(o.lon - 71.654321) < 1e-6
    assert o.alt == 1500 and o.heading == 45 and abs(o.speed - 10) < 0.01


def test_mavlink_rejects_corrupt_frame_and_gcs():
    dec = D.MavlinkDecoder()
    pos = bytearray(D.mav_global_position(34.0, 71.0, 100, 50))
    pos[12] ^= 0xFF
    assert dec.feed(bytes(pos)) == []
    dec2 = D.MavlinkDecoder()
    dec2.feed(D.mav_heartbeat(mav_type=6, sysid=255))
    assert dec2.feed(D.mav_global_position(34.0, 71.0, 100, 50, sysid=255)) == []


def test_nmea_decoder_checksum_and_units():
    dec = D.NmeaDecoder(uid="veh-1", name="VIC 1")
    good = b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n"
    bad = b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*00\r\n"
    out = dec.feed(bad + good)
    assert len(out) == 1
    assert abs(out[0].lat - 48.1173) < 1e-4 and abs(out[0].lon - 11.51667) < 1e-4
    assert abs(out[0].speed - 22.4 * 0.514444) < 1e-3 and out[0].heading == 84.4


def test_sbs_decoder_merges_callsign_and_position():
    dec = D.SbsDecoder()
    ident = b"MSG,1,1,1,4CA2D6,1,2026/11/14,10:00:00.000,2026/11/14,10:00:00.000,RYR123  ,,,,,,,,,,,0\n"
    pos = b"MSG,3,1,1,4CA2D6,1,2026/11/14,10:00:01.000,2026/11/14,10:00:01.000,,36000,,,51.4706,-0.4619,,,0,0,0,0\n"
    out = dec.feed(ident + pos)
    assert len(out) == 1 and out[0].name == "RYR123" and abs(out[0].alt - 10972.8) < 0.1


def test_json_decoder_mapping_ndjson_and_geojson():
    dec = D.JsonDecoder(records="data.items", id="serial", lat="pos.lat", lon="pos.lon", name="label", kind="ugs")
    doc = {"data": {"items": [{"serial": "S1", "label": "Seismic 1", "pos": {"lat": 34.1, "lon": 71.1}},
                              {"serial": "S2", "pos": {"lat": None, "lon": 71.1}}]}}
    out = dec.feed(json.dumps(doc).encode())
    assert [(o.uid, o.name, o.kind) for o in out] == [("S1", "Seismic 1", "ugs")]
    nd = D.JsonDecoder()
    out = nd.feed(b'{"id":"a","lat":1.5,"lon":2.5}\n{"id":"b","lat":3.5,"lo')
    assert [o.uid for o in out] == ["a"]
    out = nd.feed(b'n":4.5}\n')
    assert [o.uid for o in out] == ["b"]
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "f1", "geometry": {"type": "Point", "coordinates": [71.2, 34.2, 500]},
         "properties": {"name": "Tower cam"}}]}
    o = D.JsonDecoder().feed(json.dumps(gj).encode())[0]
    assert (o.uid, o.lat, o.lon, o.alt, o.name) == ("f1", 34.2, 71.2, 500.0, "Tower cam")


def test_observation_validity():
    assert not Observation("x", 0, 0).valid()
    assert not Observation("x", 95, 10).valid()
    assert Observation("x", 10, 10).valid()


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pump(qgis_app, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qgis_app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_end_to_end_udp_connection_updates_live_layers(qgis_app, tmp_path):
    """Real sockets: a CoT feed and a MAVLink feed on two UDP ports, one picture."""
    from badr.core.workbench import Workbench
    from badr.sensors.manager import DeviceManager
    wb = Workbench(); wb.open_operation(str(tmp_path / "op"))
    mgr = DeviceManager(wb)
    p_cot, p_mav = _free_udp_port(), _free_udp_port()
    c1 = mgr.add_connection({"name": "TAK", "transport": "udp", "transport_opts": {"host": "127.0.0.1", "port": p_cot},
                             "decoder": "cot", "transient": True}, save=False)
    c2 = mgr.add_connection({"name": "Drone", "transport": "udp", "transport_opts": {"host": "127.0.0.1", "port": p_mav},
                             "decoder": "mavlink", "transient": True}, save=False)
    assert _pump(qgis_app, lambda: all(c.state == "listening" for c in mgr.connections.values()))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(3):
        tx.sendto(D.encode_cot("PTL-1", 34.02 + i * 0.001, 71.53, "a-f-G-U-C-I", "PATROL 1"), ("127.0.0.1", p_cot))
        tx.sendto(D.mav_heartbeat(2, 9) + D.mav_global_position(34.05, 71.5 + i * 0.001, 900, 100, hdg=270, sysid=9),
                  ("127.0.0.1", p_mav))
        time.sleep(0.05)
    assert _pump(qgis_app, lambda: len(mgr.devices) == 2 and all(d.count == 3 for d in mgr.devices.values()))
    tracks = wb.operation.layer("tracks")
    assert tracks.featureCount() == 2                      # upserted, not appended
    names = sorted(f["name"] for f in tracks.getFeatures())
    assert names == ["PATROL 1", "UAS-9"]
    assert wb.operation.layer("trails").featureCount() == 2
    d = mgr.describe()
    assert {c["protocol"] for c in d["connections"]} == {"cot", "mavlink"}
    assert all(dev["status"] == "live" for dev in d["devices"])

    mgr.configure_device("mav-9", alias="RAVEN", affiliation="hostile")
    f = wb.operation.find("tracks", "mav-9")
    assert f["name"] == "RAVEN" and f["sidc"][3] == "6"

    mgr.remove_connection(c1, save=False)
    assert tracks.featureCount() == 1
    mgr.remove_connection(c2, save=False)
    mgr.shutdown()


def test_sensor_fov_becomes_footprint(qgis_app, tmp_path):
    from badr.core.workbench import Workbench
    from badr.sensors.manager import DeviceManager
    wb = Workbench(); wb.open_operation(None)
    mgr = DeviceManager(wb)
    mgr.ingest(Observation("cam", 34.0, 71.0, kind="sensor", fov=(90, 40, 600)))
    assert wb.operation.layer("footprints").featureCount() == 1
    mgr.shutdown()


def test_bad_profiles_are_rejected():
    from badr.sensors.manager import normalise_profile
    with pytest.raises(ValueError):
        normalise_profile({"transport": "carrier-pigeon", "decoder": "cot"})
    with pytest.raises(ValueError):
        normalise_profile({"transport": "udp", "decoder": "proprietary"})
