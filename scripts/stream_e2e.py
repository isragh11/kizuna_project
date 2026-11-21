"""Test de bout en bout de la Phase 5 (broker MQTT réel, stockage InfluxDB ou SQLite).

    .\\.venv\\Scripts\\python scripts\\stream_e2e.py

Lance le consumer et le simulateur (plan du scénario canicule, déterministe et hors ligne), puis :
  A. 30 s de fonctionnement normal (vitesse réelle, SIM_SECONDS_PER_HOUR) : fausses alertes ?
  B. fuite de 1,5 m³/h sur S3 pendant l'irrigation de la parcelle B : alerte en < 10 s ?
  C. rinçage des filtres 0,6 m³/h pendant 2 jours simulés (horloge accélérée) : alertes bornées,
     aucune le 2e jour ?
  D. fuite pendant le rinçage (après absorption) : toujours détectée ?
  E. stockage : des mesures ont bien été écrites ?
Code retour 0 si tous les critères passent. Journaux : logs/e2e_consumer.log, logs/e2e_simulator.log.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aquamind_core  # noqa: E402,F401  (truststore avant requests)
import paho.mqtt.client as mqtt  # noqa: E402

from aquamind_core.config import get_settings  # noqa: E402

S = get_settings()
BASE = S.mqtt_base_topic
LEAK_RATE, FLUSH_RATE = 1.5, 0.6
MAX_LATENCY_S = 10.0
FAST_S_PER_H = 0.5


class Watcher:
    """Abonné de test : alertes, statut du consumer, débits du plan."""

    def __init__(self) -> None:
        self.events: list[tuple[float, dict]] = []
        self.ready_at: float | None = None
        self.plan: dict[str, float] = {}
        self.sim_ts: str = ""
        self.lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aquamind-e2e")
        self.client.on_message = self._on_message
        self.client.connect(S.mqtt_host, S.mqtt_port)
        self.client.subscribe([(f"{BASE}/alerts", 1), (f"{BASE}/status/consumer", 1), (f"{BASE}/pipe/+/flow", 0)])
        self.client.loop_start()

    def _on_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload or b"{}")
        except ValueError:
            return
        now = time.monotonic()
        with self.lock:
            if msg.topic.endswith("/alerts"):
                self.events.append((now, data))
            elif msg.topic.endswith("status/consumer") and data.get("status") == "ready":
                self.ready_at = now
            elif "/pipe/" in msg.topic:
                self.plan[msg.topic.split("/")[-2]] = data.get("plan_m3h", 0.0)
                self.sim_ts = data.get("ts", self.sim_ts)

    def cmd(self, name: str, body: dict) -> None:
        self.client.publish(f"{BASE}/cmd/{name}", json.dumps(body), qos=1).wait_for_publish(2)

    def alerts_since(self, t0: float) -> list[tuple[float, dict]]:
        with self.lock:
            return [(t, e) for t, e in self.events if t >= t0 and e.get("type") == "alert"]

    def wait_until(self, cond, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False


def start(name: str, args: list[str]) -> subprocess.Popen:
    log = open(ROOT / "logs" / f"e2e_{name}.log", "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.Popen([sys.executable, "-m", *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)


def main() -> int:
    (ROOT / "logs").mkdir(exist_ok=True)
    results: dict[str, tuple[bool, str]] = {}
    w = Watcher()
    w.client.publish(f"{BASE}/status/consumer", b"", qos=1, retain=True).wait_for_publish(2)  # efface l'ancien statut
    t_start = time.monotonic()
    consumer = start("consumer", ["stream.consumer"])
    sim = None
    try:
        if not w.wait_until(lambda: w.ready_at is not None and w.ready_at > t_start, 60):
            print("KO  le consumer n'est pas prêt (voir logs/e2e_consumer.log)")
            return 1
        print(f"OK  consumer prêt en {w.ready_at - t_start:.1f} s (River préchauffé)")
        sim = start("simulator", ["simulator.publisher", "--plan", "scenario:tunisia_heatwave", "--start-hour", "6"])

        # A. fonctionnement normal
        tA = time.monotonic()
        time.sleep(30)
        fa = w.alerts_since(tA)
        results["A. 30 s normales sans alerte"] = (len(fa) == 0, f"{len(fa)} alerte(s), heure simulée {w.sim_ts}")

        # B. fuite pendant l'irrigation de B (S3)
        w.wait_until(lambda: w.plan.get("S3", 0) > 1.0, 180)
        time.sleep(2 * S.sim_seconds_per_hour / 6)           # S3 en eau depuis au moins un pas
        tB = time.monotonic()
        w.cmd("leak", {"section": "S3", "rate_m3h": LEAK_RATE})
        got = w.wait_until(lambda: bool(w.alerts_since(tB)), 15)
        if got:
            t_alert, alert = w.alerts_since(tB)[0]
            latency = t_alert - tB
            results["B. fuite S3 → alerte < 10 s"] = (
                latency < MAX_LATENCY_S and alert["section"] == "S3",
                f"{latency:.1f} s, tronçon {alert['section']}, {alert['est_loss_m3_day']} m³/j, "
                f"{alert['est_loss_kwh_day']} kWh/j, {alert['est_loss_tnd_day']} TND/j, "
                f"confirmé par l'IA : {alert['confirmed_by_ai']}")
            print("    Message :", alert["message"])
        else:
            results["B. fuite S3 → alerte < 10 s"] = (False, "aucune alerte en 15 s")
        w.cmd("reset", {})
        time.sleep(3)

        # C. rinçage des filtres, 2 jours simulés en accéléré
        w.cmd("speed", {"sim_seconds_per_hour": FAST_S_PER_H})
        time.sleep(1)
        tC = time.monotonic()
        w.cmd("flush", {"rate_m3h": FLUSH_RATE})
        day_s = 24 * FAST_S_PER_H
        time.sleep(2 * day_s + 2)
        flush_alerts = w.alerts_since(tC)
        day2 = [a for t, a in flush_alerts if t - tC > day_s + 1]
        results["C. rinçage : pas d'alerte en boucle"] = (
            len(flush_alerts) <= 6 and not day2,
            f"{len(flush_alerts)} alerte(s) le 1er jour simulé, {len(day2)} le 2e")

        # D. fuite pendant le rinçage (référence déjà recalée)
        w.wait_until(lambda: w.plan.get("S2", 0) > 1.0, 60)
        tD = time.monotonic()
        w.cmd("leak", {"section": "S2", "rate_m3h": LEAK_RATE})
        okD = w.wait_until(lambda: bool(w.alerts_since(tD)), 10)
        results["D. fuite pendant le rinçage détectée"] = (
            okD, w.alerts_since(tD)[0][1]["section"] if okD else "aucune alerte")
        w.cmd("reset", {})

        # E. stockage
        results["E. mesures stockées"] = storage_check()
    finally:
        for p in (sim, consumer):
            if p is not None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
        w.client.loop_stop()

    print()
    ok_all = True
    for name, (ok, detail) in results.items():
        ok_all &= ok
        print(f"{'OK ' if ok else 'KO '} {name} : {detail}")
    print("\nRÉSULTAT :", "tous les critères passent" if ok_all else "au moins un critère échoue")
    return 0 if ok_all else 1


def storage_check() -> tuple[bool, str]:
    """Compte les débits de S3 des 10 dernières minutes (InfluxDB) ou en base SQLite."""
    if S.storage_backend == "influx":
        try:
            from influxdb_client import InfluxDBClient

            flux = (f'from(bucket: "{S.influxdb_bucket}") |> range(start: -10m) '
                    '|> filter(fn: (r) => r._measurement == "flow" and r.source_id == "S3" '
                    'and r._field == "flow_m3h") |> count()')
            with InfluxDBClient(url=S.influxdb_url, token=S.influxdb_token, org=S.influxdb_org) as c:
                n = sum(int(r.get_value()) for t in c.query_api().query(flux) for r in t.records)
            return n > 0, f"InfluxDB : {n} débits S3 enregistrés (10 dernières minutes réelles)"
        except Exception as exc:
            return False, f"InfluxDB : {exc}"
    import sqlite3

    with sqlite3.connect(S.sqlite_file) as con:
        n = con.execute("SELECT COUNT(*) FROM readings WHERE kind='flow' AND source_id='S3'").fetchone()[0]
    return n > 0, f"SQLite : {n} débits S3"


if __name__ == "__main__":
    print(f"Test de bout en bout — {datetime.now():%H:%M:%S}, broker {S.mqtt_host}:{S.mqtt_port}, "
          f"stockage {S.storage_backend}")
    sys.exit(main())
