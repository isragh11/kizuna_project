"""Calendrier cultural : culture active, stade et Kc du jour pour chaque parcelle (FAO-56).

Pour une culture annuelle, le Kc vaut kc_ini pendant le stade initial, monte linéairement jusqu'à kc_mid
pendant le développement, reste à kc_mid en mi-saison, puis descend linéairement jusqu'à kc_end en
arrière-saison. Hors saison, la parcelle est en jachère (Kc 0, besoin nul). Hypothèses :
data/static/crop_calendar.json.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from aquamind_core.config import load_crop_calendar, load_crops
from aquamind_core.schemas import Crop, Farm, GrowthStage, Plot

STAGE_NAMES = ["initial", "développement", "mi-saison", "arrière-saison"]


def _season_start(start_mmdd: str, day: date) -> date:
    """Dernière date de plantation ≤ day (cette année ou l'année précédente)."""
    month, dom = (int(v) for v in start_mmdd.split("-"))
    this_year = date(day.year, month, dom)
    return this_year if this_year <= day else date(day.year - 1, month, dom)


def crop_state(crop: Crop, spec: dict[str, Any], day: date) -> tuple[float, GrowthStage, str] | None:
    """(Kc du jour, stade ini/mid/end, note lisible) ; None si la culture est hors saison."""
    if spec.get("perennial"):
        if day.month in spec.get("rest_months", []):
            return crop.kc_ini, "ini", "repos hivernal"
        return crop.kc_mid, "mid", "saison d'irrigation"
    start = _season_start(spec["start"], day)
    elapsed = (day - start).days
    s_ini, s_dev, s_mid, s_late = spec["stage_days"]
    total = s_ini + s_dev + s_mid + s_late
    if elapsed >= total:
        return None
    note = f"jour {elapsed + 1}/{total}"
    if elapsed < s_ini:
        return crop.kc_ini, "ini", f"{STAGE_NAMES[0]}, {note}"
    if elapsed < s_ini + s_dev:
        frac = (elapsed - s_ini) / s_dev
        kc_value = crop.kc_ini + frac * (crop.kc_mid - crop.kc_ini)
        return kc_value, "ini" if frac < 0.5 else "mid", f"{STAGE_NAMES[1]}, {note}"
    if elapsed < s_ini + s_dev + s_mid:
        return crop.kc_mid, "mid", f"{STAGE_NAMES[2]}, {note}"
    frac = (elapsed - s_ini - s_dev - s_mid) / s_late
    kc_value = crop.kc_mid + frac * (crop.kc_end - crop.kc_mid)
    return kc_value, "end", f"{STAGE_NAMES[3]}, {note}"


def apply_calendar(farm: Farm, day: date) -> Farm:
    """Ferme du jour : pour chaque parcelle, la première culture de sa rotation qui est en saison.

    Une ferme absente du calendrier est renvoyée telle quelle (stades de farm.json)."""
    cal = load_crop_calendar()
    rotations = cal.get("farms", {}).get(farm.id)
    if not rotations:
        return farm
    crops = load_crops()
    plots: list[Plot] = []
    for plot in farm.plots:
        options = rotations.get(plot.id, [{"crop": None}])
        chosen = None
        for opt in options:
            key = opt["crop"]
            if key is None:
                continue
            state = crop_state(crops[key], cal["crops"][key], day)
            if state is not None:
                chosen = (opt, crops[key], state)
                break
        if chosen is None:
            opt = options[0]
            crop = crops[opt["crop"]] if opt.get("crop") else plot.crop
            update = {"crop": crop, "kc_value": 0.0, "growth_stage": "ini",
                      "season_note": "jachère (hors saison)"}
        else:
            opt, crop, (kc_value, stage, note) = chosen
            update = {"crop": crop, "kc_value": round(kc_value, 4), "growth_stage": stage,
                      "season_note": note}
        for field in ("irrigation_method", "irrigation_efficiency"):
            if field in opt:
                update[field] = opt[field]
        plots.append(plot.model_copy(update=update))
    return farm.model_copy(update={"plots": plots})
