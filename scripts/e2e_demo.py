"""Parcours de démonstration complet, testé automatiquement via l'API (qui doit déjà tourner).

    .\\.venv\\Scripts\\python scripts\\e2e_demo.py [--api http://127.0.0.1:8000]

Prérequis : l'API (uvicorn api.main:app) et le simulateur (python -m simulator.publisher --plan api)
tournent déjà. Le script ne lance rien ; il passe par l'API (HTTP + WebSocket /ws/live). Seule exception :
l'étape 8 accélère temporairement l'horloge du simulateur (MQTT cmd/speed) puis la rétablit.

Étapes : 1 santé · 2 scénario canicule · 3 pompe et vanne · 4 fuite · 5 alerte < 15 s · 6 réparation ·
7 isoler la parcelle · 8 arrêt d'urgence · 9 rinçage sans alerte en boucle · 10 journal des commandes.
Code retour 0 si tout passe. En cas d'échec : cause probable et correction proposée (rien n'est modifié).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aquamind_core  # noqa: E402,F401  (truststore avant requests)
import requests  # noqa: E402

from aquamind_core.config import get_settings  # noqa: E402

S = get_settings()
SECTION_OF = {"A": "S2", "B": "S3", "C": "S4"}
LEAK_RATE = 1.5
ALERT_DEADLINE_S = 15.0
FAST_S_PER_H = 0.5
FLUSH_DAYS = 2


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str
    why: str = ""
    fix: str = ""


@dataclass
class Ctx:
    api: str
    events: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    ws_ok: bool = False
    stop: threading.Event = field(default_factory=threading.Event)
    valve: str | None = None
    section: str | None = None
    command_ids: list[str] = field(default_factory=list)

    def get(self, path: str, **kw) -> requests.Response:
        return requests.get(self.api + path, timeout=30, **kw)

    def post(self, path: str, **kw) -> requests.Response:
        return requests.post(self.api + path, timeout=60, **kw)

    def alerts_since(self, t0: float) -> list[tuple[float, dict[str, Any]]]:
        with self.lock:
            return [(t, e) for t, e in self.events if t >= t0 and e.get("type") == "alert"]

    def wait(self, cond: Callable[[], bool], timeout: float, every: float = 0.1) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(every)
        return False


def listen(ctx: Ctx) -> None:
    """Abonné WebSocket : alertes, fin d'alerte, nouveau normal, débit nul."""
    from websockets.sync.client import connect

    url = ctx.api.replace("http", "ws", 1) + "/ws/live?types=alert,alert_end,alert_update,new_normal,no_flow"
    try:
        with connect(url, open_timeout=10) as ws:
            ctx.ws_ok = True
            while not ctx.stop.is_set():
                try:
                    msg = json.loads(ws.recv(timeout=0.5))
                except TimeoutError:
                    continue
                with ctx.lock:
                    ctx.events.append((time.monotonic(), msg))
    except Exception as exc:
        print(f"   (WebSocket indisponible : {exc})")


def set_sim_speed(seconds_per_hour: float) -> bool:
    """Seule action hors API : horloge du simulateur (MQTT cmd/speed), pour l'étape 8."""
    import paho.mqtt.client as mqtt

    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aquamind-e2e-demo-{os.getpid()}")
        c.connect(S.mqtt_host, S.mqtt_port)
        c.loop_start()
        c.publish(f"{S.mqtt_base_topic}/cmd/speed", json.dumps({"sim_seconds_per_hour": seconds_per_hour}),
                  qos=1).wait_for_publish(3)
        c.loop_stop()
        c.disconnect()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------------------- étapes

def ticks(ctx: Ctx) -> int:
    return int(((ctx.get("/health").json().get("consumer") or {}).get("stats") or {}).get("ticks", 0))


def sim_alive(ctx: Ctx, seconds: float = 4.0) -> bool:
    """Le simulateur publie-t-il ? (le compteur de pas du consumer doit augmenter)."""
    n0 = ticks(ctx)
    return ctx.wait(lambda: ticks(ctx) > n0, seconds, every=0.5)


SIM_DOWN_WHY = "aucun pas de débits reçu : le simulateur ne publie plus (arrêté, planté ou figé)"
SIM_DOWN_FIX = ("regarder le terminal du simulateur (trace d'erreur ? console figée par une sélection : "
                "appuyer sur Échap) puis le relancer : .\\.venv\\Scripts\\python -m simulator.publisher "
                "--plan api --start-hour 8")


