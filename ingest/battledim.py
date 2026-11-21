"""BattLeDIM 2020 (réseau urbain L-Town) : conversion des CSV en parquet.

Entrée (téléchargée manuellement, lue avec pandas uniquement) : data/raw/battledim/
  20XX_SCADA_Flows.csv / _Pressures.csv / _Levels.csv : sep=";", décimale ",",
    colonne "Timestamp" (%Y-%m-%d %H:%M:%S), pas de 5 min ; débits en m³/h, pressions/niveaux en m.
  20XX_Leakages.csv : débit de chaque fuite (m³/h) par conduite (colonnes = linkID).
  dataset_configuration.yaml : liste des fuites (linkID, début, fin, diamètre, type, pic).
  2018_Fixed_Leakages_Report.txt : heures de réparation des fuites réparées en 2018.

Sorties (data/processed/) :
  battledim_flows.parquet      index timestamp ; p227, p235, PUMP_1 (m³/h), 2018–2019
  battledim_pressures.parquet  index timestamp ; 33 capteurs n* (m), 2018–2019
  battledim_levels.parquet     index timestamp ; T1 (m), 2018 (seul fichier téléchargé)
  battledim_leaks.parquet      une ligne par fuite : début, fin/réparation, type, diamètre,
                               dates observées et débit max d'après les séries de fuite
  battledim_leak_flow.parquet  index timestamp ; débit total de fuite (m³/h), nb de fuites actives
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from aquamind_core.config import RAW_DIR
from ingest.sites import save_parquet

logger = logging.getLogger(__name__)

BATTLEDIM_DIR = RAW_DIR / "battledim"
YEARS = (2018, 2019)
DATASET_END = pd.Timestamp("2019-12-31 23:55")
_LEAK_LINE = re.compile(
    r"^-\s*(p\d+),\s*([\d-]+ [\d:]+),\s*([\d-]+ [\d:]+),\s*([\d.]+),\s*(\w+),\s*([\d-]+ [\d:]+)\s*$")
_FIXED_LINE = re.compile(r"^-\s*(p\d+),\s*([\d-]+ [\d:]+)\s*$")


class BattledimMissing(FileNotFoundError):
    """Le dossier data/raw/battledim/ est vide ou incomplet."""


def read_scada_csv(path: Path) -> pd.DataFrame:
    """Lit un CSV BattLeDIM (sep ';', décimale ',') ; index = Timestamp."""
    df = pd.read_csv(path, sep=";", decimal=",", parse_dates=["Timestamp"],
                     date_format="%Y-%m-%d %H:%M:%S")
    return df.set_index("Timestamp").rename_axis("timestamp").astype("float32")


def inspect(folder: Path = BATTLEDIM_DIR) -> list[Path]:
    """Liste les fichiers présents et affiche les colonnes de chaque CSV."""
    files = sorted(p for p in folder.glob("*") if p.is_file() and p.name != ".gitkeep")
    if not files:
        raise BattledimMissing(
            f"{folder} est vide : télécharger BattLeDIM (Zenodo, record 4017658) et le dézipper ici.")
    for p in files:
        line = f"{p.name:32s} {p.stat().st_size / 1e6:8.2f} Mo"
        if p.suffix == ".csv":
            header = pd.read_csv(p, sep=";", nrows=0).columns.tolist()
            line += f"  {len(header)} colonnes : {', '.join(header[:8])}{' …' if len(header) > 8 else ''}"
        logger.info(line)
    return files


def _concat_years(kind: str, years: tuple[int, ...] = YEARS) -> pd.DataFrame:
    frames = []
    for y in years:
        path = BATTLEDIM_DIR / f"{y}_SCADA_{kind}.csv"
        if path.exists():
            frames.append(read_scada_csv(path))
        else:
            logger.warning("Fichier absent : %s", path.name)
    if not frames:
        raise BattledimMissing(f"Aucun fichier *_SCADA_{kind}.csv dans {BATTLEDIM_DIR}")
    return pd.concat(frames).sort_index()


def parse_leak_config(path: Path) -> pd.DataFrame:
    """Fuites déclarées dans dataset_configuration.yaml (lecture texte, pas d'exécution)."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LEAK_LINE.match(line.strip())
        if m:
            link, start, end, diam, kind, peak = m.groups()
            rows.append({"link_id": link, "start": pd.Timestamp(start), "end": pd.Timestamp(end),
                         "diameter_m": float(diam), "leak_type": kind, "peak_time": pd.Timestamp(peak)})
    return pd.DataFrame(rows)


def parse_fixed_report(path: Path) -> dict[str, pd.Timestamp]:
    """Heures de réparation (2018_Fixed_Leakages_Report.txt)."""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _FIXED_LINE.match(line.strip())
        if m:
            out[m.group(1)] = pd.Timestamp(m.group(2))
    return out


def build_leaks() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Table des fuites (une ligne par fuite) et série du débit total de fuite."""
    paths = [BATTLEDIM_DIR / f"{y}_Leakages.csv" for y in YEARS]
    paths = [p for p in paths if p.exists()]
    if not paths:
        raise BattledimMissing(f"Aucun fichier *_Leakages.csv dans {BATTLEDIM_DIR}")
    leak_flows = pd.concat([read_scada_csv(p) for p in paths]).sort_index()
    leak_flows = leak_flows.fillna(0.0)  # colonnes propres à une année → 0 l'autre année

    config = parse_leak_config(BATTLEDIM_DIR / "dataset_configuration.yaml")
    fixed = parse_fixed_report(BATTLEDIM_DIR / "2018_Fixed_Leakages_Report.txt")
    if config.empty:  # secours : périodes déduites des séries de débit de fuite
        logger.warning("Configuration des fuites illisible : périodes déduites de *_Leakages.csv")
        config = pd.DataFrame({"link_id": leak_flows.columns})

    obs_rows = []
    for link in config["link_id"]:
        s = leak_flows[link] if link in leak_flows else pd.Series(dtype="float32")
        active = s[s > 0]
        obs_rows.append({
            "link_id": link,
            "observed_start": active.index.min() if len(active) else pd.NaT,
            "observed_end": active.index.max() if len(active) else pd.NaT,
            "max_flow_m3h": float(active.max()) if len(active) else 0.0,
            "mean_flow_m3h": float(active.mean()) if len(active) else 0.0,
        })
    leaks = config.merge(pd.DataFrame(obs_rows), on="link_id", how="left")
    if "start" not in leaks:
        leaks["start"], leaks["end"] = leaks["observed_start"], leaks["observed_end"]
    leaks["repair_time"] = leaks["link_id"].map(fixed)
    leaks["repaired"] = leaks["end"] < DATASET_END
    leaks["dataset_year"] = leaks["start"].dt.year
    leaks["duration_h"] = (leaks["end"] - leaks["start"]).dt.total_seconds() / 3600
    mismatch = leaks.dropna(subset=["repair_time"]).query("repair_time != end")
    if not mismatch.empty:
        logger.warning("Réparations incohérentes avec la configuration : %s", mismatch["link_id"].tolist())

    total = pd.DataFrame({"leak_flow_m3h": leak_flows.sum(axis=1).astype("float32"),
                          "n_active_leaks": (leak_flows > 0).sum(axis=1).astype("int16")})
    return leaks.sort_values("start").reset_index(drop=True), total


def run() -> dict[str, pd.DataFrame]:
    """Inspecte le dossier puis écrit les parquet BattLeDIM."""
    inspect()
    flows = _concat_years("Flows")
    pressures = _concat_years("Pressures")
    levels = _concat_years("Levels")
    leaks, leak_flow = build_leaks()

    out = {"battledim_flows": flows, "battledim_pressures": pressures,
           "battledim_levels": levels, "battledim_leaks": leaks, "battledim_leak_flow": leak_flow}
    for name, df in out.items():
        save_parquet(df, name)
    logger.info("BattLeDIM : %d fuites (%d réparées), %d capteurs de débit, %d de pression",
                len(leaks), int(leaks["repaired"].sum()), flows.shape[1], pressures.shape[1])
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    run()
