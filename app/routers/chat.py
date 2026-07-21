"""인증·Origin·메시지별 권한 검증을 적용한 실시간 채팅."""

from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.chat import ChatConnection
from app.db import connect
from app.repository import (
    ConflictError,
    ForbiddenOperationError,
    audit,
    create_message,
    get_user_by_id,
    list_chat_users,
    load_session,
    message_history,
)
from app.security import csrf_matches, ip_fingerprint
from app.web import SESSION_COOKIE, render, require_user

router = APIRouter()
NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,100}$")


@router.get("/chat")
def chat_page(request: Request, with_user: int | None = None, recipient: int | None = None):
    user = require_user(request)
    with connect(request.app.state.settings) as conn:
        contacts = list_chat_users(conn, int(user["id"]))
    selected = None
    requested_user = with_user if with_user is not None else recipient
    if requested_user is not None:
        selected = next((item for item in contacts if int(item["id"]) == requested_user), None)
    return render(
        request,
        "chat.html",
        contacts=contacts,
        chat_users=contacts,
        selected_user=selected,
        selected_user_id=int(selected["id"]) if selected else None,
    )


async def _send_error(connection: ChatConnection, message: str) -> None:
    await connection.send_json({"type": "error", "message": message})


def _validate_body(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("메시지 형식이 올바르지 않습니다.")
    body = value.strip()
    if not 1 <= len(body) <= 500:
        raise ValueError("메시지는 1~500자로 입력해 주세요.")
    if any(ord(character) < 32 and character not in "\n\t" for character in body):
        raise ValueError("메시지에 허용되지 않은 제어 문자가 있습니다.")
    return body


def _load_chat_session(settings, raw_token: str | None):
    with connect(settings) as conn:
        return load_session(conn, settings, raw_token)


def _audit_chat_connection(settings, user_id: int, ip_hash: str) -> None:
    with connect(settings) as conn:
        audit(
            conn,
            "chat.connected",
            actor_user_id=user_id,
            target_type="user",
            target_id=user_id,
            ip_hash=ip_hash,
        )
        conn.commit()


def _chat_user(settings, user_id: int):
    with connect(settings) as conn:
        return get_user_by_id(conn, user_id)


def _chat_history(settings, user_id: int, recipient_id: int | None):
    with connect(settings) as conn:
        return message_history(conn, user_id, recipient_id)


def _store_chat_message(
    settings,
    *,
    sender_id: int,
    recipient_id: int | None,
    body: str,
    client_nonce: str,
):
    with connect(settings) as conn:
        return create_message(
            conn,
            sender_id=sender_id,
            recipient_id=recipient_id,
            body=body,
            client_nonce=client_nonce,
        )


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket):
    settings = websocket.app.state.settings
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if origin not in settings.allowed_origins:
        await websocket.close(code=4403, reason="허용되지 않은 출처입니다.")
        return
    host = websocket.client.host if websocket.client else "unknown"
    ip_hash = ip_fingerprint(host, settings.app_secret)
    if not websocket.app.state.rate_limiter.allow(f"ws-preauth:{ip_hash}", 30, 60):
        await websocket.close(code=4429, reason="연결 요청이 너무 많습니다.")
        return
    raw_token = websocket.cookies.get(SESSION_COOKIE)
    session = await run_in_threadpool(_load_chat_session, settings, raw_token)
    if session is None or session.get("user_id") is None:
        await websocket.close(code=4401, reason="로그인이 필요합니다.")
        return
    user_id = int(session["user_id"])
    if not websocket.app.state.rate_limiter.allow(f"ws-connect:{user_id}:{ip_hash}", 20, 60):
        await websocket.close(code=4429, reason="연결 요청이 너무 많습니다.")
        return

    await websocket.accept()
    connection = ChatConnection(websocket=websocket, user_id=user_id)
    try:
        try:
            raw_first = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            if len(raw_first.encode("utf-8")) > 4096:
                raise ValueError("인증 메시지가 너무 큽니다.")
            first = json.loads(raw_first)
        except (TimeoutError, json.JSONDecodeError, ValueError):
            await websocket.close(code=4401, reason="채팅 인증에 실패했습니다.")
            return
        if (
            not isinstance(first, dict)
            or first.get("type") != "auth"
            or not csrf_matches(session.get("csrf_token"), first.get("csrf_token"))
        ):
            await websocket.close(code=4401, reason="채팅 인증에 실패했습니다.")
            return

        if not await websocket.app.state.chat_manager.add(connection):
            await websocket.close(code=4429, reason="동시 채팅 연결이 너무 많습니다.")
            return
        await run_in_threadpool(_audit_chat_connection, settings, user_id, ip_hash)
        await connection.send_json({"type": "ready", "user_id": user_id})

        while True:
            try:
                raw_payload = await asyncio.wait_for(websocket.receive_text(), timeout=60)
            except TimeoutError:
                current = await run_in_threadpool(_load_chat_session, settings, raw_token)
                if current is None or int(current.get("user_id") or 0) != user_id:
                    await websocket.close(code=4401, reason="세션이 만료되었습니다.")
                    break
                await connection.send_json({"type": "ping"})
                continue
            if len(raw_payload.encode("utf-8")) > 4096:
                await _send_error(connection, "메시지가 너무 큽니다.")
                continue
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                await _send_error(connection, "JSON 형식이 올바르지 않습니다.")
                continue
            if not isinstance(payload, dict):
                await _send_error(connection, "요청 형식이 올바르지 않습니다.")
                continue

            current = await run_in_threadpool(_load_chat_session, settings, raw_token)
            if current is None or int(current.get("user_id") or 0) != user_id:
                await websocket.close(code=4401, reason="세션이 만료되었습니다.")
                break

            message_type = payload.get("type")
            if message_type == "pong":
                continue
            if message_type == "subscribe":
                recipient_value = payload.get("recipient_id")
                if recipient_value is None:
                    recipient_id = None
                elif isinstance(recipient_value, int) and recipient_value > 0:
                    recipient_id = recipient_value
                else:
                    await _send_error(connection, "대화 상대 형식이 올바르지 않습니다.")
                    continue
                if recipient_id is not None:
                    recipient = await run_in_threadpool(_chat_user, settings, recipient_id)
                    if (
                        recipient is None
                        or recipient["status"] != "active"
                        or recipient_id == user_id
                    ):
                        await _send_error(connection, "대화 상대를 확인할 수 없습니다.")
                        continue
                connection.channel = recipient_id
                history = await run_in_threadpool(_chat_history, settings, user_id, recipient_id)
                await connection.send_json(
                    {"type": "history", "recipient_id": recipient_id, "messages": history}
                )
                continue
            if message_type != "message":
                await _send_error(connection, "지원하지 않는 채팅 명령입니다.")
                continue

            recipient_value = payload.get("recipient_id")
            if recipient_value != connection.channel:
                await _send_error(connection, "구독한 대화방과 받는 사람이 일치하지 않습니다.")
                continue
            nonce = payload.get("nonce")
            if not isinstance(nonce, str) or not NONCE_PATTERN.fullmatch(nonce):
                await _send_error(connection, "메시지 식별자가 올바르지 않습니다.")
                continue
            try:
                body = _validate_body(payload.get("body"))
            except ValueError as exc:
                await _send_error(connection, str(exc))
                continue
            if not websocket.app.state.rate_limiter.allow(f"ws-message:{user_id}", 20, 10):
                await _send_error(connection, "메시지를 너무 빠르게 보내고 있습니다.")
                continue
            try:
                message = await run_in_threadpool(
                    _store_chat_message,
                    settings,
                    sender_id=user_id,
                    recipient_id=connection.channel,
                    body=body,
                    client_nonce=nonce,
                )
                await websocket.app.state.chat_manager.publish(message)
            except ConflictError:
                await _send_error(connection, "이미 처리한 메시지입니다.")
            except ForbiddenOperationError as exc:
                await _send_error(connection, str(exc))
    except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
        pass
    finally:
        await websocket.app.state.chat_manager.remove(connection)
