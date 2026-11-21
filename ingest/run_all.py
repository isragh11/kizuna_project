"""Lance toute la collecte (Sidi Bouzid + Niigata + BattLeDIM) et affiche un résumé.

Usage :
  python -m ingest.run_all                    # en ligne (cache disque utilisé si frais)
  OFFLINE_MODE=true python -m ingest.run_all  # uniquement data/cache, aucun accès réseau
Code retour : 0 si tous les fichiers ont été produits, 1 sinon.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable

import pandas as pd

from aquamind_core.config import get_settings
from ingest import battledim, open_meteo, pvgis, soilgrids
from ingest.sites import get_sites

logger = logging.getLogger("ingest.run_all")


def _date_range(df: pd.DataFrame) -> tuple[str, str]:
    """Dates min/max : index temporel, sinon colonnes start/end (table des fuites)."""
    if isinstance(df.index, pd.DatetimeIndex) and len(df):
        return str(df.index.min()), str(df.index.max())
    if {"start", "end"} <= set(df.columns):
        return str(df["start"].min()), str(df["end"].max())
    return "—", "—"


def _missing(df: pd.DataFrame) -> tuple[float, str]:
    """% de valeurs manquantes (global) et colonne la plus incomplète."""
    if df.empty:
        return 100.0, "—"
    by_col = df.isna().mean() * 100
    worst = by_col.idxmax()
    return float(df.isna().to_numpy().mean() * 100), (f"{worst} {by_col[worst]:.1f}%" if by_col[worst] > 0 else "—")


def summarize(results: dict[str, pd.DataFrame]) -> str:
    """Tableau texte : fichier, lignes, colonnes, dates min/max, % manquants."""
    lines = [f"{'fichier (data/processed/)':34s} {'lignes':>8s} {'col':>4s}  {'date min':19s}  "
             f"{'date max':19s}  {'% manq.':>7s}  colonne la plus incomplète"]
    lines.append("-" * len(lines[0]) + "-" * 12)
    for name, df in results.items():
        dmin, dmax = _date_range(df)
        pct, worst = _missing(df)
        lines.append(f"{name + '.parquet':34s} {len(df):8d} {df.shape[1]:4d}  {dmin[:19]:19s}  "
                     f"{dmax[:19]:19s}  {pct:6.2f}%  {worst}")
    return "\n".join(lines)


def main() -> int:
    """Exécute chaque étape ; une étape en échec n'empêche pas les suivantes."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s : %(message)s",
                        datefmt="%H:%M:%S")
    settings = get_settings()
    logger.info("Collecte AquaMind — OFFLINE_MODE=%s", settings.offline_mode)
    t0 = time.perf_counter()

    steps: list[tuple[str, Callable[[], dict[str, pd.DataFrame]]]] = []
    for site in get_sites().values():
        steps += [(f"Open-Meteo {site.name}", lambda s=site: open_meteo.run(s)),
                  (f"PVGIS {site.name}", lambda s=site: pvgis.run(s)),
                  (f"SoilGrids {site.name}", lambda s=site: soilgrids.run(s))]
    steps.append(("BattLeDIM", battledim.run))

    results: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    for label, step in steps:
        logger.info("=== %s", label)
        try:
            results.update(step())
        except battledim.BattledimMissing as exc:
            failures.append(label)
            logger.error("%s — RAPPEL : télécharger BattLeDIM dans data/raw/battledim/", exc)
        except Exception as exc:  # noqa: BLE001 - on continue avec les autres sources
            failures.append(label)
            logger.exception("Échec de l'étape %s : %s", label, exc)

    print("\n" + summarize(results))
    print(f"\nOFFLINE_MODE={settings.offline_mode} — {len(results)} fichiers en "
          f"{time.perf_counter() - t0:.1f} s" + (f" — ÉCHECS : {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
