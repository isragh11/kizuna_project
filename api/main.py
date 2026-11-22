"""API FastAPI d'AquaMind.

    .\\.venv\\Scripts\\python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
    puis http://127.0.0.1:8000/docs

Au démarrage (lifespan) : consumer MQTT en tâche de fond (stockage + détection + diffusion WebSocket),
calcul du plan automatique en arrière-plan, APScheduler (prévision + plan toutes les heures).
Variables : API_EMBED_CONSUMER=false (sans broker), API_SCHEDULER=false, CORS_ORIGINS.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import parcel_profile, parcels, routes_actuators, routes_core, routes_optimize, routes_stream, ws
from api.state import AppState

logger = logging.getLogger("api")


def create_app(start_background: bool = True) -> FastAPI:
    """start_background=False : ni consumer, ni planificateur (tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st: AppState = app.state.aqua
        st.hub.attach_loop(asyncio.get_running_loop())
        if start_background and st.settings.api_embed_consumer:
            try:
                await asyncio.to_thread(st.start_consumer)
                logger.info("Consumer embarqué démarré (stockage %s)", st.consumer.storage.name)
            except Exception as exc:
                logger.error("Consumer non démarré : %s", exc)
        if start_background:
            threading.Thread(target=st.refresh_auto_plan, name="plan-initial", daemon=True).start()
            st.start_loops()                    # confirmations, appareil hors-ligne, plan → ESP32, status/api
        if start_background and st.settings.api_scheduler:
            from apscheduler.schedulers.background import BackgroundScheduler

            st.scheduler = BackgroundScheduler(timezone=st.settings.farm_tz)
            st.scheduler.add_job(st.refresh_auto_plan, "interval", hours=1, id="plan_horaire",
                                 max_instances=1, coalesce=True)
            st.scheduler.start()
        yield
        st.stop_loops()
        if st.scheduler:
            st.scheduler.shutdown(wait=False)
        st.stop_consumer()

    app = FastAPI(
        title="AquaMind API", version="0.6.0", lifespan=lifespan,
        description="Aide à la décision eau–énergie : prévision ET0, optimisation, détection de fuites en "
                    "direct, actionneurs avec garde-fous. Résultats simulés : **estimation simulée**.",
    )
    app.state.aqua = AppState()
    origins = [o.strip() for o in app.state.aqua.settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    # parcel_profile avant parcels : /api/parcels/crops ne doit pas être pris pour /api/parcels/{parcel_id}
    for module in (routes_core, routes_optimize, routes_stream, routes_actuators, ws, parcel_profile, parcels):
        app.include_router(module.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
