"""Appareil « pompe + vannes » (ESP32 Wokwi / jumeau Python) : protocole, sécurité locale, confirmations
côté API, demandes des boutons, appareil hors-ligne, exécuteur de plan, simulateur --device wokwi, pont."""

from __future__ import annotations

import random
from datetime import datetime

import pytest

from aquamind_core.safety import ActuatorCommand, CommandJournal, SafetyLimits, check_command, ActuatorState
from api.actuators import CONFIRM_TIMEOUT_S, ActuatorController
from iot import protocol
from iot.device_twin import DeviceLogic


# ----------------------------------------------------------------------------------------- protocole

def test_bridge_directions_are_disjoint_and_strict():
    assert protocol.allowed("down", "actuator/pump/cmd") and protocol.allowed("down", "actuator/valve/B/cmd")
    assert protocol.allowed("up", "actuator/pump/state") and protocol.allowed("up", "pipe/S3/flow")
    assert not protocol.allowed("up", "actuator/pump/cmd")          # un inconnu ne peut pas commander
    assert not protocol.allowed("down", "actuator/pump/state")      # pas de boucle
    assert not protocol.allowed("up", "actuator/valve/Z/state")
    assert not protocol.allowed("up", "pipe/S9/flow")
    assert not protocol.allowed("up", "plot/B/soil")                # seul le sol de A vient de l'ESP32
    assert not protocol.allowed("down", "cmd/speed")
    for t in protocol.DOWN_TOPICS:
        assert not any(protocol._match(u, t.replace("+", "A")) for u in protocol.UP_TOPICS)


def test_decode_rejects_garbage_and_oversize():
    assert protocol.decode(b'{"on": true}') == {"on": True}
    assert protocol.decode(b"[1, 2]") is None and protocol.decode(b"pas du json") is None
    assert protocol.decode(b'{"x": "' + b"a" * 5000 + b'"}') is None
    assert protocol.relative("aqm-1234abcd/actuator/pump/cmd", "aqm-1234abcd") == "actuator/pump/cmd"
    assert protocol.relative("autre/actuator/pump/cmd", "aqm-1234abcd") is None


def test_device_flows_mass_balance_and_leak():
    f = protocol.device_flows(6.0, True, {"A": True, "B": True, "C": False}, False, noise=False)
    s = {k: v["flow_m3h"] for k, v in f.items()}
    assert s["S1"] == pytest.approx(s["S2"] + s["S3"] + s["S4"] + s["S5"], abs=1e-3)
    assert s["S2"] == pytest.approx(6.0 * 0.92 / 2, abs=1e-3) and s["S4"] == 0.0
    leak = protocol.device_flows(6.0, True, {"A": True, "B": True, "C": False}, False, leak=("S2", 1.5), noise=False)
    l = {k: v["flow_m3h"] for k, v in leak.items()}
    assert l["S1"] - (l["S2"] + l["S3"] + l["S4"] + l["S5"]) == pytest.approx(1.5 * 1.25, abs=1e-3)
    off = protocol.device_flows(6.0, True, {"A": False, "B": False, "C": False}, False, noise=False)
    assert all(v["flow_m3h"] == 0 for v in off.values())               # pas de vanne : rien ne coule
    assert all(v["flow_m3h"] == 0 for v in protocol.device_flows(6.0, True, {"A": True}, True, noise=False).values())


# ------------------------------------------------------------------------- sécurité locale (jumeau)

def _rel(msgs, suffix):
    return [m for m in msgs if m[0].endswith(suffix)]


