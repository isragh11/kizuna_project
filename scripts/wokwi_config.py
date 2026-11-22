"""Prépare l'ESP32 Wokwi : préfixe de topic aléatoire (.env) et fichier iot/wokwi/config.h.

    .\\.venv\\Scripts\\python scripts\\wokwi_config.py            # crée le préfixe s'il manque, écrit config.h
    .\\.venv\\Scripts\\python scripts\\wokwi_config.py --new      # nouveau préfixe (anciens messages ignorés)

Wokwi ne lit pas le .env : config.h contient le préfixe, le broker public et les certificats RACINES
(publics) qui servent à VÉRIFIER le certificat TLS du broker. Il ne contient aucun secret, mais il est
propre à votre installation : il n'est pas commité (voir config.h.example).
Seules les variables WOKWI_TOPIC_PREFIX, PUBLIC_MQTT_HOST et PUBLIC_MQTT_PORT du .env sont lues ou ajoutées.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from iot.protocol import PREFIX_RE  # noqa: E402

ENV = ROOT / ".env"
OUT = ROOT / "iot" / "wokwi" / "config.h"
# Autorités racines courantes, extraites du paquet certifi (Mozilla) : le broker public utilise l'une d'elles.
# Avast inspecte le TLS sur ce PC : impossible d'y lire la vraie chaîne du broker ; l'ESP32, lui, la voit
# (le trafic part des serveurs Wokwi). Plusieurs racines = vérification réelle, quelle que soit l'autorité.
ROOT_LABELS = ["ISRG Root X1", "ISRG Root X2", "DigiCert Global Root G2", "GTS Root R1",
               "Amazon Root CA 1", "USERTrust RSA Certification Authority"]


def root_bundle() -> tuple[str, list[str]]:
    import certifi

    pem = Path(certifi.where()).read_text(encoding="utf-8")
    found, blocks = [], []
    for label in ROOT_LABELS:
        m = re.search(r'# Label: "' + re.escape(label) + r'".*?(-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----)',
                      pem, re.S)
        if m:
            found.append(label)
            blocks.append(f"# {label}\n{m.group(1)}")
    if not blocks:
        sys.exit("Aucun certificat racine trouvé dans certifi : réinstaller requirements.txt")
    return "\n".join(blocks) + "\n", found


def ensure_env(new: bool) -> dict[str, str]:
    from dotenv import dotenv_values, set_key

    if not ENV.exists():
        sys.exit(".env introuvable : copier .env.example en .env (scripts/init_env.py)")
    values = dotenv_values(ENV)
    prefix = (values.get("WOKWI_TOPIC_PREFIX") or "").strip()
    if new or not PREFIX_RE.match(prefix):
        prefix = "aqm-" + secrets.token_hex(8)
        set_key(str(ENV), "WOKWI_TOPIC_PREFIX", prefix, quote_mode="never")
        print(f"Nouveau préfixe de topic écrit dans .env : {prefix[:8]}… (aléatoire, 16 caractères hexadécimaux)")
    host = (values.get("PUBLIC_MQTT_HOST") or "").strip() or "broker.hivemq.com"
    port = (values.get("PUBLIC_MQTT_PORT") or "").strip() or "8883"
    for key, val in (("PUBLIC_MQTT_HOST", host), ("PUBLIC_MQTT_PORT", port)):
        if not (values.get(key) or "").strip():
            set_key(str(ENV), key, val, quote_mode="never")
    if port != "8883":
        print("ATTENTION : PUBLIC_MQTT_PORT n'est pas 8883 (TLS). Le mode non chiffré n'est pas prévu.")
    return {"prefix": prefix, "host": host, "port": port}


def render(cfg: dict[str, str], bundle: str, labels: list[str], example: bool = False) -> str:
    prefix = "aqm-REMPLACEZ-MOI-0000" if example else cfg["prefix"]
    origin = ("EXEMPLE : générez le vrai fichier avec  .\\.venv\\Scripts\\python scripts\\wokwi_config.py"
              if example else
              "Généré par scripts/wokwi_config.py depuis .env — ne pas commiter (préfixe propre à votre installation).")
    return f"""// AquaMind — configuration de l'ESP32 Wokwi.
// {origin}
// SIMULATION UNIQUEMENT : broker public, jamais pour une vraie pompe.
#pragma once

#define AQ_WIFI_SSID "Wokwi-GUEST"
#define AQ_WIFI_PASS ""
#define AQ_WIFI_CHANNEL 6

#define AQ_BROKER "{cfg['host']}"
#define AQ_PORT {cfg['port']}
#define AQ_PREFIX "{prefix}"
#define AQ_DEVICE "esp32-wokwi"

// Certificats RACINES publics (Mozilla, via certifi) : {", ".join(labels)}.
// Le certificat du broker est VÉRIFIÉ contre ces racines ; aucun repli sans TLS.
static const char AQ_ROOT_CA[] = R"PEM(
{bundle})PEM";
"""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # console Windows (cp1252)
    ap = argparse.ArgumentParser(description="Préfixe aléatoire (.env) + iot/wokwi/config.h")
    ap.add_argument("--new", action="store_true", help="générer un nouveau préfixe")
    args = ap.parse_args()
    cfg = ensure_env(args.new)
    bundle, labels = root_bundle()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(cfg, bundle, labels), encoding="utf-8")
    (OUT.parent / "config.h.example").write_text(render(cfg, bundle, labels, example=True), encoding="utf-8")
    print(f"Écrit : {OUT.relative_to(ROOT)} (préfixe {cfg['prefix'][:8]}…, broker {cfg['host']}:{cfg['port']}, "
          f"{len(labels)} certificats racines)")
    print("Dans Wokwi : onglet « + » (nouveau fichier) → « config.h » → coller le contenu de ce fichier.")


if __name__ == "__main__":
    main()
