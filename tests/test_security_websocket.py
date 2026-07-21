from __future__ import annotations

import asyncio

import pytest
from conftest import TEST_ORIGIN, seed_session_user, websocket_headers
from starlette.websockets import WebSocketDisconnect

import app.chat as chat_module
import app.routers.products as product_routes
from app.chat import ChatConnection, ChatManager
from app.db import connect


def test_security_headers_are_present_on_normal_response(client):
    response = client.get("/")

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


def test_unhandled_error_response_does_not_disclose_exception_or_traceback(client, monkeypatch):
    internal_detail = "INTERNAL_SQL=SELECT password_hash FROM users /srv/private/app.py"

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError(internal_detail)

    monkeypatch.setattr(product_routes, "list_products", fail_closed)
    response = client.get("/")

    assert response.status_code == 500
    assert internal_detail not in response.text
    assert "Traceback" not in response.text
    assert "password_hash" not in response.text
    assert "/srv/private/app.py" not in response.text
    assert "요청을 처리하지 못했습니다" in response.text
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_websocket_rejects_unauthenticated_connection(client):
    with pytest.raises(WebSocketDisconnect) as captured:
        with client.websocket_connect("/ws/chat", headers={"Origin": TEST_ORIGIN}):
            pass
    assert captured.value.code == 4401


def test_websocket_rejects_untrusted_origin_even_with_valid_session(client, settings):
    account = seed_session_user(settings, "originuser")

    with pytest.raises(WebSocketDisconnect) as captured:
        with client.websocket_connect(
            "/ws/chat",
            headers=websocket_headers(account, origin="https://evil.example"),
        ):
            pass
    assert captured.value.code == 4403


def test_websocket_requires_matching_csrf_auth_message(client, settings):
    account = seed_session_user(settings, "csrfwsuser")

    with client.websocket_connect("/ws/chat", headers=websocket_headers(account)) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "wrong-token"})
        with pytest.raises(WebSocketDisconnect) as captured:
            websocket.receive_json()
    assert captured.value.code == 4401


def test_websocket_limits_concurrent_connections_per_user(client, settings):
    account = seed_session_user(settings, "wslimituser")
    headers = websocket_headers(account)

    with client.websocket_connect("/ws/chat", headers=headers) as first:
        with client.websocket_connect("/ws/chat", headers=headers) as second:
            with client.websocket_connect("/ws/chat", headers=headers) as third:
                _authenticate(first, account.csrf_token, account.id)
                _authenticate(second, account.csrf_token, account.id)
                _authenticate(third, account.csrf_token, account.id)

                with pytest.raises(WebSocketDisconnect) as captured:
                    with client.websocket_connect("/ws/chat", headers=headers) as fourth:
                        fourth.send_json({"type": "auth", "csrf_token": account.csrf_token})
                        fourth.receive_json()
                assert captured.value.code == 4429


def test_slow_websocket_does_not_block_or_break_fanout(monkeypatch):
    monkeypatch.setattr(chat_module, "SEND_TIMEOUT_SECONDS", 0.01)

    class FakeSocket:
        def __init__(self, delay: float = 0):
            self.delay = delay
            self.messages: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.messages.append(payload)

    async def scenario():
        manager = ChatManager()
        slow_socket = FakeSocket(delay=1)
        fast_socket = FakeSocket()
        slow = ChatConnection(websocket=slow_socket, user_id=1)
        fast = ChatConnection(websocket=fast_socket, user_id=2)
        assert await manager.add(slow)
        assert await manager.add(fast)
        await manager.publish(
            {
                "sender_id": 2,
                "recipient_id": None,
                "body": "fanout remains available",
            }
        )
        assert len(fast_socket.messages) == 1
        assert fast_socket.messages[0]["type"] == "message"
        assert slow not in manager._connections
        assert fast in manager._connections

    asyncio.run(scenario())


def _authenticate(websocket, csrf_token: str, expected_user_id: int) -> None:
    websocket.send_json({"type": "auth", "csrf_token": csrf_token})
    ready = websocket.receive_json()
    assert ready == {"type": "ready", "user_id": expected_user_id}


def _subscribe(websocket, recipient_id: int | None) -> list[dict]:
    websocket.send_json({"type": "subscribe", "recipient_id": recipient_id})
    history = websocket.receive_json()
    assert history["type"] == "history"
    assert history["recipient_id"] == recipient_id
    return history["messages"]


def test_websocket_global_dm_nonce_deduplication_and_length_validation(client, settings):
    alice = seed_session_user(settings, "wsalice")
    bob = seed_session_user(settings, "wsbob")

    with client.websocket_connect("/ws/chat", headers=websocket_headers(alice)) as alice_ws:
        with client.websocket_connect("/ws/chat", headers=websocket_headers(bob)) as bob_ws:
            _authenticate(alice_ws, alice.csrf_token, alice.id)
            _authenticate(bob_ws, bob.csrf_token, bob.id)

            assert _subscribe(alice_ws, None) == []
            assert _subscribe(bob_ws, None) == []

            global_nonce = "global_nonce_000001"
            alice_ws.send_json(
                {
                    "type": "message",
                    "recipient_id": None,
                    "nonce": global_nonce,
                    "body": "Hello to the global room",
                }
            )
            alice_global = alice_ws.receive_json()
            bob_global = bob_ws.receive_json()
            assert alice_global["type"] == "message"
            assert bob_global["type"] == "message"
            assert alice_global["message"]["sender_id"] == alice.id
            assert bob_global["message"]["recipient_id"] is None
            assert bob_global["message"]["body"] == "Hello to the global room"

            assert _subscribe(alice_ws, bob.id) == []
            assert _subscribe(bob_ws, alice.id) == []

            dm_nonce = "direct_nonce_000001"
            dm_payload = {
                "type": "message",
                "recipient_id": bob.id,
                "nonce": dm_nonce,
                "body": "Private hello to Bob",
            }
            alice_ws.send_json(dm_payload)
            alice_dm = alice_ws.receive_json()
            bob_dm = bob_ws.receive_json()
            assert alice_dm["type"] == "message"
            assert bob_dm["type"] == "message"
            assert bob_dm["message"]["sender_id"] == alice.id
            assert bob_dm["message"]["recipient_id"] == bob.id
            assert bob_dm["message"]["body"] == "Private hello to Bob"

            alice_ws.send_json(dm_payload)
            duplicate = alice_ws.receive_json()
            assert duplicate["type"] == "error"
            assert "이미 처리한 메시지" in duplicate["message"]

            alice_ws.send_json(
                {
                    "type": "message",
                    "recipient_id": bob.id,
                    "nonce": "too_long_nonce_0001",
                    "body": "x" * 501,
                }
            )
            too_long = alice_ws.receive_json()
            assert too_long["type"] == "error"
            assert "1~500자" in too_long["message"]

    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT sender_id, recipient_id, body, client_nonce FROM messages ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["client_nonce"] == "global_nonce_000001"
    assert rows[0]["recipient_id"] is None
    assert rows[1]["client_nonce"] == "direct_nonce_000001"
    assert rows[1]["recipient_id"] == bob.id
