"""Affiche les dernières mesures stockées (InfluxDB ou SQLite) — en attendant GET /history (Phase 6).

    python scripts/show_history.py flow S3 10       # 10 dernières minutes réelles, tronçon S3
    python scripts/show_history.py alert            # dernières alertes
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aquamind_core.config import get_settings  # noqa: E402

FLUX = """from(bucket: "{bucket}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r._measurement == "{kind}"{source})
  |> filter(fn: (r) => r._field != "sim_epoch")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 15)"""


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "flow"
    source = sys.argv[2] if len(sys.argv) > 2 and kind != "alert" else None
    minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    s = get_settings()
    if s.storage_backend == "influx":
        from influxdb_client import InfluxDBClient

        flux = FLUX.format(bucket=s.influxdb_bucket, minutes=minutes, kind=kind,
                           source=f' and r.source_id == "{source}"' if source else "")
        print("Requête Flux (à coller aussi dans l'explorateur http://localhost:8086) :\n" + flux + "\n")
        with InfluxDBClient(url=s.influxdb_url, token=s.influxdb_token, org=s.influxdb_org) as c:
            for table in c.query_api().query(flux):
                for r in table.records:
                    vals = {k: v for k, v in r.values.items() if not k.startswith(("_start", "_stop", "result",
                                                                                     "table", "_measurement"))}
                    print(json.dumps(vals, default=str, ensure_ascii=False))
        return
    with sqlite3.connect(s.sqlite_file) as con:
        if kind == "alert":
            for (body,) in con.execute("SELECT json FROM alerts ORDER BY ts DESC LIMIT 10"):
                print(body)
        else:
            q = ("SELECT ts, sim_ts, source_id, field, value FROM readings WHERE kind = ? "
                 + ("AND source_id = ? " if source else "") + "ORDER BY ts DESC LIMIT 30")
            for row in con.execute(q, (kind, source) if source else (kind,)):
                print(row)


if __name__ == "__main__":
    main()