def test_device_refuses_pump_without_valve_and_last_valve_close():
    d = DeviceLogic()
    out = d.on_message("actuator/pump/cmd", {"on": True, "command_id": "c1", "source": "dashboard"}, 0.0)
    assert out[0][1]["result"] == "refused" and "vanne" in out[0][1]["reason"] and not d.pump_on
    d.on_message("actuator/valve/A/cmd", {"open": True, "command_id": "c2"}, 0.0)
    out = d.on_message("actuator/pump/cmd", {"on": True, "duration_s": 60, "command_id": "c3"}, 0.0)
    assert out[0][1]["result"] == "ok" and out[0][1]["command_id"] == "c3" and d.pump_on
    out = d.on_message("actuator/valve/A/cmd", {"open": False, "command_id": "c4"}, 1.0)
    assert out[0][1]["result"] == "refused" and d.valves["A"]           # dernière vanne, pompe en marche


def test_device_max_duration_dry_tank_and_api_silence():
    d = DeviceLogic()
    d.on_message("status/api", {"alive": True}, 0.0)
    d.on_message("actuator/valve/B/cmd", {"open": True}, 0.0)
    d.on_message("actuator/pump/cmd", {"on": True, "duration_s": 10}, 0.0)
    out = d.poll(11.0)
    assert not d.pump_on and any(m[1].get("reason", "").startswith("durée maximale") for m in out)
    d.on_message("actuator/pump/cmd", {"on": True, "duration_s": 999999}, 12.0)
    assert d.pump_until - 12.0 == protocol.MAX_PUMP_S                    # plafonné à 2 h
    d.on_message("tank/level", {"level_m3": 0.1}, 12.0)
    d.poll(12.5)
    assert not d.pump_on                                                  # cuve vide
    d.on_message("tank/level", {"level_m3": 40}, 13.0)
    d.on_message("actuator/pump/cmd", {"on": True}, 13.0)
    out = d.poll(31.0)                                                    # API muette depuis 31 s
    assert not d.pump_on and not any(d.valves.values()) and not d.api_ok
    assert any("API muette" in m[1].get("reason", "") for m in out)


def test_physical_estop_cuts_locally_first_then_asks_the_api():
    d = DeviceLogic()
    d.on_message("actuator/valve/C/cmd", {"open": True}, 0.0)
    d.on_message("actuator/pump/cmd", {"on": True}, 0.0)
    out = d.press_estop()
    assert d.estop and not d.pump_on and not any(d.valves.values())
    states, reqs = _rel(out, "/state"), _rel(out, "actuator/request")
    assert states[0][0] == "actuator/estop/state" and states[0][1]["engaged"] and states[0][1]["result"] == "local"
    assert reqs[0][1]["target"] == "estop" and reqs[0][1]["action"] == "engage"
    assert d.on_message("actuator/valve/A/cmd", {"open": True, "command_id": "x"}, 1.0)[0][1]["result"] == "refused"
    d.on_message("actuator/estop/cmd", {"engaged": False, "command_id": "r"}, 2.0)       # relâché par l'API
    assert not d.estop


def test_pump_button_stops_locally_but_starts_through_api():
    d = DeviceLogic()
    out = d.press_pump()
    assert not d.pump_on and out[0][0] == "actuator/request" and out[0][1]["action"] == "on"
    d.on_message("actuator/valve/A/cmd", {"open": True}, 0.0)
    d.on_message("actuator/pump/cmd", {"on": True}, 0.0)
    out = d.press_pump()
    assert not d.pump_on and out[0][1]["result"] == "local" and out[1][1]["action"] == "off"


def test_scheduler_commands_keep_auto_mode_and_leak_button_targets_open_branch():
    d = DeviceLogic()
    d.on_message("actuator/valve/B/cmd", {"open": True, "source": "scheduler"}, 0.0)
    assert d.mode == "auto"
    d.on_message("actuator/valve/A/cmd", {"open": True, "source": "dashboard"}, 0.0)
    assert d.mode == "manual"
    d.press_leak()
    assert d.leak == ("S2", 1.5)                                           # branche de A (première ouverte)
    d.press_leak()
    assert d.leak is None
    msgs = d.flows("2025-07-20T10:00:00")
    assert len(_rel(msgs, "/flow")) == 5 and msgs[-1][0] == "plot/A/soil"