def step_health(ctx: Ctx) -> Step:
    h = ctx.get("/health").json()
    mode = (h.get("consumer") or {}).get("mode")
    ok = h.get("mqtt") is True and h.get("storage") in ("influx", "sqlite") and mode == "rule_adaptive"
    alive = sim_alive(ctx)
    age = h.get("last_reading_age_s")
    detail = (f"mqtt={h.get('mqtt')}, stockage={h.get('storage')}, mode={mode}, "
              f"dernière mesure {'?' if age is None else f'{age:.1f} s'}, "
              f"WebSocket={'oui' if ctx.ws_ok else 'non'}, flux du simulateur={'oui' if alive else 'NON'}")
    if not alive:
        return Step("1. /health + flux vivant", False, 0, detail, SIM_DOWN_WHY, SIM_DOWN_FIX)
    return Step("1. /health + flux vivant", ok and ctx.ws_ok, 0, detail,
                why="" if ok else "l'API ne voit pas le broker, le stockage ou le consumer embarqué",
                fix="" if ok else "lancer scripts\\start-services.ps1 puis redémarrer uvicorn ; vérifier "
                                  "API_EMBED_CONSUMER=true et RECOMMENDED_MODE dans river_config.json")


def step_scenario(ctx: Ctx) -> Step:
    r = ctx.get("/scenarios/tunisia_heatwave", params={"apply": "true"})
    if r.status_code != 200:
        return Step("2. scénario canicule appliqué", False, 0, f"HTTP {r.status_code}",
                    why=r.text[:200], fix="vérifier les données d'archive (python -m ingest.run_all)")
    sat = r.json()["result"]["satisfaction"]
    plan = ctx.get("/optimize/last").json()
    meta = ctx.get("/health").json()["plan"]
    # le simulateur recharge /optimize/last sur cmd/replan : on le vérifie par la consigne publiée
    ok_plan = meta.get("source") == "scénario tunisia_heatwave"
    followed = ctx.wait(lambda: _sim_follows(ctx, plan), 20, every=1.0)
    return Step("2. scénario canicule appliqué", ok_plan and followed, 0,
                f"satisfaction A/B/C {sat['A']:.0%}/{sat['B']:.0%}/{sat['C']:.0%} ; simulateur "
                f"{'suit le plan' if followed else 'NE SUIT PAS le plan'}",
                why="" if followed else "le simulateur n'a pas rechargé /optimize/last (cmd/replan)",
                fix="" if followed else "lancer le simulateur avec --plan api (ou auto) pour qu'il lise le plan de "
                                        "l'API ; sinon le redémarrer après l'application du scénario")


