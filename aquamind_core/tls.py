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

logger = logging.getLogger(__name__)
_injected = False


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