# ------------------------------------------------------------------------ API : confirmations

class Bus:
    def __init__(self):
        self.sent = []

    def publish(self, rel, body, qos=1, retain=False):
        self.sent.append((rel, body))
        return True


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def ctl():
    clock = Clock()
    events = []
    c = ActuatorController(Bus(), journal=CommandJournal(None), notify=events.append, clock=clock, mono=clock,
                           offline_after_s=15)
    c.events, c.clk = events, clock
    c.state.tank_m3, c.state.tank_updated = 40.0, clock.t
    return c


def test_command_is_confirmed_with_measured_latency(ctl):
    e = ctl.command(ActuatorCommand(source="dashboard", target="valve", action="open", valve_id="A"))
    cid = e["command"]["command_id"]
    assert ctl.snapshot()["inflight"][0]["command_id"] == cid
    ctl.clk.t += 0.62
    ctl.on_device_state("actuator/valve/A/state", {"device": "esp32-wokwi", "open": True, "command_id": cid,
                                                    "result": "ok", "mode": "manual"})
    conf = [ev for ev in ctl.events if ev["type"] == "actuator_confirm"][-1]
    assert conf["result"] == "ok" and conf["latency_ms"] == 620 and conf["command_id"] == cid
    snap = ctl.snapshot()
    assert snap["inflight"] == [] and snap["confirmed"]["valves"]["A"] is True


def test_device_refusal_and_timeout(ctl):
    ctl.state.valves["A"] = True
    e = ctl.command(ActuatorCommand(source="dashboard", target="pump", action="on", duration_min=5))
    ctl.on_device_state("actuator/pump/state", {"device": "esp32-wokwi", "on": False, "result": "refused",
                                                "reason": "cuve vide : marche à sec interdite",
                                                "command_id": e["command"]["command_id"]})
    conf = [ev for ev in ctl.events if ev["type"] == "actuator_confirm"][-1]
    assert conf["result"] == "refused" and "cuve vide" in conf["reason"] and ctl.state.pump_on is False
    e2 = ctl.command(ActuatorCommand(source="dashboard", target="valve", action="open", valve_id="B"))
    ctl.clk.t += CONFIRM_TIMEOUT_S + 0.5
    ctl.tick()
    conf = [ev for ev in ctl.events if ev["type"] == "actuator_confirm"][-1]
    assert conf["result"] == "timeout" and conf["command_id"] == e2["command"]["command_id"]


def test_button_requests_go_through_safety_with_esp32_local_source(ctl):
    entry = ctl.on_request({"request_id": "r1", "device": "esp32-wokwi", "target": "pump", "action": "on",
                            "duration_min": 30, "source": "dashboard"})       # la source annoncée est ignorée
    assert entry["command"]["source"] == "esp32_local" and not entry["accepted"]   # aucune vanne ouverte
    reply = [b for t, b in ctl.bus.sent if t == "actuator/request/result"][-1]
    assert reply["request_id"] == "r1" and reply["accepted"] is False
    ctl.on_request({"request_id": "r2", "target": "estop", "action": "release", "reason": "ok"})
    assert [b for t, b in ctl.bus.sent if t == "actuator/request/result"][-1]["accepted"] is False
    e = ctl.on_request({"request_id": "r3", "target": "estop", "action": "engage"})
    assert e["accepted"] and ctl.state.estop
    released = check_command(ActuatorCommand(source="esp32_local", target="estop", action="release", reason="x"),
                             ctl.state, SafetyLimits(), 0)
    assert not released.accepted                                           # un bouton ne relâche jamais


def test_physical_estop_state_is_shown_immediately(ctl):
    ctl.on_device_state("actuator/estop/state", {"device": "esp32-wokwi", "engaged": True, "result": "local",
                                                 "reason": "arrêt d'urgence physique", "source": "esp32_local"})
    assert ctl.state.estop is True
    ev = [e for e in ctl.events if e["type"] == "actuator_confirm"][-1]
    assert ev["result"] == "local" and ev["target"] == "estop"


