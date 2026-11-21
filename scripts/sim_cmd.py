"""Envoie une commande au simulateur ou au consumer (évite les guillemets JSON sous PowerShell).

    python scripts/sim_cmd.py leak S3 1.5      # fuite de 1,5 m³/h sur le tronçon S3
    python scripts/sim_cmd.py flush 0.6        # rinçage des filtres 0,6 m³/h (0 = arrêt)
    python scripts/sim_cmd.py reset            # répare tout
    python scripts/sim_cmd.py new_normal       # bouton « nouveau normal » (consumer)
    python scripts/sim_cmd.py speed 1          # 1 s réelle par heure simulée
    python scripts/sim_cmd.py replan           # recharge le dernier plan
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paho.mqtt.client as mqtt  # noqa: E402

from aquamind_core.config import get_settings  # noqa: E402


def build(args: list[str]) -> tuple[str, dict]:
    name = args[0] if args else ""
    if name == "leak":
        return "leak", {"section": args[1] if len(args) > 1 else "S3",
                        "rate_m3h": float(args[2]) if len(args) > 2 else 1.5}
    if name == "flush":
        return "flush", {"rate_m3h": float(args[1]) if len(args) > 1 else 0.6}
    if name == "speed":
        return "speed", {"sim_seconds_per_hour": float(args[1]) if len(args) > 1 else 5.0}
    if name in ("reset", "new_normal", "replan"):
        return name, {}
    raise SystemExit(__doc__)


def main() -> None:
    name, body = build(sys.argv[1:])
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aquamind-cmd-{os.getpid()}")
    client.connect(s.mqtt_host, s.mqtt_port)
    client.loop_start()
    client.publish(f"{s.mqtt_base_topic}/cmd/{name}", json.dumps(body), qos=1).wait_for_publish(3)
    client.loop_stop()
    client.disconnect()
    print(f"envoyé : {s.mqtt_base_topic}/cmd/{name} {json.dumps(body)}")


if __name__ == "__main__":
    main()
