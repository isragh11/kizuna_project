"""Tests de la Phase 5 (sans broker) : règle adaptative, simulateur, détecteur, messages, pertes."""

from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from aquamind_core.config import ROOT_DIR, load_farm
from aquamind_core.leaks import AdaptiveRule, estimate_losses, load_river_config
from simulator.physics import FarmSimulator
from simulator.plan import plan_from_optimizer
from stream.consumer import parse_message
from stream.detector import LeakMonitor

TICK = timedelta(minutes=10)


@pytest.fixture(scope="module")
def heat_plan():
    return plan_from_optimizer("tunisia_heatwave")


def notebook_adaptive_rule():
    """Fonction adaptive_rule du notebook 04, exécutée telle quelle (référence)."""
    nb = json.loads((ROOT_DIR / "notebooks" / "04_river_calibration.ipynb").read_text(encoding="utf-8"))
    src = next("".join(c["source"]) for c in nb["cells"]
               if c["cell_type"] == "code" and "".join(c["source"]).startswith("ADAPT_WINDOW"))
    g = {"pd": pd, "np": np, "RULE_THRESHOLD": 0.5, "N_CONSEC_BEST": 2,
         "stream_features": lambda df: df[["imbalance"]]}
    exec(src.split("# Contrôle rapide")[0], g)
    return g["adaptive_rule"]


