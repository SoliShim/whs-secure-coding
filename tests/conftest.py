from __future__ import annotations

import html
import io
import re
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.db import connect
from app.main import create_app
from app.repository import create_session, create_user
from app.security import hash_password
from app.web import SESSION_COOKIE

TEST_ORIGIN = "http://testserver"
DEFAULT_PASSWORD = "Correct-Horse-Battery-2026!"  # noqa: S105 - synthetic test credential
NEW_PASSWORD = "New-Correct-Horse-Battery-2026!"  # noqa: S105 - synthetic test credential


@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    password: str
    raw_token: str
    csrf_token: str


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_path=tmp_path / "baromarket-test.sqlite3",
        upload_dir=tmp_path / "uploads",
        allowed_hosts=("testserver",),
        allowed_origins=(TEST_ORIGIN,),
        secure_cookies=False,
        session_ttl_seconds=3_600,
        session_idle_seconds=600,
        report_threshold=3,
        app_secret="test-only-secret-with-at-least-thirty-two-bytes-2026",  # noqa: S106
        debug=False,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client_factory(app) -> Generator[Callable[[], TestClient], None, None]:
    opened: list[TestClient] = []

    def factory() -> TestClient:
        client = TestClient(app, base_url=TEST_ORIGIN)
        client.__enter__()
        opened.append(client)
        return client

    yield factory

    for item in reversed(opened):
        item.__exit__(None, None, None)


@pytest.fixture
def client(client_factory: Callable[[], TestClient]) -> TestClient:
    return client_factory()


def hidden_value(markup: str, name: str) -> str:
    escaped_name = re.escape(name)
    patterns = (
        rf'<input[^>]*name=["\']{escaped_name}["\'][^>]*value=["\']([^"\']*)["\']',
        rf'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']{escaped_name}["\']',
        rf'<meta[^>]*name=["\']{escaped_name}["\'][^>]*content=["\']([^"\']*)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, markup, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
    raise AssertionError(f"HTML에서 {name!r} 값을 찾지 못했습니다.")


def csrf_for(client: TestClient, path: str = "/") -> str:
    response = client.get(path)
    assert response.status_code == 200, response.text
    return hidden_value(response.text, "csrf_token")


def register_user(
    client: TestClient,
    username: str,
    *,
    password: str = DEFAULT_PASSWORD,
    display_name: str | None = None,
):
    token = csrf_for(client, "/register")
    return client.post(
        "/register",
        data={
            "username": username,
            "display_name": display_name or username.title(),
            "password": password,
            "password_confirm": password,
            "csrf_token": token,
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )


def login_user(client: TestClient, username: str, password: str = DEFAULT_PASSWORD):
    token = csrf_for(client, "/login")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )


def logout_user(client: TestClient):
    token = csrf_for(client, "/")
    return client.post(
        "/logout",
        data={"csrf_token": token},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )


def user_id(settings: Settings, username: str) -> int:
    with connect(settings) as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    assert row is not None
    return int(row["id"])


def make_admin(settings: Settings, username: str) -> int:
    account_id = user_id(settings, username)
    with connect(settings) as conn:
        conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (account_id,))
        conn.commit()
    return account_id


def make_reporter_eligible(settings: Settings, username: str) -> None:
    """Age a synthetic account beyond the auto-moderation trust delay."""

    created_at = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE users SET created_at = ?, updated_at = ? WHERE username = ?",
            (created_at, created_at, username),
        )
        conn.commit()


def png_bytes(color: tuple[int, int, int] = (35, 139, 88)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def create_product_http(
    client: TestClient,
    *,
    name: str = "Secure Keyboard",
    description: str = "A carefully tested second-hand keyboard.",
    price: str = "25000",
) -> int:
    token = csrf_for(client, "/products/new")
    response = client.post(
        "/products",
        data={
            "name": name,
            "description": description,
            "price": price,
            "csrf_token": token,
        },
        files={"image": ("item.png", png_bytes(), "image/png")},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    match = re.fullmatch(r"/products/(\d+)\?notice=product_created", location)
    assert match, location
    return int(match.group(1))


def seed_session_user(
    settings: Settings,
    username: str,
    *,
    password: str = DEFAULT_PASSWORD,
    role: str = "user",
) -> SessionUser:
    with connect(settings) as conn:
        account = create_user(
            conn,
            username=username,
            display_name=username.title(),
            password_hash=hash_password(password),
            role=role,
        )
        raw_token, session = create_session(conn, settings, int(account["id"]))
    return SessionUser(
        id=int(account["id"]),
        username=username,
        password=password,
        raw_token=raw_token,
        csrf_token=str(session["csrf_token"]),
    )


def websocket_headers(session: SessionUser, origin: str = TEST_ORIGIN) -> dict[str, str]:
    return {
        "Origin": origin,
        "Cookie": f"{SESSION_COOKIE}={session.raw_token}",
    }
