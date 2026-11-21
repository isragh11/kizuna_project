"""Crée .env à partir de .env.example et génère les secrets InfluxDB.

Remplace uniquement les valeurs « change-me… » de INFLUXDB_TOKEN et
INFLUXDB_ADMIN_PASSWORD par secrets.token_hex(32). N'affiche jamais les secrets.
Idempotent : un .env déjà configuré n'est pas modifié.

Usage :  python scripts/init_env.py
"""

from __future__ import annotations

import logging
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
SECRET_KEYS = ("INFLUXDB_TOKEN", "INFLUXDB_ADMIN_PASSWORD")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("init_env")


def main() -> None:
    """Copie .env.example si besoin puis remplit les secrets manquants."""
    if not ENV.exists():
        shutil.copyfile(EXAMPLE, ENV)
        log.info(".env créé à partir de .env.example")

    lines = ENV.read_text(encoding="utf-8").splitlines()
    changed: list[str] = []
    for i, line in enumerate(lines):
        key, sep, value = line.partition("=")
        if sep and key.strip() in SECRET_KEYS and (not value.strip() or value.strip().startswith("change-me")):
            lines[i] = f"{key.strip()}={secrets.token_hex(32)}"
            changed.append(key.strip())

    if changed:
        ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Secrets générés dans .env : %s (valeurs non affichées)", ", ".join(changed))
    else:
        log.info(".env déjà configuré, rien à faire")


if __name__ == "__main__":
    main()
