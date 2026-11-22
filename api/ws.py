"""WebSocket /ws/live : mesures, alertes, plan, actionneurs en direct.

Messages envoyés (JSON) : {"type": "hello" | "reading" | "alert" | "alert_end" | "alert_update" |
"new_normal" | "no_flow" | "plan" | "plot_info" | "actuator_state" | "actuator_command", ...}.
Filtre facultatif : /ws/live?types=alert,plan pour ne recevoir que certains types.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/live")
async def live(ws: WebSocket) -> None:
    await ws.accept()
    st = ws.app.state.aqua
    types = {t for t in (ws.query_params.get("types") or "").split(",") if t}
    q = st.hub.register()
    await ws.send_json({
        "type": "hello", "plan": st.plan_meta,
        "plan_kpis": st.last_plan.kpis if st.last_plan else None,
        "actuators": st.actuators.snapshot(), "alerts": st.alerts_with_status(10), "plot_info": st.plot_info,
        "mqtt": bool(st.consumer and st.consumer.connected.is_set()),
    })

    async def pump_out() -> None:
        while True:
            event = await q.get()
            if not types or event.get("type") in types:
                await ws.send_json(event)

    async def read_in() -> None:          # détecte la déconnexion ; « ping » → « pong »
        while True:
            msg = await ws.receive_text()
            if msg.strip().lower() == "ping":
                await ws.send_json({"type": "pong"})

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(read_in())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        st.hub.unregister(q)