def toy_stream(days: int = 10, flush_day: int = 5, leaks=((3, 1.5), (8, 1.5)), seed: int = 0):
    rng = np.random.default_rng(seed)
    n = days * 144
    t = np.arange(n)
    hour = (t // 6) % 24
    pump = (hour >= 7) & (hour < 18)
    imb = 0.05 * rng.standard_normal(n)
    imb[pump & (t >= flush_day * 144)] += 0.6
    for d, rate in leaks:
        s = d * 144 + 60
        imb[s:s + 6] += rate
    return imb, pump


# ------------------------------------------------------------------ règle adaptative

def test_adaptive_rule_matches_notebook_exactly():
    imb, pump = toy_stream()
    df = pd.DataFrame({"imbalance": imb, "S1_plan": np.where(pump, 5.0, 0.0)})
    ref_alarm, ref_base = notebook_adaptive_rule()(df)
    rule = AdaptiveRule.from_config()
    steps = [rule.update(float(i), bool(p)) for i, p in zip(imb, pump)]
    assert np.array_equal(ref_alarm, np.array([s.alarm for s in steps]))
    assert np.allclose(ref_base, [s.baseline for s in steps])


def test_rule_detects_leaks_and_absorbs_flush():
    imb, pump = toy_stream()
    rule = AdaptiveRule.from_config()
    alarm = np.array([rule.update(float(i), bool(p)).alarm for i, p in zip(imb, pump)])
    rising = np.flatnonzero(alarm & ~np.r_[False, alarm[:-1]])
    days = sorted({int(r // 144) for r in rising})
    assert 3 in days and 8 in days              # deux fuites détectées
    flush_alerts = [r for r in rising if 5 * 144 <= r < 8 * 144]
    assert len(flush_alerts) <= 2               # le rinçage n'alerte pas en boucle


def test_new_normal_command_stops_flush_alerts():
    imb, pump = toy_stream(leaks=())
    rule = AdaptiveRule.from_config()
    alerts, pressed = 0, False
    prev = False
    for i, p in zip(imb, pump):
        step = rule.update(float(i), bool(p))
        if step.alarm and not prev:
            alerts += 1
            if not pressed:
                rule.new_normal()           # l'agriculteur confirme le changement
                pressed = True
                step.alarm = False
        prev = step.alarm
    assert alerts == 1


# ------------------------------------------------------------------ simulateur

def test_simulator_mass_balance_calendar_and_bounds(heat_plan):
    farm = load_farm()
    sim = FarmSimulator(farm, heat_plan, spikes=False)
    ts = heat_plan.start_dt
    for _ in range(288):
        plan = sim.plan_flows(ts)
        assert plan["S1"] == pytest.approx(plan["S2"] + plan["S3"] + plan["S4"] + plan["S5"])
        msgs = dict(sim.step(ts))
        assert 0 <= msgs["tank/level"]["level_m3"] <= farm.tank_capacity_m3
        assert msgs["pv/power"]["power_kw"] >= 0
        ts += TICK
    crops = {m["crop"] for _, m in sim.crop_info(heat_plan.start_dt)}
    assert "Piment" in crops                     # juillet : piment, pas blé


def test_parse_message_validates_readings():
    r = parse_message("aquamind/farm1", "aquamind/farm1/pipe/S3/flow",
                      {"ts": "2025-07-20T08:00:00", "flow_m3h": 2.5, "plan_m3h": 2.4, "pressure_bar": 2.5})
    assert r.kind == "flow" and r.source_id == "S3" and r.values["flow_m3h"] == 2.5
    assert parse_message("aquamind/farm1", "aquamind/farm1/pv/power",
                         {"ts": "2025-07-20T08:00:00", "power_kw": 1.0}).kind == "pv"
    assert parse_message("aquamind/farm1", "aquamind/farm1/cmd/leak", {"ts": "x"}) is None
    with pytest.raises(Exception):
        parse_message("aquamind/farm1", "aquamind/farm1/tank/level", {"level_m3": 3})   # pas de ts


def test_loss_estimate():
    loss = estimate_losses(1.5, 10, 0.4, 0.2, 0.22)
    assert loss == {"m3_day": 15.0, "kwh_day": 6.0, "tnd_day": round(15 * 0.2 + 6 * 0.22, 2)}


# ------------------------------------------------------------------ détecteur de bout en bout (hors ligne)

def run_monitor(plan, n_ticks, events_at=None, seed=1):
    """Simulateur → détecteur, pas à pas ; events_at : {pas: fonction(sim, monitor)}."""
    farm = load_farm()
    sim = FarmSimulator(farm, plan, seed=seed)
    mon = LeakMonitor(farm)
    from simulator.physics import synthetic_ticks
    from simulator.plan import default_plan

    mon.warmup(synthetic_ticks(farm, default_plan(farm, plan.start_dt.date()), 576, plan.start_dt))
    ts = plan.start_dt
    out = []
    for k in range(n_ticks):
        if events_at and k in events_at:
            events_at[k](sim, mon)
        flows, plans = {}, {}
        for topic, p in sim.step(ts):
            if topic.startswith("pipe/"):
                flows[topic.split("/")[1]] = p["flow_m3h"]
                plans[topic.split("/")[1]] = p["plan_m3h"]
        for ev in mon.process(ts, flows, plans):
            out.append((k, ev))
        ts += TICK
    return out


def first_line_tick(plan, after=0, section="S1", hold=3):
    """Premier pas ≥ after où le tronçon est en eau pendant `hold` pas consécutifs."""
    sim = FarmSimulator(load_farm(), plan)
    flows = [sim.plan_flows(plan.start_dt + TICK * k)[section] for k in range(after, after + 288 + hold)]
    for j in range(288):
        if all(f > 1.0 for f in flows[j:j + hold]):
            return after + j
    raise AssertionError(f"{section} jamais en eau")


def test_monitor_alerts_on_leak_within_three_ticks(heat_plan):
    k0 = first_line_tick(heat_plan, after=6 * 6, section="S3")
    events = run_monitor(heat_plan, k0 + 6,
                         {k0: lambda sim, mon: sim.inject_leak("S3", 1.5)})
    alerts = [(k, e.alert) for k, e in events if e.kind == "alert"]
    assert alerts, "aucune alerte"
    k, a = alerts[-1]
    assert k0 <= k <= k0 + 2                     # 2 messages consécutifs (≈ 1,7 s réelles)
    assert a.section == "S3" and a.detector == "rule_adaptive"
    assert a.est_loss_m3_day > 0 and a.est_loss_kwh_day > 0 and a.est_loss_tnd_day > 0
    assert "Pertes estimées" in a.message


def test_monitor_flush_does_not_alert_in_loop(heat_plan):
    k0 = first_line_tick(heat_plan, after=6 * 6)
    events = run_monitor(heat_plan, k0 + 2 * 144,                       # deux jours de rinçage
                         {k0: lambda sim, mon: sim.set_flush(0.6)})
    flush_alerts = [k for k, e in events if e.kind == "alert" and k >= k0]
    # La référence absorbe le rinçage (médiane glissante, ou acceptation après 8 h d'écart) :
    # quelques alertes le premier jour, puis plus rien — pas d'alerte en boucle.
    assert len(flush_alerts) <= 6, flush_alerts                         # borné (logique du notebook 04)
    assert not [k for k in flush_alerts if k > k0 + 144], flush_alerts  # aucune le 2e jour


def test_new_normal_button_gives_single_flush_alert(heat_plan):
    """Avec le bouton « nouveau normal » pressé à la première alerte : une seule alerte."""
    k0 = first_line_tick(heat_plan, after=6 * 6)
    farm = load_farm()
    sim = FarmSimulator(farm, heat_plan, seed=1)
    mon = LeakMonitor(farm)
    from simulator.physics import synthetic_ticks
    from simulator.plan import default_plan

    mon.warmup(synthetic_ticks(farm, default_plan(farm, heat_plan.start_dt.date()), 576, heat_plan.start_dt))
    ts, alerts = heat_plan.start_dt, []
    for k in range(k0 + 2 * 144):
        if k == k0:
            sim.set_flush(0.6)
        flows, plans = {}, {}
        for topic, p in sim.step(ts):
            if topic.startswith("pipe/"):
                flows[topic.split("/")[1]], plans[topic.split("/")[1]] = p["flow_m3h"], p["plan_m3h"]
        for ev in mon.process(ts, flows, plans):
            if ev.kind == "alert" and k >= k0:
                alerts.append(k)
                mon.new_normal()
        ts += TICK
    assert len(alerts) == 1, alerts


def test_monitor_mode_comes_from_river_config():
    assert LeakMonitor(load_farm()).mode == load_river_config()["recommended_mode"] == "rule_adaptive"


def test_sqlite_fallback_storage(tmp_path):
    from aquamind_core.config import Settings
    from aquamind_core.schemas import Alert, SensorReading
    from stream.storage import make_storage

    s = Settings(storage_backend="sqlite", sqlite_path=str(tmp_path / "stream.db"))
    st = make_storage(s)
    assert st.name == "sqlite"
    st.write_reading(SensorReading(ts="2025-07-20T08:00:00", kind="flow", source_id="S3",
                                   values={"flow_m3h": 2.0, "plan_m3h": 2.0}))
    st.write_alert(Alert(ts="2025-07-20T08:00:00", section="S3", score=1.9, detector="rule_adaptive",
                         est_loss_m3_day=15, est_loss_kwh_day=6, est_loss_tnd_day=4, message="test"))
    st.close()
    import sqlite3

    with sqlite3.connect(tmp_path / "stream.db") as con:
        assert con.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_all_simulator_messages_are_json_serializable(heat_plan):
    """Régression : un numpy.bool_ (dry_run_protection) faisait planter le simulateur à la cuve vide."""
    farm = load_farm()
    sim = FarmSimulator(farm, heat_plan)
    sim.set_flush(3.0)                      # vide la cuve plus vite
    ts = heat_plan.start_dt
    dry_seen = False
    for _ in range(400):
        for _, payload in sim.step(ts):
            json.dumps(payload)
        state = sim.actuator_state(ts)
        json.dumps(state)                   # pas de default : doit déjà être du JSON pur
        dry_seen |= state["dry_run_protection"]
        ts += TICK
    sim.state.tank_m3 = 0.1
    state = sim.actuator_state(ts)
    assert state["dry_run_protection"] is True and type(state["dry_run_protection"]) is bool
    json.dumps(state)
