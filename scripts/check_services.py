"""Vérifie les services AquaMind (Docker ou natif) : InfluxDB et MQTT.

1. InfluxDB : GET /health == "pass", puis écriture et relecture d'un point de test
   dans le bucket (ignoré si STORAGE_BACKEND=sqlite → test SQLite à la place).
2. MQTT : publish/subscribe aller-retour sur aquamind/test.

Usage :  python scripts/check_services.py      (code retour 0 si tout est OK)
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aquamind_core  # noqa: E402,F401  (truststore avant requests)
import paho.mqtt.client as mqtt  # noqa: E402
import requests  # noqa: E402

from aquamind_core.config import Settings, get_settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("check_services")


def check_influx(s: Settings) -> bool:
    """Ping /health puis écrit et relit un point de test dans le bucket."""
    try:
        r = requests.get(f"{s.influxdb_url}/health", timeout=5)
        status = r.json().get("status")
    except (requests.RequestException, ValueError) as exc:
        log.error("InfluxDB injoignable sur %s : %s", s.influxdb_url, exc)
        return False
    if status != "pass":
        log.error("InfluxDB /health = %s", status)
        return False
    log.info("InfluxDB /health = pass")

    from influxdb_client import InfluxDBClient, Point
    from influxdb_client.client.write_api import SYNCHRONOUS

    marker = uuid.uuid4().hex[:8]
    try:
        with InfluxDBClient(url=s.influxdb_url, token=s.influxdb_token, org=s.influxdb_org) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=s.influxdb_bucket,
                record=Point("healthcheck").tag("marker", marker).field("value", 1.0),
            )
            flux = (
                f'from(bucket: "{s.influxdb_bucket}") |> range(start: -5m) '
                f'|> filter(fn: (r) => r._measurement == "healthcheck" and r.marker == "{marker}")'
            )
            rows = [rec for table in client.query_api().query(flux) for rec in table.records]
    except Exception as exc:  # noqa: BLE001 - on veut un diagnostic lisible
        log.error("Écriture/lecture InfluxDB échouée (token/org/bucket du .env ?) : %s", exc)
        return False
    if not rows:
        log.error("Point de test écrit mais introuvable à la relecture")
        return False
    log.info("InfluxDB écriture + lecture OK (bucket %s)", s.influxdb_bucket)
    return True


def check_sqlite(s: Settings) -> bool:
    """Secours : vérifie que la base SQLite est accessible en écriture."""
    path = s.sqlite_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS healthcheck (ts REAL)")
        con.execute("INSERT INTO healthcheck VALUES (?)", (time.time(),))
        con.execute("DELETE FROM healthcheck")
    log.info("SQLite OK (%s)", path)
    return True


def check_mqtt(s: Settings, timeout_s: float = 5.0) -> bool:
    """Publie un message sur aquamind/test et vérifie qu'on le reçoit."""
    topic, payload = "aquamind/test", f"ping-{uuid.uuid4().hex[:8]}"
    received = threading.Event()

    def on_connect(client: mqtt.Client, _u: object, _f: object, rc: object, _p: object) -> None:
        client.subscribe(topic, qos=1)

    def on_subscribe(client: mqtt.Client, *_args: object) -> None:
        client.publish(topic, payload, qos=1)

    def on_message(_c: mqtt.Client, _u: object, msg: mqtt.MQTTMessage) -> None:
        if msg.payload.decode() == payload:
            received.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"check-{uuid.uuid4().hex[:6]}")
    client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
    try:
        client.connect(s.mqtt_host, s.mqtt_port, keepalive=10)
    except OSError as exc:
        log.error("Broker MQTT injoignable sur %s:%s : %s", s.mqtt_host, s.mqtt_port, exc)
        return False
    client.loop_start()
    ok = received.wait(timeout_s)
    client.loop_stop()
    client.disconnect()
    if ok:
        log.info("MQTT publish/subscribe OK sur %s:%s (%s)", s.mqtt_host, s.mqtt_port, topic)
    else:
        log.error("MQTT : message de test non reçu en %.0f s", timeout_s)
    return ok


def main() -> int:
    """Lance toutes les vérifications et renvoie un code retour."""
    s = get_settings()
    storage_ok = check_sqlite(s) if s.storage_backend == "sqlite" else check_influx(s)
    mqtt_ok = check_mqtt(s)
    log.info("Résumé : stockage(%s)=%s  mqtt=%s", s.storage_backend,
             "OK" if storage_ok else "KO", "OK" if mqtt_ok else "KO")
    return 0 if storage_ok and mqtt_ok else 1


if __name__ == "__main__":
    sys.exit(main())
