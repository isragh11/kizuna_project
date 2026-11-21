"""Santé, ferme, prévision, métriques des modèles."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from aquamind_core.agronomy import hargreaves_et0, plot_kc, plot_need_m3
from aquamind_core.config import ARTIFACTS_DIR, load_farm
from aquamind_core.crop_calendar import apply_calendar
from aquamind_core.forecast import et0_mae, live_inputs, recent_daily

router = APIRouter(tags=["général"])
STALE_AFTER_S = 30.0
FARM_FILE = {"tunisia": "farm.json", "japan": "farm_japan.json"}


def aqua(request: Request):
    return request.app.state.aqua


@router.get("/health", summary="État de l'API, du broker, du stockage et du plan")
def health(request: Request) -> dict[str, Any]:
    st = aqua(request)
    c = st.consumer
    mqtt_ok = bool(c and c.connected.is_set())
    storage = c.storage.name if c else None
    age = round(time.monotonic() - c.last_reading_at, 1) if c and c.last_reading_at else None
    sim_alive = age is not None and age <= STALE_AFTER_S
    reasons = []
    if st.settings.api_embed_consumer and not mqtt_ok:
        reasons.append("broker MQTT injoignable")
    if c is not None and not sim_alive:
        reasons.append("aucune mesure depuis plus de 30 s : simulateur arrêté ou figé" if age is not None
                       else "aucune mesure reçue depuis le démarrage de l'API")
    return {
        "status": "ok" if not reasons else "degraded", "reasons": reasons,
        "last_reading_age_s": age,
        "simulator": {"alive": sim_alive, "last_reading_age_s": age, "status": c.sim_status if c else None},
        "mqtt": mqtt_ok, "storage": storage,
        "consumer": {"embedded": c is not None, "ready": bool(c and c.ready),
                     "mode": c.monitor.mode if c else None, "stats": c.stats if c else None},
        "plan": st.plan_meta, "scheduler": bool(st.scheduler and st.scheduler.running),
        "websocket_clients": len(st.hub.clients), "started_at": st.started_at.isoformat(),
    }


@router.get("/farm", summary="Ferme du jour (culture et stade selon le calendrier cultural)")
def farm(site: Literal["tunisia", "japan"] = "tunisia", day: date | None = None) -> dict[str, Any]:
    day = day or date.today()
    f = apply_calendar(load_farm(FARM_FILE[site]), day)
    data = json.loads(f.model_dump_json())
    for p, raw in zip(f.plots, data["plots"]):
        raw["kc_effective"] = round(plot_kc(p), 3)
        raw["section"] = {"A": "S2", "B": "S3", "C": "S4"}.get(p.id)
    return {"site": site, "date": day.isoformat(), "farm": data,
            "topology": {"S1": "conduite principale (sortie de cuve)", "S2": "parcelle A", "S3": "parcelle B",
                         "S4": "parcelle C", "S5": "retour vers la cuve"}}


@router.get("/forecast", summary="ET0 prévue (modèle, Open-Meteo, Hargreaves) et besoins par parcelle")
def forecast(site: Literal["tunisia", "japan"] = "tunisia") -> dict[str, Any]:
    try:
        inputs = live_inputs(site, days=2)
        daily = recent_daily(site)
    except Exception as exc:
        raise HTTPException(503, f"prévision indisponible : {exc}") from exc
    from aquamind_core.config import get_settings

    s = get_settings()
    lat = s.farm_lat if site == "tunisia" else s.japan_lat
    days = []
    for d in range(inputs.days):
        day = inputs.start + timedelta(days=d)
        ts = pd.Timestamp(day)
        row = daily.loc[ts] if ts in daily.index else None
        om = float(row["et0_fao_evapotranspiration"]) if row is not None else None
        harg = (hargreaves_et0(float(row["temperature_2m_max"]), float(row["temperature_2m_min"]), lat,
                               day.timetuple().tm_yday) if row is not None else None)
        farm_day = apply_calendar(load_farm(FARM_FILE[site]), day)
        et0 = inputs.et0_mm[d]
        days.append({
            "date": day.isoformat(), "et0_used_mm": et0, "source": inputs.sources[d],
            "et0_model_mm": et0 if "LightGBM" in inputs.sources[d] else None,
            "et0_open_meteo_mm": round(om, 2) if om is not None else None,
            "et0_hargreaves_mm": round(harg, 2) if harg is not None else None,
            "tmax_c": inputs.tmax_c[d], "rain_mm": inputs.rain_mm[d], "pv_day_factor": inputs.pv_day_factors[d],
            "needs": {p.id: {"crop": p.crop.name, "stage": p.season_note, "kc": round(plot_kc(p), 3),
                             "need_m3": round(plot_need_m3(p, et0, inputs.rain_mm[d]), 2)}
                      for p in farm_day.plots},
        })
    return {"site": site, "model_mae_mm": et0_mae(site), "days": days}


@lru_cache(maxsize=1)
def _sensitivity() -> list[dict[str, Any]]:
    from aquamind_core.scenarios import sensitivity

    return sensitivity("tunisia_heatwave")


@router.get("/models/sensitivity", summary="Sensibilité : valeur de chaque culture ±30 % (canicule)")
def models_sensitivity() -> dict[str, Any]:
    rows = _sensitivity()
    gains = [r["gain_vs_trad_tnd"] for r in rows]
    return {"scenario": "tunisia_heatwave", "rows": rows,
            "summary": f"Gain vs traditionnel toujours positif ({min(gains):+.0f} à {max(gains):+.0f} TND sur 48 h), "
                       "part solaire inchangée, olivier ≥ 30 % (palier de survie) ; le partage tomate / piment "
                       "dépend de l'hypothèse de prix.",
            "label": "estimation simulée"}


def _read(name: str) -> dict[str, Any]:
    with open(ARTIFACTS_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@router.get("/models/metrics", summary="Métriques des modèles (ET0, fuites BattLeDIM, River / règle)")
def models_metrics() -> dict[str, Any]:
    et0, leak, river = _read("et0_metrics.json"), _read("leak_metrics.json"), _read("river_metrics.json")
    thr = _read("leak_threshold.json")
    ms = river.get("multi_seed", {}).get("summary", {})
    pick = lambda k: {m: ms.get(f"{k} | {m}") for m in  # noqa: E731
                      ("Règle adaptative", "River seul", "Règle fixe")}
    return {
        "summary": {
            "et0": {site: {"mae_mm": v["lightgbm"]["MAE"], "gain_vs_persistence_pct": v["gain_mae_vs_persistence_pct"]}
                    for site, v in et0["test"].items()},
            "et0_farm": et0.get("farm_translation"),
            "leak_battledim": {"recommended_config": thr.get("recommended_config"),
                               "criterion_met": thr.get("criterion_met"),
                               "use_for_alerting": thr.get("use_for_alerting"),
                               "note": "indicateur lent seulement : ne bat pas des inspections régulières "
                                       "(docs/RAPPORT_MODELES.md §7)"},
            "live_detection": {"recommended_mode": river.get("multi_seed", {}).get("recommended_mode"),
                               "normal": pick("normal"), "drift": pick("dérive"),
                               "note": "5 graines, estimation simulée (notebook 04 §6 ter)"},
        },
        "et0_metrics": et0, "leak_metrics": leak, "river_metrics": river,
    }
