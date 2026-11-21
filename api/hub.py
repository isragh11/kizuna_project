"""Diffusion temps réel vers les WebSockets, alimentée depuis des threads (MQTT, minuteries).

Chaque client a une file bornée : si un client lent prend du retard, les messages les plus anciens
sont abandonnés (throttle naturel) au lieu de bloquer le flux des autres.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)
QUEUE_SIZE = 500


class LiveHub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.sent = 0
        self.dropped = 0

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        """Appelable depuis n'importe quel thread."""
        loop = self.loop
        if loop is None or loop.is_closed() or not self.clients:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:          # boucle en cours d'arrêt
            pass

    def _fanout(self, event: dict[str, Any]) -> None:
        for q in list(self.clients):
            if q.full():
                q.get_nowait()
                self.dropped += 1
            q.put_nowait(event)
            self.sent += 1

    def register(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.clients.add(q)
        return q

    def unregister(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self.clients.discard(q)
