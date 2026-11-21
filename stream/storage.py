"""Stockage des mesures et des alertes : InfluxDB (par défaut) ou SQLite (secours), plus un tampon mémoire.

InfluxDB : measurement = kind (soil, flow, pv, tank), tag source_id, un champ par valeur ; les alertes
dans le measurement « alert » (tag section). SQLite : tables readings et alerts (data/stream.db).
Horodatage : heure RÉELLE de réception (UTC), pour que « les 60 dernières minutes » aient un sens
avec l'horloge accélérée ; l'heure simulée est gardée dans le champ sim_epoch (secondes).
Si InfluxDB ne répond pas au démarrage, on bascule sur SQLite : le projet n'est jamais bloqué.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Protocol

from aquamind_core.config import Settings
from aquamind_core.schemas import Alert, SensorReading

logger = logging.getLogger(__name__)
MEMORY_SIZE = 20_000


class Storage(Protocol):
    name: str

    def write_reading(self, reading: SensorReading) -> None: ...
    def write_alert(self, alert: Alert) -> None: ...
    def history(self, kind: str, source_id: str | None, minutes: float) -> list[dict[str, Any]]: ...
    def recent_alerts(self, limit: int) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sim_epoch(ts: datetime) -> float:
    return ts.timestamp() if ts.tzinfo else ts.replace(tzinfo=timezone.utc).timestamp()


class MemoryBuffer:
    """Derniers messages et alertes en mémoire (toujours actif, en plus du stockage durable)."""

    def __init__(self, size: int = MEMORY_SIZE) -> None:
        self.readings: deque[SensorReading] = deque(maxlen=size)
        self.alerts: deque[Alert] = deque(maxlen=500)


class InfluxStorage:
    name = "influx"

    def __init__(self, s: Settings) -> None:
        from influxdb_client import InfluxDBClient, WriteOptions

        self.bucket = s.influxdb_bucket
        self.client = InfluxDBClient(url=s.influxdb_url, token=s.influxdb_token, org=s.influxdb_org, timeout=5000)
        if not self.client.ping():
            raise ConnectionError(f"InfluxDB ne répond pas sur {s.influxdb_url}")
        self.write_api = self.client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=1000))

    def write_reading(self, r: SensorReading) -> None:
        from influxdb_client import Point

        p = Point(r.kind).tag("source_id", r.source_id).time(_now()).field("sim_epoch", _sim_epoch(r.ts))
        for k, v in r.values.items():
            p = p.field(k, float(v))
        self.write_api.write(bucket=self.bucket, record=p)

    def write_alert(self, a: Alert) -> None:
        from influxdb_client import Point

        p = (Point("alert").tag("section", a.section).tag("detector", a.detector).time(_now())
             .field("sim_epoch", _sim_epoch(a.ts)).field("alert_id", a.alert_id or "")
             .field("score", float(a.score)).field("est_loss_m3_day", a.est_loss_m3_day)
             .field("est_loss_kwh_day", a.est_loss_kwh_day).field("est_loss_tnd_day", a.est_loss_tnd_day)
             .field("confirmed_by_ai", int(a.confirmed_by_ai)).field("message", a.message))
        self.write_api.write(bucket=self.bucket, record=p)

    def history(self, kind: str, source_id: str | None, minutes: float) -> list[dict[str, Any]]:
        """Mesures des `minutes` dernières minutes réelles, une ligne par instant (champs pivotés)."""
        self.write_api.flush()
        src = f' and r.source_id == "{source_id}"' if source_id else ""
        flux = (f'from(bucket: "{self.bucket}") |> range(start: -{int(max(1, minutes))}m) '
                f'|> filter(fn: (r) => r._measurement == "{kind}"{src}) '
                '|> pivot(rowKey: ["_time", "source_id"], columnKey: ["_field"], valueColumn: "_value") '
                '|> sort(columns: ["_time"])')
        rows = []
        for table in self.client.query_api().query(flux):
            for r in table.records:
                v = r.values
                values = {k: x for k, x in v.items() if not k.startswith("_") and k not in
                          ("result", "table", "source_id", "sim_epoch")}
                sim = v.get("sim_epoch")
                rows.append({"ts": v["_time"].isoformat(), "source_id": v.get("source_id"),
                             "sim_ts": (datetime.fromtimestamp(sim, timezone.utc).replace(tzinfo=None).isoformat()
                                        if sim else None), "values": values})
        return rows

    def recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        flux = (f'from(bucket: "{self.bucket}") |> range(start: -30d) |> filter(fn: (r) => r._measurement == "alert") '
                '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
                f'|> sort(columns: ["_time"], desc: true) |> limit(n: {int(limit)})')
        out = []
        for table in self.client.query_api().query(flux):
            for r in table.records:
                v = r.values
                row = {k: (x.isoformat() if hasattr(x, "isoformat") else x) for k, x in v.items()
                       if k not in ("result", "table", "_start", "_stop", "_measurement")}
                row["ts"] = row.pop("_time", None)            # même forme que les alertes en direct
                row["confirmed_by_ai"] = bool(row.get("confirmed_by_ai"))
                out.append(row)
        return out

    def close(self) -> None:
        self.write_api.close()
        self.client.close()


class SQLiteStorage:
    name = "sqlite"

    def __init__(self, s: Settings) -> None:
        path = s.sqlite_file
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("CREATE TABLE IF NOT EXISTS readings (ts TEXT, kind TEXT, source_id TEXT, "
                         "field TEXT, value REAL, sim_ts TEXT)")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_readings ON readings(kind, source_id, ts)")
        self.con.execute("CREATE TABLE IF NOT EXISTS alerts (ts TEXT, section TEXT, json TEXT)")
        self.pending = 0
        logger.info("Stockage SQLite : %s", path)

    def write_reading(self, r: SensorReading) -> None:
        ts, sim_ts = _now().isoformat(), r.ts.isoformat()
        with self.lock:
            self.con.executemany("INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?)",
                                 [(ts, r.kind, r.source_id, k, float(v), sim_ts) for k, v in r.values.items()])
            self.pending += 1
            if self.pending >= 50:
                self.con.commit()
                self.pending = 0

    def write_alert(self, a: Alert) -> None:
        with self.lock:
            self.con.execute("INSERT INTO alerts VALUES (?, ?, ?)",
                             (_now().isoformat(), a.section, a.model_dump_json()))
            self.con.commit()

    def history(self, kind: str, source_id: str | None, minutes: float) -> list[dict[str, Any]]:
        since = datetime.fromtimestamp(_now().timestamp() - minutes * 60, timezone.utc).isoformat()
        q = ("SELECT ts, sim_ts, source_id, field, value FROM readings WHERE kind = ? AND ts >= ? "
             + ("AND source_id = ? " if source_id else "") + "ORDER BY ts")
        args = (kind, since, source_id) if source_id else (kind, since)
        rows: dict[tuple[str, str], dict[str, Any]] = {}
        with self.lock:
            self.con.commit()
            for ts, sim_ts, src, field_name, value in self.con.execute(q, args):
                row = rows.setdefault((ts, src), {"ts": ts, "source_id": src, "sim_ts": sim_ts, "values": {}})
                row["values"][field_name] = value
        return list(rows.values())

    def recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        with self.lock:
            return [json.loads(b) for (b,) in self.con.execute(
                "SELECT json FROM alerts ORDER BY ts DESC LIMIT ?", (int(limit),))]

    def close(self) -> None:
        with self.lock:
            self.con.commit()
            self.con.close()


def make_storage(s: Settings) -> Storage:
    """InfluxDB si STORAGE_BACKEND=influx et joignable, sinon SQLite."""
    if s.storage_backend == "influx":
        try:
            st = InfluxStorage(s)
            logger.info("Stockage InfluxDB : %s, bucket %s", s.influxdb_url, s.influxdb_bucket)
            return st
        except Exception as exc:
            logger.warning("InfluxDB indisponible (%s) : secours SQLite", exc)
    return SQLiteStorage(s)


def alert_to_dict(a: Alert) -> dict[str, Any]:
    return json.loads(a.model_dump_json())
