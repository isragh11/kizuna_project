"""Tests de la Phase 6 : garde-fous des actionneurs et API (sans broker, sans réseau)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aquamind_core.safety import ActuatorCommand, ActuatorState, CommandJournal, SafetyLimits, check_command
from api.main import create_app

NOW = 1_000_000.0
LIM = SafetyLimits()


def cmd(target, action, source="dashboard", **kw):
    return ActuatorCommand(source=source, target=target, action=action, actor="test", **kw)


def ready_state(**kw) -> ActuatorState:
    st = ActuatorState(tank_m3=40.0, tank_updated=NOW - 5)
    st.valves["A"] = True
    for k, v in kw.items():
        setattr(st, k, v)
    return st


# ------------------------------------------------------------------ safety.check_command

def test_pump_on_allowed_when_safe():
    d = check_command(cmd("pump", "on", duration_min=20), ready_state(), LIM, NOW)
    assert d.accepted and d.duration_min == 20


@pytest.mark.parametrize("state_kw, duration, reason", [
    ({"tank_m3": 0.5}, 20, "cuve vide"),
    ({"tank_m3": None, "tank_updated": None}, 20, "inconnu"),
    ({"tank_updated": NOW - 600}, 20, "inconnu"),
    ({"valves": {"A": False, "B": False, "C": False}}, 20, "aucune vanne"),
    ({}, 500, "maximum"),
    ({"estop": True}, 20, "arrêt d'urgence"),
    ({"last_pump_toggle": NOW - 1}, 20, "court-cycle"),
])
def test_pump_on_refused_with_reason(state_kw, duration, reason):
    d = check_command(cmd("pump", "on", duration_min=duration), ready_state(**state_kw), LIM, NOW)
    assert not d.accepted and reason in d.reason


def test_whitelist_and_sources():
    assert not check_command(cmd("pump", "explode"), ready_state(), LIM, NOW).accepted
    assert not check_command(cmd("pump", "on", source="hacker"), ready_state(), LIM, NOW).accepted
    assert not check_command(cmd("valve", "open", valve_id="Z"), ready_state(), LIM, NOW).accepted


def test_emergency_stop_has_priority_and_release_needs_human_with_reason():
    st = ready_state(estop=True)
    assert check_command(cmd("estop", "engage", source="agent"), st, LIM, NOW).accepted   # toujours
    assert check_command(cmd("pump", "off", source="agent"), st, LIM, NOW).accepted       # arrêt permis
    assert not check_command(cmd("valve", "open", valve_id="B"), st, LIM, NOW).accepted
    assert not check_command(cmd("estop", "release", source="agent", reason="ok"), st, LIM, NOW).accepted
    assert not check_command(cmd("estop", "release", source="dashboard"), st, LIM, NOW).accepted
    assert check_command(cmd("estop", "release", source="dashboard", reason="vérifié"), st, LIM, NOW).accepted


def test_cannot_close_last_valve_while_pumping():
    st = ready_state(pump_on=True)
    assert not check_command(cmd("valve", "close", valve_id="A"), st, LIM, NOW).accepted
    st.valves["B"] = True
    assert check_command(cmd("valve", "close", valve_id="A"), st, LIM, NOW).accepted


def test_same_rules_whatever_the_source():
    for source in ("dashboard", "agent", "esp32"):
        assert not check_command(cmd("pump", "on", source=source, duration_min=20),
                                 ready_state(tank_m3=0.1), LIM, NOW).accepted


def test_journal_records_every_decision(tmp_path):
    j = CommandJournal(tmp_path / "log.jsonl")
    j.record(check_command(cmd("pump", "on", duration_min=20), ready_state(), LIM, NOW))
    j.record(check_command(cmd("pump", "on", duration_min=999), ready_state(), LIM, NOW))
    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and '"accepted": false' in lines[1] and '"source": "dashboard"' in lines[0]


# ------------------------------------------------------------------ API

@pytest.fixture(scope="module")
def client():
    app = create_app(start_background=False)
    with TestClient(app) as c:
        c.app_state = app.state.aqua
        yield c


def test_health_farm_scenarios_metrics(client):
    assert client.get("/health").json()["consumer"]["embedded"] is False
    farm = client.get("/farm", params={"day": "2025-07-20"}).json()
    assert {p["crop"]["name"] for p in farm["farm"]["plots"]} >= {"Tomate", "Piment"}
    assert any(s["key"] == "tunisia_heatwave" for s in client.get("/scenarios").json())
    m = client.get("/models/metrics").json()["summary"]
    assert m["live_detection"]["recommended_mode"] == "rule_adaptive"
    assert m["leak_battledim"]["use_for_alerting"] is False


def test_optimize_what_if_does_not_apply_but_apply_does(client):
    body = {"site": "tunisia", "date": "2025-07-20", "water_quota_m3": 30}
    r = client.post("/optimize", json=body)
    assert r.status_code == 200 and r.json()["limiting_factor"] == "water"
    assert client.get("/optimize/last").status_code == 404           # « Et si ? » n'applique rien
    assert client.post("/optimize", params={"apply": True}, json=body).status_code == 200
    assert client.get("/optimize/last").json()["kpis"]["quota_m3"] == 60
    assert ("cmd/replan", {"source": "POST /optimize (tunisia, 2025-07-20)"}, False) in client.app_state.bus.sent


def test_scenario_endpoint(client):
    r = client.get("/scenarios/tunisia_heatwave").json()
    assert r["result"]["satisfaction"]["B"] >= 0.29 and "horizon_value" in r


def test_sim_commands_need_broker(client):
    assert client.post("/sim/leak", json={"section": "S3", "rate_m3h": 1.5}).status_code == 503
    assert ("cmd/leak", {"section": "S3", "rate_m3h": 1.5}, False) in client.app_state.bus.sent


def test_actuator_endpoints_apply_safety_and_log(client):
    st = client.app_state
    r = client.post("/actuators/pump", json={"on": True, "duration_min": 10, "source": "agent"})
    assert r.status_code == 409 and "inconnu" in r.json()["reason"]               # cuve inconnue
    st.actuators.on_tank_level(30.0)
    assert client.post("/actuators/valve/A", json={"open": True}).status_code == 503  # accepté, non transmis
    r = client.post("/actuators/pump", json={"on": True, "duration_min": 500})
    assert r.status_code == 409 and "maximum" in r.json()["reason"]
    assert client.post("/emergency-stop", json={"engaged": True, "source": "agent"}).status_code == 503
    state = client.get("/actuators/state").json()
    assert state["estop"] is True and state["pump_on"] is False and not any(state["valves"].values())
    assert client.post("/actuators/valve/B", json={"open": True}).status_code == 409
    assert client.post("/emergency-stop", json={"engaged": False, "source": "agent", "reason": "x"}).status_code == 409
    log = client.get("/actuators/log").json()
    assert log[0]["command"]["source"] == "agent" and log[0]["accepted"] is False
    assert any(e["command"]["target"] == "estop" and e["accepted"] for e in log)
    sent = [t for t, _, _ in st.bus.sent]
    assert "actuator/estop/cmd" in sent and "actuator/pump/cmd" in sent


def test_websocket_hello_and_events(client):
    with client.websocket_connect("/ws/live") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and "actuators" in hello
        client.app_state.add_external_alert({"alert_id": "t1", "section": "S3", "message": "test"})
        msg = ws.receive_json()
        assert msg["type"] == "alert" and msg["alert_id"] == "t1"
    assert client.get("/alerts").json()[0]["alert_id"] == "t1"


# ------------------------------------------------------------------ corrections avant la Phase 7

def test_isolate_plot_always_possible_even_on_last_valve(client):
    st = client.app_state
    st.actuators.on_device_state("actuator/estop/state", {"engaged": False})
    st.actuators.on_device_state("actuator/pump/state", {"on": True, "mode": "auto"})
    for v in "ABC":
        st.actuators.on_device_state(f"actuator/valve/{v}/state", {"open": v == "B"})
    # fermer directement la dernière vanne ouverte pompe en marche : refusé…
    assert client.post("/actuators/valve/B", json={"open": False}).status_code == 409
    # …mais l'isolement atomique passe : pompe off puis vanne fermée, et une alerte est créée
    r = client.post("/actuators/isolate/B", json={"source": "dashboard", "actor": "test", "reason": "fuite S3"})
    body = r.json()
    assert body["accepted"] is True and [s["command"]["target"] for s in body["steps"]] == ["pump", "valve"]
    state = client.get("/actuators/state").json()
    assert state["pump_on"] is False and state["valves"]["B"] is False
    assert client.get("/alerts").json()[0]["detector"] == "isolation"


def test_health_reports_staleness(client):
    h = client.get("/health").json()
    assert "last_reading_age_s" in h and "simulator" in h and isinstance(h["reasons"], list)


def test_sensitivity_endpoint(client):
    r = client.get("/models/sensitivity").json()
    assert len(r["rows"]) == 7 and r["label"] == "estimation simulée"
