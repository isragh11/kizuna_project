"""Vérification TLS via le magasin de certificats du système (truststore).

Pourquoi : un antivirus qui inspecte le HTTPS (ex. Avast) re-signe les
connexions avec sa propre racine, installée dans le magasin Windows mais absente
du bundle `certifi` utilisé par `requests`. truststore fait vérifier les
certificats par l'OS : la vérification reste complète (jamais désactivée).

IMPORTANT : l'injection doit avoir lieu AVANT l'import de requests/urllib3
(sinon RecursionError dans ssl). C'est garanti par `aquamind_core/__init__.py`
et par les `__init__.py` des packages ingest, api, simulator, stream.
"""

from __future__ import annotations

import logging
import ssl

logger = logging.getLogger(__name__)
_injected = False
# Classe d'origine, capturée AVANT l'injection de truststore (ce module est importé en premier).
_STDLIB_SSLCONTEXT = ssl.SSLContext


def verified_context() -> ssl.SSLContext:
    """Contexte TLS standard de Python, VÉRIFIÉ (certificat + nom d'hôte), pour paho-mqtt.

    truststore n'est pas compatible avec la poignée de main en deux temps de paho (do_handshake séparé :
    « Peer sent no certificates to verify »). Ce contexte n'utilise donc pas truststore, mais il charge
    les magasins de certificats du système (Windows : ROOT et CA, racine d'un antivirus incluse) ET le
    bundle certifi. La vérification n'est jamais désactivée."""
    # PROTOCOL_TLS_CLIENT impose déjà CERT_REQUIRED et check_hostname. (On ne les réaffecte pas : après
    # l'injection de truststore, les SETTERS de ssl.SSLContext bouclent à l'infini ; les lire est sûr.)
    ctx = _STDLIB_SSLCONTEXT(ssl.PROTOCOL_TLS_CLIENT)
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        raise RuntimeError("contexte TLS sans vérification : refusé")
    ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


def enable_system_trust() -> bool:
    """Injecte truststore dans `ssl` (idempotent). Retourne True si actif."""
    global _injected
    if _injected:
        return True
    try:
        import truststore
    except ImportError:
        logger.warning("truststore absent : vérification TLS via certifi uniquement")
        return False
    truststore.inject_into_ssl()
    _injected = True
    logger.debug("truststore actif : certificats vérifiés par le magasin du système")
    return True
