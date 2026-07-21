"""단일 worker용 WebSocket 연결 관리자."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

MAX_CONNECTIONS_GLOBAL = 80
MAX_CONNECTIONS_PER_USER = 3
SEND_TIMEOUT_SECONDS = 2


@dataclass(eq=False)
class ChatConnection:
    websocket: WebSocket
    user_id: int
    channel: int | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def send_json(self, payload: dict[str, Any]) -> None:
        async def send_locked() -> None:
            async with self.send_lock:
                await self.websocket.send_json(payload)

        await asyncio.wait_for(send_locked(), timeout=SEND_TIMEOUT_SECONDS)


class ChatManager:
    """연결 상태는 메모리, 메시지는 DB에 보존한다."""

    def __init__(self) -> None:
        self._connections: set[ChatConnection] = set()
        self._lock = asyncio.Lock()

    async def add(self, connection: ChatConnection) -> bool:
        async with self._lock:
            if len(self._connections) >= MAX_CONNECTIONS_GLOBAL:
                return False
            user_connections = sum(
                1 for item in self._connections if item.user_id == connection.user_id
            )
            if user_connections >= MAX_CONNECTIONS_PER_USER:
                return False
            self._connections.add(connection)
            return True

    async def remove(self, connection: ChatConnection) -> None:
        async with self._lock:
            self._connections.discard(connection)

    async def disconnect_user(self, user_id: int) -> None:
        async with self._lock:
            targets = [item for item in self._connections if item.user_id == user_id]
            for item in targets:
                self._connections.discard(item)
        for item in targets:
            try:
                await asyncio.wait_for(
                    item.websocket.close(code=4401, reason="세션이 종료되었습니다."),
                    timeout=SEND_TIMEOUT_SECONDS,
                )
            except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
                pass

    async def publish(self, message: dict[str, Any]) -> None:
        sender_id = int(message["sender_id"])
        recipient_id = message.get("recipient_id")
        async with self._lock:
            if recipient_id is None:
                targets = [item for item in self._connections if item.channel is None]
            else:
                recipient_id = int(recipient_id)
                targets = [
                    item
                    for item in self._connections
                    if (item.user_id == sender_id and item.channel == recipient_id)
                    or (item.user_id == recipient_id and item.channel == sender_id)
                ]

        async def deliver(item: ChatConnection) -> ChatConnection | None:
            try:
                await item.send_json({"type": "message", "message": message})
                return None
            except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
                return item

        results = await asyncio.gather(*(deliver(item) for item in targets))
        stale = [item for item in results if item is not None]
        if stale:
            async with self._lock:
                for item in stale:
                    self._connections.discard(item)