def test_heartbeat_online_offline_and_labels(ctl):
    assert ctl.device_status()["kind"] == "inconnu"
    ctl.on_heartbeat({"device": "esp32-wokwi", "uptime_s": 3})
    st = ctl.device_status()
    assert st["kind"] == "esp32" and st["online"] and st["label"] == "ESP32 Wokwi"
    ctl.clk.t += 16
    ctl.tick()
    assert not ctl.device_status()["online"]
    assert any(e["type"] == "device" and e["online"] is False for e in ctl.events)
    ctl.on_heartbeat({"device": "esp32-wokwi"})
    assert ctl.device_status()["online"]
    ctl.on_device_status({"online": False})                                # dernière volonté MQTT
    assert not ctl.device_status()["online"]
    # retour au simulateur Python : ESP32 muet et états venus du simulateur → plus de bandeau hors-ligne
    ctl.clk.t += 20
    ctl.on_device_state("actuator/pump/state", {"device": "simulateur", "on": False})
    st = ctl.device_status()
    assert st["kind"] == "simulateur" and st["online"] and st["label"] == "simulateur Python"


def test_scheduler_keeps_auto_mode_but_dashboard_takes_over(ctl):
    ctl.command(ActuatorCommand(source="scheduler", target="valve", action="open", valve_id="A"))
    assert ctl.state.mode == "auto"
    ctl.command(ActuatorCommand(source="dashboard", target="valve", action="open", valve_id="B"))
    assert ctl.state.mode == "manual"


# ------------------------------------------------------------------------------- exécuteur de plan

def test_plan_executor_drives_esp32_in_auto_mode(ctl):
    from types import SimpleNamespace

    from api.plan_executor import PlanExecutor

    plan = SimpleNamespace(horizon_h=24, allocation={"A": [0.0] * 8 + [3.0] * 4 + [0.0] * 12,
                                                     "B": [0.0] * 24, "C": [0.0] * 24},
                           pump_m3=[0.0] * 24, inputs={"start": "2025-07-20", "pv_day_factors": [1.0],
                                                       "et0_used_mm": [5.0], "tank_initial_m3": 40}, duals={})
    sim = {"ts": "2025-07-20T09:00:00"}
    ex = PlanExecutor(ctl, lambda: plan, lambda: sim["ts"], mono=ctl.clk)
    ex.tick()
    assert not ctl.state.valves["A"]                                       # pas d'ESP32 en ligne : inactif
    ctl.on_heartbeat({"device": "esp32-wokwi"})
    ex.tick()
    sources = [e["command"]["source"] for e in ctl.events if e["type"] == "actuator_command"]
    assert ctl.state.valves["A"] and ctl.state.pump_on and set(sources) == {"scheduler"}
    assert ctl.state.mode == "auto"
    ctl.clk.t += 10
    ctl.state.last_pump_toggle -= 10
    sim["ts"] = "2025-07-20T13:00:00"                                      # plus rien à arroser
    ex.tick()
    assert not ctl.state.pump_on
    ctl.command(ActuatorCommand(source="dashboard", target="valve", action="open", valve_id="C"))
    n = len(ctl.events)
    ctl.clk.t += 10
    ex.tick()
    assert len(ctl.events) == n                                            # mode manuel : l'IA n'agit plus


# --------------------------------------------------------------------- simulateur --device wokwi