def _sim_follows(ctx: Ctx, plan: dict[str, Any]) -> bool:
    """Les consignes reçues (plan_m3h) correspondent à une heure du plan canicule."""
    rows = ctx.get("/history", params={"kind": "flow", "source": "S2", "minutes": 1}).json().get("rows", [])
    if not rows:
        return False
    last = rows[-1]
    sim_ts = last.get("sim_ts") or ""
    if not sim_ts:
        return False
    from datetime import datetime

    start = datetime.fromisoformat(plan["inputs"]["start"])
    h = int((datetime.fromisoformat(sim_ts) - start).total_seconds() // 3600) % plan["horizon_h"]
    return abs(last["values"].get("plan_m3h", -1) - plan["allocation"]["A"][h]) < 0.01


def step_pump(ctx: Ctx) -> Step:
    def running() -> bool:
        st = ctx.get("/actuators/state").json()
        open_v = [v for v, o in st["valves"].items() if o]
        if st["pump_on"] and open_v and st["mode"] == "auto":
            ctx.valve = open_v[0]
            return True
        return False

    # On attend qu'un robinet S'OUVRE (début d'une heure d'arrosage) : la fuite aura alors toute
    # l'heure simulée (≈ 5 s, 6 relevés) pour couler, au lieu de tomber juste avant une fermeture.
    def opened_now(prev: set[str]) -> set[str]:
        st = ctx.get("/actuators/state").json()
        now = {v for v, o in st["valves"].items() if o} if st["pump_on"] and st["mode"] == "auto" else set()
        return now - prev

    ok, deadline = False, time.monotonic() + 240
    prev: set[str] = set()
    st0 = ctx.get("/actuators/state").json()
    prev = {v for v, o in st0["valves"].items() if o}
    while not ok and time.monotonic() < deadline:
        new = opened_now(prev)
        if new:
            ctx.valve, ok = sorted(new)[0], True
            break
        st = ctx.get("/actuators/state").json()
        prev = {v for v, o in st["valves"].items() if o} if st["pump_on"] else set()
        time.sleep(0.2)
    ctx.section = SECTION_OF.get(ctx.valve or "", None)
    return Step("3. pompe en marche, vanne repérée", ok, 0,
                f"vanne {ctx.valve} ouverte → tronçon {ctx.section}" if ok else "pompe jamais en marche en 240 s",
                why="" if ok else "le plan ne prévoit pas d'irrigation pendant l'attente, ou mode manuel resté actif",
                fix="" if ok else "POST /actuators/auto (retour au plan), ou relancer le simulateur avec --start-hour 8")


def step_leak_and_alert(ctx: Ctx) -> tuple[Step, Step]:
    t0 = time.monotonic()
    r = ctx.post("/sim/leak", json={"section": ctx.section, "rate_m3h": LEAK_RATE})
    leak = Step("4. fuite injectée", r.status_code == 200, 0, f"{ctx.section}, {LEAK_RATE} m³/h (HTTP {r.status_code})",
                why="" if r.status_code == 200 else r.text[:150], fix="vérifier le broker (GET /health)")
    if r.status_code != 200:
        return leak, Step("5. alerte < 15 s", False, 0, "fuite non injectée")
    got = ctx.wait(lambda: bool(ctx.alerts_since(t0)), ALERT_DEADLINE_S, every=0.05)
    if not got:
        st = ctx.get("/actuators/state").json()
        return leak, Step("5. alerte < 15 s", False, ALERT_DEADLINE_S, "aucune alerte",
                          why=f"la vanne {ctx.valve} s'est peut-être fermée (vannes : {st['valves']}) : une fuite de "
                              "branche ne coule que si la branche est en eau",
                          fix="injecter la fuite sur S1 (conduite principale, toujours en eau pendant le pompage)")
    t, a = ctx.alerts_since(t0)[0]
    # l'alerte peut être précisée quelques secondes plus tard (tuyau localisé sur plusieurs relevés)
    if a["section"] != ctx.section:
        def updated() -> str | None:
            with ctx.lock:
                ups = [e for _, e in ctx.events if e.get("type") == "alert_update"
                       and e.get("alert_id") == a.get("alert_id") and e.get("section")]
            return ups[-1]["section"] if ups else None
        ctx.wait(lambda: updated() is not None, 8, every=0.2)
        if updated():
            a = {**a, "section": updated()}
    complete = all(a.get(k, 0) > 0 for k in ("est_loss_m3_day", "est_loss_kwh_day", "est_loss_tnd_day"))
    ok = (t - t0) < ALERT_DEADLINE_S and a["section"] == ctx.section and complete
    detail = (f"tronçon {a['section']} ; {a['est_loss_m3_day']} m³/j, {a['est_loss_kwh_day']} kWh/j, "
              f"{a['est_loss_tnd_day']} TND/j ; confirmé par l'IA : {a.get('confirmed_by_ai')}")
    why, fix = "", ""
    if a["section"] != ctx.section:
        why = f"alerte localisée sur {a['section']} au lieu de {ctx.section} (baisse du compteur trop faible ?)"
        fix = "abaisser locate_section(min_drop_m3h) à 0,15 ou augmenter le débit de fuite du test"
    elif not complete:
        why, fix = "pertes manquantes dans l'alerte", "vérifier estimate_losses / heures de pompage estimées"
    return leak, Step("5. alerte < 15 s (m³, kWh, TND)", ok, t - t0, detail, why, fix)


def step_repair(ctx: Ctx) -> Step:
    t0 = time.monotonic()
    r = ctx.post("/sim/reset")
    ended = ctx.wait(lambda: any(e.get("type") == "alert_end" for t, e in ctx.events if t >= t0), 15, every=0.1)
    return Step("6. réparation (/sim/reset)", r.status_code == 200 and ended, time.monotonic() - t0,
                "fin d'alerte reçue" if ended else "pas de fin d'alerte en 15 s",
                why="" if ended else "le bilan reste rompu (fuite encore active ?) ou la vanne s'est fermée",
                fix="" if ended else "renvoyer POST /sim/reset ; vérifier GET /alerts")


def step_isolate(ctx: Ctx) -> Step:
    """Isoler la parcelle de la vanne ouverte (pompe off → vanne fermée → alerte), puis retour au plan."""
    t0 = time.monotonic()

    def running() -> bool:
        st = ctx.get("/actuators/state").json()
        open_v = [v for v, o in st["valves"].items() if o]
        if st["pump_on"] and open_v:
            ctx.valve = open_v[0]
            return True
        return False

    if not ctx.wait(running, 120, every=0.5):
        return Step("7. isoler la parcelle (pompe → vanne → alerte)", False, time.monotonic() - t0,
                    "pompe jamais en marche", why="pas d'irrigation pendant l'attente", fix="POST /actuators/auto")
    v = ctx.valve
    r = ctx.post(f"/actuators/isolate/{v}", json={"source": "dashboard", "actor": "e2e-demo", "reason": "test e2e"})
    if r.status_code == 404:
        return Step("7. isoler la parcelle (pompe → vanne → alerte)", False, time.monotonic() - t0,
                    "endpoint absent (HTTP 404)", why="l'API tourne une version antérieure à la Phase 7",
                    fix="arrêter puis relancer uvicorn pour charger le nouveau code")
    body = r.json()
    order = [s["command"]["target"] for s in body.get("steps", [])]
    confirmed = ctx.wait(lambda: (lambda s: not s["pump_on"] and not s["valves"][v])(ctx.get("/actuators/state").json()),
                         8, every=0.3)
    alert = next((a for a in ctx.get("/alerts", params={"limit": 5}).json() if a.get("detector") == "isolation"), None)
    ok = r.status_code == 200 and order == ["pump", "valve"] and confirmed and alert is not None
    ctx.post("/actuators/auto", json={"source": "dashboard", "actor": "e2e-demo"})
    return Step("7. isoler la parcelle (pompe → vanne → alerte)", ok, time.monotonic() - t0,
                f"vanne {v} : HTTP {r.status_code}, séquence {order}, appareil {'confirme' if confirmed else 'NE confirme PAS'}"
                f", alerte {'créée' if alert else 'absente'} ; retour au plan",
                why="" if ok else body.get("reason", "séquence incomplète"),
                fix="" if ok else "lire GET /actuators/log : quelle étape a été refusée et pourquoi")


def step_no_open_alert(ctx: Ctx) -> Step:
    """Précondition : aucune alerte ouverte (sinon une nouvelle fuite ou un rinçage ne seraient pas
    annoncés : le bilan de masse est global, une alerte ouverte couvre toute la ferme)."""
    def clear() -> bool:
        return not any(a.get("active") for a in ctx.get("/alerts", params={"limit": 20}).json())

    ok = ctx.wait(clear, 90, every=1.0)
    return Step("·  aucune alerte ouverte au départ", ok, 0,
                "ferme saine" if ok else "une alerte reste ouverte après réparation",
                why="" if ok else "le tuyau suspect n'a pas recoulé normalement depuis la réparation (vanne fermée)",
                fix="" if ok else "attendre la prochaine irrigation de ce tuyau, ou POST /sim/new_normal")


def step_estop(ctx: Ctx) -> Step:
    t0 = time.monotonic()
    results = []
    calls = [
        ("arrêt d'urgence (agent)", "/emergency-stop", {"engaged": True, "source": "agent", "actor": "e2e-demo"}, 200),
        ("pompe pendant l'arrêt", "/actuators/pump",
         {"on": True, "duration_min": 5, "source": "dashboard", "actor": "e2e-demo"}, 409),
        ("relâchement par l'agent", "/emergency-stop",
         {"engaged": False, "source": "agent", "actor": "e2e-demo", "reason": "test"}, 409),
        ("relâchement par un humain", "/emergency-stop",
         {"engaged": False, "source": "dashboard", "actor": "e2e-demo", "reason": "test e2e terminé"}, 200),
        ("retour au plan", "/actuators/auto", {"source": "dashboard", "actor": "e2e-demo"}, 200),
    ]
    for label, path, body, expected in calls:
        r = ctx.post(path, json=body)
        data = r.json()
        if "command" in data:
            ctx.command_ids.append(data["command"]["command_id"])
        results.append((label, r.status_code, expected, data.get("reason", "")))
        time.sleep(0.5)
    estop_state = ctx.get("/actuators/state").json()
    bad = [(lbl, got, exp, why) for lbl, got, exp, why in results if got != exp]
    pump_reason = results[1][3]
    ok = not bad and "arrêt d'urgence" in pump_reason and not estop_state["estop"]
    detail = " ; ".join(f"{lbl} → {got}" for lbl, got, _, _ in results)
    why = "; ".join(f"{lbl} : {got} au lieu de {exp} ({w})" for lbl, got, exp, w in bad)
    fix = ("503 = commande acceptée mais broker absent : vérifier MQTT ; 409 inattendu : lire la raison et "
           "safety.check_command") if bad else ""
    return Step("8. arrêt d'urgence → pompe refusée → relâchement humain", ok, time.monotonic() - t0,
                detail, why, fix)


def step_flush(ctx: Ctx) -> Step:
    t0 = time.monotonic()
    if not set_sim_speed(FAST_S_PER_H):
        return Step("9. rinçage sans alerte en boucle", False, 0, "impossible d'accélérer l'horloge (MQTT)",
                    why="broker injoignable depuis le script", fix="vérifier MQTT_HOST/MQTT_PORT dans .env")
    try:
        time.sleep(1)
        n0 = ticks(ctx)
        tf = time.monotonic()
        ctx.post("/sim/flush", json={"rate_m3h": 0.6})
        day_s = 24 * FAST_S_PER_H
        time.sleep(FLUSH_DAYS * day_s + 2)
        received = ticks(ctx) - n0
        expected = FLUSH_DAYS * 144
        imbalance = flush_imbalance(ctx, seconds=FLUSH_DAYS * day_s)
        alerts = ctx.alerts_since(tf)
        day1 = [a for t, a in alerts if t - tf <= day_s + 1]
        later = [a for t, a in alerts if t - tf > day_s + 1]
        flowing = received >= 0.5 * expected
        effective = imbalance is not None and imbalance > 0.3
        ok = flowing and effective and len(day1) <= 6 and not later
        detail = (f"{received}/{expected} pas reçus, déséquilibre moyen en pompage "
                  f"{'—' if imbalance is None else f'{imbalance:+.2f}'} m³/h ; {len(day1)} alerte(s) le 1er jour "
                  f"simulé, {len(later)} ensuite")
        why = fix = ""
        if not flowing:
            why, fix = f"{SIM_DOWN_WHY} ({received} pas reçus sur {expected} attendus)", SIM_DOWN_FIX
        elif not effective:
            why = "le rinçage n'a pas modifié le bilan de masse : la conduite n'était pas en eau, ou la commande flush est perdue"
            fix = "vérifier dans le terminal du simulateur la ligne « Rinçage des filtres : 0.60 m³/h »"
        elif not ok:
            why = ("la référence n'absorbe pas le rinçage" if later else
                   f"trop d'alertes avant absorption ({len(day1)})")
            fix = ("réduire accept_after_pumping_ticks (48 → 24) dans le notebook 04 puis le réexécuter, ou "
                   "appuyer sur « nouveau normal » (POST /sim/new_normal) dès la première alerte")
    finally:
        ctx.post("/sim/flush", json={"rate_m3h": 0.0})
        ctx.post("/sim/new_normal")             # l'arrêt du rinçage est lui aussi un changement voulu
        set_sim_speed(float(S.sim_seconds_per_hour))
    return Step("9. rinçage des filtres sans alerte en boucle", ok, time.monotonic() - t0, detail, why, fix)


def flush_imbalance(ctx: Ctx, seconds: float) -> float | None:
    """Déséquilibre moyen S1 − (S2+S3+S4+S5) des pas en pompage des `seconds` dernières secondes réelles."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ticks_by: dict[str, dict[str, tuple[float, float]]] = {}
    for s in ("S1", "S2", "S3", "S4", "S5"):
        rows = ctx.get("/history", params={"kind": "flow", "source": s, "minutes": seconds / 60 + 1}).json()["rows"]
        for r in rows:
            if datetime.fromisoformat(r["ts"]) >= since:
                ticks_by.setdefault(r["sim_ts"], {})[s] = (r["values"]["flow_m3h"], r["values"]["plan_m3h"])
    vals = [v["S1"][0] - sum(v[s][0] for s in ("S2", "S3", "S4", "S5"))
            for v in ticks_by.values() if len(v) == 5 and v["S1"][1] > 0.5 and v["S1"][0] > 0.3]
    return sum(vals) / len(vals) if vals else None


def step_journal(ctx: Ctx) -> Step:
    log = ctx.get("/actuators/log", params={"limit": 100}).json()
    by_id = {e["command"]["command_id"]: e for e in log}
    mine = [by_id.get(cid) for cid in ctx.command_ids]
    missing = [cid for cid, e in zip(ctx.command_ids, mine) if e is None]
    expected = [True, False, False, True, True]
    got = [e["accepted"] if e else None for e in mine]
    complete = all(e and e["command"]["source"] and e["decided_at"] and e["reason"] for e in mine)
    ok = not missing and got == expected and complete
    detail = f"{len(ctx.command_ids)} commandes retrouvées, décisions {got}"
    why = "" if ok else (f"commandes absentes du journal : {missing}" if missing else
                         f"décisions {got} au lieu de {expected}, ou champs qui/quand/pourquoi manquants")
    fix = "" if ok else "vérifier CommandJournal.record (api/actuators.py) et data/actuator_log.jsonl"
    return Step("10. journal des commandes (qui, quoi, quand, pourquoi)", ok, 0, detail, why, fix)


# ---------------------------------------------------------------------------------------- principal

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    ctx = Ctx(args.api.rstrip("/"))
    try:
        ctx.get("/health")
    except requests.RequestException:
        print(f"❌ API injoignable sur {ctx.api} : lancer d'abord "
              ".\\.venv\\Scripts\\python -m uvicorn api.main:app --host 127.0.0.1 --port 8000")
        return 1
    threading.Thread(target=listen, args=(ctx,), daemon=True).start()
    ctx.wait(lambda: ctx.ws_ok, 10)

    steps: list[Step] = []

    def run(fn: Callable[[Ctx], Step | tuple[Step, ...]]) -> bool:
        t0 = time.monotonic()
        try:
            out = fn(ctx)
        except Exception as exc:
            out = Step(fn.__name__, False, 0, f"exception : {exc!r}", why="erreur inattendue",
                       fix="lire la trace et les journaux de l'API")
        for s in (out if isinstance(out, tuple) else (out,)):
            if not s.seconds:
                s.seconds = time.monotonic() - t0
            steps.append(s)
            print(f"{'✅' if s.ok else '❌'} {s.name:58s} {s.seconds:6.1f} s  {s.detail}")
            if not s.ok:
                print(f"     ↳ pourquoi : {s.why}\n     ↳ correction proposée (non appliquée) : {s.fix}")
        return all(s.ok for s in (out if isinstance(out, tuple) else (out,)))

    print(f"Parcours de démonstration via {ctx.api} (simulateur : {S.sim_seconds_per_hour} s / heure simulée)\n")
    t_all = time.monotonic()
    # point de départ propre : aucune fuite ni nettoyage en cours, l'IA pilote, aucune alerte ouverte
    ctx.post("/sim/reset")
    ctx.post("/actuators/auto", json={"source": "dashboard", "actor": "e2e-demo"})
    if run(step_health) and run(step_scenario) and run(step_no_open_alert) and run(step_pump):
        run(step_leak_and_alert)
        run(step_repair)
        run(step_isolate)
    run(step_estop)
    if run(step_no_open_alert):
        run(step_flush)
    run(step_journal)
    ctx.stop.set()

    n_ok = sum(s.ok for s in steps)
    print(f"\nRÉSUMÉ : {n_ok}/{len(steps)} étapes réussies en {time.monotonic() - t_all:.0f} s")
    for s in steps:
        if not s.ok:
            print(f"  ❌ {s.name} — {s.why}")
    return 0 if n_ok == len(steps) else 1


if __name__ == "__main__":
    sys.exit(main())
