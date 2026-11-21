"""Client HTTP JSON avec cache disque (data/cache/).

Règles :
- réponse fraîche en cache (âge < ttl) → aucun appel réseau ;
- sinon : requête (timeout 30 s, 3 tentatives avec backoff exponentiel) ;
- si le réseau échoue → on sert la dernière réponse valide (même périmée) ;
- OFFLINE_MODE=true → uniquement le cache, jamais le réseau.
La vérification TLS n'est jamais désactivée (truststore, cf. aquamind_core.tls).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from aquamind_core.config import CACHE_DIR, get_settings
from aquamind_core.tls import enable_system_trust

enable_system_trust()
logger = logging.getLogger(__name__)

TIMEOUT_S = 30
ATTEMPTS = 3
BACKOFF_S = 2.0
USER_AGENT = "AquaMind-hackathon/0.1 (python-requests)"

# dict, ou liste de paires si un paramètre se répète (ex. SoilGrids : property=clay&property=sand)
Params = dict[str, Any] | list[tuple[str, Any]]


class CacheMiss(RuntimeError):
    """Pas de réponse en cache alors que le réseau est interdit ou indisponible."""


class ApiError(RuntimeError):
    """Erreur HTTP définitive (4xx) renvoyée par l'API ; `reason` = message de l'API."""

    def __init__(self, status: int, reason: str, url: str) -> None:
        super().__init__(f"HTTP {status} sur {url} : {reason}")
        self.status = status
        self.reason = reason


def _cache_path(cache_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", cache_key)
    if len(safe) > 120:  # noms de fichiers raisonnables sous Windows
        safe = safe[:80] + "_" + hashlib.sha1(cache_key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{safe}.json"


def _read_cache(path: Path) -> tuple[Any, float] | None:
    """Retourne (données, âge en heures) ou None si absent/illisible."""
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(entry["fetched_at"])
        age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
        return entry["data"], age_h
    except (ValueError, KeyError, OSError) as exc:
        logger.warning("Cache illisible %s : %s", path.name, exc)
        return None


def _write_cache(path: Path, url: str, params: Params, data: Any) -> None:
    """Écriture atomique (fichier temporaire puis renommage)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "params": params,
        "data": data,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _api_reason(resp: requests.Response) -> str:
    """Extrait le message d'erreur d'une réponse (JSON {'reason'|'message'} ou texte)."""
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("reason") or body.get("message") or body.get("detail") or body)
    except ValueError:
        pass
    return resp.text[:300]


def _fetch(url: str, params: Params) -> Any:
    """Requête GET avec tentatives ; lève ApiError (4xx) ou requests.RequestException."""
    last_exc: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise ApiError(resp.status_code, _api_reason(resp), url)  # inutile de réessayer
            resp.raise_for_status()
            return resp.json()
        except ApiError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < ATTEMPTS:
                wait = BACKOFF_S * 2 ** (attempt - 1)
                logger.warning("Tentative %d/%d échouée (%s) ; nouvel essai dans %.0f s",
                               attempt, ATTEMPTS, type(exc).__name__, wait)
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def get_json(url: str, params: Params, cache_key: str, ttl_hours: float | None = 24) -> Any:
    """GET JSON avec cache disque.

    ttl_hours=None : la réponse en cache n'expire jamais (données d'archive).
    Lève CacheMiss si aucune donnée n'est disponible (hors-ligne sans cache),
    ApiError si l'API refuse la requête (et qu'aucun cache n'existe).
    """
    path = _cache_path(cache_key)
    cached = _read_cache(path)

    if get_settings().offline_mode:
        if cached is None:
            raise CacheMiss(f"OFFLINE_MODE : pas de cache pour '{cache_key}'")
        logger.info("OFFLINE_MODE : cache '%s' (âge %.1f h)", cache_key, cached[1])
        return cached[0]

    if cached is not None and (ttl_hours is None or cached[1] < ttl_hours):
        logger.debug("Cache frais '%s' (âge %.1f h)", cache_key, cached[1])
        return cached[0]

    try:
        data = _fetch(url, params)
    except ApiError:
        if cached is None:
            raise
        logger.warning("API en erreur ; cache périmé utilisé pour '%s'", cache_key)
        return cached[0]
    except requests.RequestException as exc:
        if cached is None:
            raise CacheMiss(f"Réseau indisponible et pas de cache pour '{cache_key}' : {exc}") from exc
        logger.warning("Réseau indisponible (%s) ; dernière réponse valide utilisée pour '%s' (âge %.1f h)",
                       type(exc).__name__, cache_key, cached[1])
        return cached[0]

    _write_cache(path, url, params, data)
    logger.info("Téléchargé et mis en cache : '%s'", cache_key)
    return data