def test_simulator_in_wokwi_mode_uses_device_flows():
    from aquamind_core.config import load_farm
    from simulator.physics import FarmSimulator
    from simulator.plan import default_plan

    farm = load_farm()
    sim = FarmSimulator(farm, default_plan(farm, datetime(2025, 7, 20).date()), seed=1)
    sim.external_flows, sim.external_plots = {}, {"A"}
    ts = datetime(2025, 7, 20, 12, 0)
    tank0 = sim.state.tank_m3
    msgs = sim.step(ts)
    topics = [t for t, _ in msgs]
    assert not any(t.startswith("pipe/") for t in topics) and "plot/A/soil" not in topics
    assert "plot/B/soil" in topics and "tank/level" in topics
    sim.external_flows = {"S1": (8.0, 8.0), "S2": (7.36, 7.36), "S5": (0.64, 0.64)}
    t1 = sim.state.tank_m3
    sim.step(ts)
    assert sim.state.tank_m3 < t1 or tank0 == farm.tank_capacity_m3      # l'eau sortie par l'ESP32 vide la cuve


def test_simulator_confirms_and_refuses_like_a_device():
    from aquamind_core.config import load_farm
    from simulator.physics import FarmSimulator
    from simulator.plan import default_plan

    farm = load_farm()
    sim = FarmSimulator(farm, default_plan(farm, datetime(2025, 7, 20).date()), seed=1)
    ts = datetime(2025, 7, 20, 12, 0)
    assert sim.actuate("valve", {"open": True}, ts, "A") == (True, "")
    sim.actuate("estop", {"engaged": True}, ts)
    ok, why = sim.actuate("pump", {"on": True}, ts)
    assert not ok and "arrêt d'urgence" in why


# ------------------------------------------------------------------------------------------- pont

def test_bridge_relays_only_allowed_topics_and_throttles():
    from types import SimpleNamespace

    from iot.bridge_public import Bridge, TokenBucket

    b = Bridge("aqm-0123456789abcdef", "aquamind/e2e", "localhost", 1883, "broker.hivemq.com", 8883)
    sent_pub, sent_loc = [], []
    b.public.publish = lambda t, p, qos=0, retain=False: sent_pub.append((t, retain))
    b.local.publish = lambda t, p, qos=0, retain=False: sent_loc.append((t, retain))
    msg = lambda topic, payload=b'{"a": 1}', retain=False: SimpleNamespace(topic=topic, payload=payload, retain=retain)
    b._from_local(None, None, msg("aquamind/e2e/actuator/estop/cmd", retain=True))
    b._from_local(None, None, msg("aquamind/e2e/plot/B/soil"))                    # pas pour l'appareil
    b._from_public(None, None, msg("aqm-0123456789abcdef/actuator/pump/state"))
    b._from_public(None, None, msg("aqm-0123456789abcdef/actuator/pump/cmd"))     # un inconnu ne commande pas
    b._from_public(None, None, msg("aqm-0123456789abcdef/pipe/S1/flow", b"pas du json"))
    assert sent_pub == [("aqm-0123456789abcdef/actuator/estop/cmd", True)]
    assert sent_loc == [("aquamind/e2e/actuator/pump/state", False)]
    assert b.stats["rejected"] == 1
    # sous un flot de mesures, les états et demandes passent toujours ; seules les mesures sont limitées
    b.bucket = TokenBucket(rate=0, burst=0, clock=lambda: 0.0)
    sent_loc.clear()
    b._from_public(None, None, msg("aqm-0123456789abcdef/pipe/S1/flow"))
    b._from_public(None, None, msg("aqm-0123456789abcdef/actuator/estop/state", retain=True))
    b._from_public(None, None, msg("aqm-0123456789abcdef/actuator/request"))
    b._from_public(None, None, msg("aqm-0123456789abcdef/device/heartbeat"))
    assert [t for t, _ in sent_loc] == ["aquamind/e2e/actuator/estop/state", "aquamind/e2e/actuator/request",
                                        "aquamind/e2e/device/heartbeat"] and b.stats["throttled"] == 1
    t = [0.0]
    bucket = TokenBucket(rate=2, burst=2, clock=lambda: t[0])
    assert [bucket.take() for _ in range(3)] == [True, True, False]
    t[0] = 1.0
    assert bucket.take()
    with pytest.raises(ValueError):
        Bridge("court", "x", "h", 1, "h", 1)
