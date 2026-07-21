from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from conftest import (
    DEFAULT_PASSWORD,
    NEW_PASSWORD,
    TEST_ORIGIN,
    csrf_for,
    login_user,
    register_user,
)
from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app
from app.security import verify_password
from app.web import SESSION_COOKIE


def test_registration_uses_argon2_and_stores_only_session_hash(client, settings):
    response = register_user(client, "alice", display_name="Alice")

    assert response.status_code == 303
    assert response.headers["location"] == "/?notice=registered"
    raw_cookie = client.cookies.get(SESSION_COOKIE)
    assert raw_cookie
    assert DEFAULT_PASSWORD not in response.text

    with connect(settings) as conn:
        account = conn.execute("SELECT * FROM users WHERE username = 'alice'").fetchone()
        session = conn.execute(
            "SELECT token_hash, user_id FROM sessions WHERE user_id = ?", (account["id"],)
        ).fetchone()

    assert account is not None
    assert account["password_hash"].startswith("$argon2id$")
    assert account["password_hash"] != DEFAULT_PASSWORD
    assert verify_password(account["password_hash"], DEFAULT_PASSWORD)
    assert session is not None
    assert len(session["token_hash"]) == 64
    assert session["token_hash"] != raw_cookie
    assert raw_cookie not in session["token_hash"]


def test_duplicate_registration_and_login_failure_are_safely_reported(client_factory, settings):
    owner = client_factory()
    assert register_user(owner, "existing").status_code == 303

    duplicate = client_factory()
    duplicate_response = register_user(duplicate, "existing", display_name="Other Name")
    assert duplicate_response.status_code == 400
    assert "이미 사용 중인 아이디" in duplicate_response.text

    wrong_password_client = client_factory()
    wrong_response = login_user(wrong_password_client, "existing", "Wrong-Password-2026!")
    missing_user_client = client_factory()
    missing_response = login_user(missing_user_client, "ghostuser", "Wrong-Password-2026!")

    generic = "아이디 또는 비밀번호를 확인해 주세요."
    assert wrong_response.status_code == 400
    assert missing_response.status_code == 400
    assert generic in wrong_response.text
    assert generic in missing_response.text
    assert "존재하지" not in missing_response.text

    with connect(settings) as conn:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE username = 'existing'").fetchone()[0]
    assert count == 1


def test_csrf_and_origin_are_required_for_registration(client, settings):
    page = client.get("/register")
    assert page.status_code == 200
    token = csrf_for(client, "/register")
    form = {
        "username": "csrfuser",
        "display_name": "CSRF User",
        "password": DEFAULT_PASSWORD,
        "password_confirm": DEFAULT_PASSWORD,
    }

    missing = client.post("/register", data=form, follow_redirects=False)
    assert missing.status_code == 400

    wrong = client.post(
        "/register",
        data={**form, "csrf_token": "wrong-token"},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert wrong.status_code == 403

    cross_site = client.post(
        "/register",
        data={**form, "csrf_token": token},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert cross_site.status_code == 403

    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    accepted = client.post(
        "/register",
        data={**form, "csrf_token": token},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert accepted.status_code == 303


def test_session_cookie_security_attributes(client):
    response = client.get("/register")
    cookie = response.headers["set-cookie"]

    assert f"{SESSION_COOKIE}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


def test_secure_cookie_attribute_when_enabled(settings, tmp_path: Path):
    secure_settings = replace(
        settings,
        database_path=tmp_path / "secure-cookie.sqlite3",
        upload_dir=tmp_path / "secure-uploads",
        secure_cookies=True,
    )
    with TestClient(create_app(secure_settings), base_url=TEST_ORIGIN) as secure_client:
        response = secure_client.get("/register")

    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Strict-Transport-Security" in response.headers


def test_profile_update_escapes_output_and_password_change_revokes_all_sessions(
    client_factory, settings
):
    primary = client_factory()
    assert register_user(primary, "profileuser", display_name="Profile User").status_code == 303

    secondary = client_factory()
    assert login_user(secondary, "profileuser").status_code == 303

    profile_token = csrf_for(primary, "/settings")
    profile_response = primary.post(
        "/settings/profile",
        data={
            "display_name": "Updated User",
            "bio": "<script>alert('profile')</script>",
            "csrf_token": profile_token,
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert profile_response.status_code == 303

    public_profile = primary.get("/users/profileuser")
    assert public_profile.status_code == 200
    assert "<script>alert('profile')</script>" not in public_profile.text
    assert "&lt;script&gt;alert" in public_profile.text

    with connect(settings) as conn:
        account = conn.execute(
            "SELECT id, display_name, bio FROM users WHERE username = 'profileuser'"
        ).fetchone()
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (account["id"],)
        ).fetchone()[0]
    assert account["display_name"] == "Updated User"
    assert account["bio"] == "<script>alert('profile')</script>"
    assert session_count == 2

    password_token = csrf_for(primary, "/settings")
    changed = primary.post(
        "/settings/password",
        data={
            "current_password": DEFAULT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
            "csrf_token": password_token,
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/login?notice=credential_updated"
    assert "Max-Age=0" in changed.headers["set-cookie"]

    with connect(settings) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (account["id"],)
            ).fetchone()[0]
            == 0
        )
        password_hash = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (account["id"],)
        ).fetchone()[0]
    assert verify_password(password_hash, NEW_PASSWORD)
    assert not verify_password(password_hash, DEFAULT_PASSWORD)

    assert secondary.get("/settings").status_code == 401

    old_password_client = client_factory()
    assert login_user(old_password_client, "profileuser", DEFAULT_PASSWORD).status_code == 400
    new_password_client = client_factory()
    assert login_user(new_password_client, "profileuser", NEW_PASSWORD).status_code == 303
