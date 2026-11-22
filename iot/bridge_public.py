"""Pont MQTT : broker PUBLIC (Wokwi / ESP32) ⇄ Mosquitto local (AquaMind), dans les deux sens.

    .\\.venv\\Scripts\\python -m iot.bridge_public

⚠ MODE RÉSERVÉ À LA SIMULATION. Le broker public (broker.hivemq.com) est ouvert à tous : n'importe qui
connaissant le préfixe peut lire les messages ou en publier. Ne JAMAIS l'utiliser pour piloter une vraie
pompe. Pour du vrai matériel : broker privé, authentification, TLS mutuel.

Garde-fous :
- TLS sur 8883, certificat ET nom d'hôte du serveur VÉRIFIÉS (aquamind_core.tls.verified_context :
  magasins du système + certifi ; la vérification n'est jamais désactivée) ;
- préfixe de topic aléatoire (WOKWI_TOPIC_PREFIX dans .env) ;
- seuls les topics de iot/protocol.py sont relayés, chacun dans UN sens (pas de boucle possible) ;
- charge utile : objet JSON ≤ 4 Ko, sinon ignorée ; débit montant limité (le broker est public) ;
- Mosquitto n'est pas modifié : le pont est un simple client des deux brokers.
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import ssl
import sys
import threading
import time

import paho.mqtt.client as mqtt

from aquamind_core.config import get_settings
from aquamind_core.tls import verified_context
from iot import protocol

logger = logging.getLogger("bridge")
UP_RATE_PER_S = 40            # messages/s acceptés depuis le broker public (ESP32 : ~8/s)
UP_BURST = 80


class TokenBucket:
    def __init__(self, rate: float, burst: float, clock=time.monotonic) -> None:
        self.rate, self.burst, self.clock = rate, burst, clock
        self.tokens, self.at = burst, clock()

    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class Bridge:
    def __init__(self, prefix: str, local_base: str, local_host: str, local_port: int,
                 public_host: str, public_port: int) -> None:
        if not protocol.PREFIX_RE.match(prefix):
            raise ValueError("WOKWI_TOPIC_PREFIX invalide : lancer `python -m scripts.wokwi_config`")
        self.prefix, self.base = prefix, local_base
        self.stats = {"down": 0, "up": 0, "rejected": 0, "throttled": 0}
        self.bucket = TokenBucket(UP_RATE_PER_S, UP_BURST)
        tag = secrets.token_hex(3)
        self.local = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aquamind-bridge-local-{tag}")
        self.public = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aquamind-bridge-{tag}")

        ctx = verified_context()                           # CERT_REQUIRED + nom d'hôte ; magasins système + certifi
        self.public.tls_set_context(ctx)
        for c in (self.local, self.public):
            c.reconnect_delay_set(min_delay=1, max_delay=15)
        self.local.on_connect = self._on_local_connect
        self.public.on_connect = self._on_public_connect
        self.local.on_message = self._from_local
        self.public.on_message = self._from_public
        self.local.on_disconnect = lambda *a: logger.warning("Mosquitto local déconnecté : reconnexion…")
        self.public.on_disconnect = lambda *a: logger.warning("Broker public déconnecté : reconnexion…")
        self.hosts = ((local_host, local_port), (public_host, public_port))

    # ------------------------------------------------------------------ connexions
    def _on_local_connect(self, client, userdata, flags, rc, props) -> None:
        if rc.is_failure:
            logger.error("Mosquitto local refuse la connexion : %s", rc)
            return
        client.subscribe([(f"{self.base}/{t}", 1) for t in protocol.DOWN_TOPICS])
        logger.info("Local %s:%s connecté — relaie vers l'appareil : %s", *self.hosts[0], ", ".join(protocol.DOWN_TOPICS))

    def _on_public_connect(self, client, userdata, flags, rc, props) -> None:
        if rc.is_failure:
            logger.error("Broker public refuse la connexion : %s", rc)
            return
        client.subscribe([(f"{self.prefix}/{t}", 1) for t in protocol.UP_TOPICS])
        logger.info("Public %s:%s connecté en TLS vérifié — préfixe %s… — relaie vers AquaMind : %s",
                    *self.hosts[1], self.prefix[:8], ", ".join(protocol.UP_TOPICS))

    # ------------------------------------------------------------------ relais
    def _from_local(self, client, userdata, msg) -> None:
        rel = protocol.relative(msg.topic, self.base)
        if rel is None or not protocol.allowed("down", rel):
            return
        if protocol.decode(msg.payload) is None:
            self.stats["rejected"] += 1
            return
        self.public.publish(f"{self.prefix}/{rel}", msg.payload, qos=1, retain=msg.retain)
        self.stats["down"] += 1

    def _from_public(self, client, userdata, msg) -> None:
        rel = protocol.relative(msg.topic, self.prefix)
        if rel is None or not protocol.allowed("up", rel):
            return
        if protocol.decode(msg.payload) is None:
            self.stats["rejected"] += 1
            return
        # La limite de débit ne vise que les MESURES (débits, sol). États des actionneurs, demandes des boutons,
        # battements de cœur : jamais jetés (un arrêt d'urgence ne doit pas se perdre dans un flot de mesures).
        if protocol.is_telemetry(rel) and not self.bucket.take():
            self.stats["throttled"] += 1
            return
        self.local.publish(f"{self.base}/{rel}", msg.payload, qos=1, retain=msg.retain)
        self.stats["up"] += 1

    # ------------------------------------------------------------------ boucle
    def run(self, stop: threading.Event) -> None:
        (lh, lp), (ph, pp) = self.hosts
        self.local.connect_async(lh, lp, keepalive=30)
        self.public.connect_async(ph, pp, keepalive=30)
        self.local.loop_start()
        self.public.loop_start()
        last = time.monotonic()
        try:
            while not stop.wait(1.0):
                if time.monotonic() - last >= 30:
                    last = time.monotonic()
                    logger.info("Pont : %s", self.stats)
        finally:
            for c in (self.public, self.local):
                c.loop_stop()
                c.disconnect()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    if not s.wokwi_topic_prefix:
        sys.exit("WOKWI_TOPIC_PREFIX absent de .env : lancer d'abord `python -m scripts.wokwi_config`")
    logger.warning("MODE SIMULATION : broker public %s — jamais pour une vraie pompe.", s.public_mqtt_host)
    bridge = Bridge(s.wokwi_topic_prefix, s.mqtt_base_topic, s.mqtt_host, s.mqtt_port,
                    s.public_mqtt_host, s.public_mqtt_port)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    bridge.run(stop)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
