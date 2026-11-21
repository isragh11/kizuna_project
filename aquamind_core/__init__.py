"""AquaMind — logique métier pure (aucune dépendance web).

Point d'entrée commun : tout import de `aquamind_core` (API, ingestion,
simulateur, consumer, scripts) active la vérification TLS par le magasin du
système (voir `aquamind_core.tls`).
"""

from aquamind_core.tls import enable_system_trust

enable_system_trust()
