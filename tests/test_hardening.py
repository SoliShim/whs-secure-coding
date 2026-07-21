from __future__ import annotations

import io

from conftest import (
    DEFAULT_PASSWORD,
    TEST_ORIGIN,
    create_product_http,
    csrf_for,
    make_admin,
    make_reporter_eligible,
    register_user,
)
from PIL import Image

from app.db import connect
from app.main import MAX_HTTP_BODY_BYTES
from app.web import SESSION_COOKIE


def _report(client, target_type: str, target_id: int):
    return client.post(
        "/reports",
        data={
            "target_type": target_type,
            "target_id": str(target_id),
            "reason": "Independent evidence indicates this target needs moderator review.",
            "csrf_token": csrf_for(client, "/"),
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )


def test_public_browsing_does_not_create_database_sessions(client, settings):
    assert client.get("/").status_code == 200
    assert client.get("/missing-page").status_code == 404
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

    registration_page = client.get("/register")
    assert registration_page.status_code == 200
    anonymous_cookie = client.cookies.get(SESSION_COOKIE)
    assert anonymous_cookie and anonymous_cookie.startswith("a.")
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_tampered_anonymous_csrf_cookie_is_rejected(client):
    page = client.get("/register")
    assert page.status_code == 200
    token = csrf_for(client, "/register")
    original = client.cookies.get(SESSION_COOKIE)
    assert original
    client.cookies.set(SESSION_COOKIE, f"{original[:-1]}x")

    response = client.post(
        "/register",
        data={
            "username": "tampered",
            "display_name": "Tampered User",
            "password": "Correct-Horse-Battery-2026!",
            "password_confirm": "Correct-Horse-Battery-2026!",
            "csrf_token": token,
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_declared_oversized_request_is_rejected_before_route_parsing(client, settings):
    response = client.post(
        "/products",
        content=b"x",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(MAX_HTTP_BODY_BYTES + 1),
        },
    )
    assert response.status_code == 413
    assert response.json() == {"error": "request_too_large"}
    assert response.headers["x-content-type-options"] == "nosniff"
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_chunked_oversized_request_is_rejected_by_received_byte_limit(client):
    def body_chunks():
        yield b"username="
        for _ in range(7):
            yield b"x" * (1024 * 1024)

    response = client.post(
        "/register",
        content=body_chunks(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Transfer-Encoding": "chunked",
        },
        follow_redirects=False,
    )
    assert response.status_code == 413
    assert response.json() == {"error": "request_too_large"}


def test_compressed_oversized_image_dimensions_are_rejected(client, settings):
    assert register_user(client, "imagebombuser").status_code == 303
    payload = io.BytesIO()
    Image.new("1", (5_000, 5_000), color=1).save(payload, format="PNG")
    response = client.post(
        "/products",
        data={
            "name": "Oversized image",
            "description": "This listing intentionally uses an excessive pixel count.",
            "price": "1000",
            "csrf_token": csrf_for(client, "/products/new"),
        },
        files={"image": ("large.png", payload.getvalue(), "image/png")},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "해상도가 너무 큽니다" in response.text
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
    assert list(settings.upload_dir.glob("*.jpg")) == []


def test_new_accounts_can_report_but_cannot_immediately_trigger_quarantine(
    client_factory, settings
):
    owner = client_factory()
    assert register_user(owner, "freshowner").status_code == 303
    product_id = create_product_http(owner, name="Fresh report target")
    reporters = [client_factory() for _ in range(3)]
    for index, reporter in enumerate(reporters, 1):
        assert register_user(reporter, f"freshreport{index}").status_code == 303
        response = _report(reporter, "product", product_id)
        assert response.status_code == 303
        assert response.headers["location"].endswith("notice=reported")
    with connect(settings) as conn:
        product = conn.execute(
            "SELECT report_count, is_hidden FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        eligible = conn.execute(
            """
            SELECT SUM(auto_eligible) FROM reports
            WHERE target_type = 'product' AND target_id = ?
            """,
            (product_id,),
        ).fetchone()[0]
    assert dict(product) == {"report_count": 0, "is_hidden": 0}
    assert eligible == 0


def test_password_change_attempts_are_rate_limited(client):
    assert register_user(client, "ratelimitpw").status_code == 303
    token = csrf_for(client, "/settings")
    form = {
        "current_password": "Wrong-Password-For-Rate-Limit!",
        "new_password": "Another-Correct-Horse-2026!",
        "new_password_confirm": "Another-Correct-Horse-2026!",
        "csrf_token": token,
    }
    for _ in range(5):
        response = client.post(
            "/settings/password",
            data=form,
            headers={"Origin": TEST_ORIGIN},
            follow_redirects=False,
        )
        assert response.status_code == 400
    blocked = client.post(
        "/settings/password",
        data=form,
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    assert "요청이 너무 많습니다" in blocked.text

    # A rejected attempt must not change the existing credential.
    assert client.get("/settings").status_code == 200
    assert DEFAULT_PASSWORD not in blocked.text


def test_dismissal_releases_only_automatic_product_quarantine(client_factory, settings):
    owner = client_factory()
    assert register_user(owner, "releaseowner").status_code == 303
    product_id = create_product_http(owner, name="Recoverable Listing")

    reporters = [client_factory() for _ in range(3)]
    for index, reporter in enumerate(reporters, 1):
        username = f"release{index}"
        assert register_user(reporter, username).status_code == 303
        make_reporter_eligible(settings, username)
        assert _report(reporter, "product", product_id).status_code == 303

    admin = client_factory()
    assert register_user(admin, "releaseadmin").status_code == 303
    make_admin(settings, "releaseadmin")
    with connect(settings) as conn:
        report_id = conn.execute(
            """
            SELECT id FROM reports
            WHERE target_type = 'product' AND target_id = ? ORDER BY id LIMIT 1
            """,
            (product_id,),
        ).fetchone()[0]
        product = conn.execute(
            "SELECT is_hidden, visibility_source FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    assert dict(product) == {"is_hidden": 1, "visibility_source": "auto_report"}

    dismissed = admin.post(
        f"/admin/reports/{report_id}/resolve",
        data={"action": "dismiss", "csrf_token": csrf_for(admin, "/admin")},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert dismissed.status_code == 303
    with connect(settings) as conn:
        product = conn.execute(
            "SELECT report_count, is_hidden, visibility_source FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
    assert dict(product) == {
        "report_count": 2,
        "is_hidden": 0,
        "visibility_source": "none",
    }

    repeated = admin.post(
        f"/admin/reports/{report_id}/resolve",
        data={"action": "confirm", "csrf_token": csrf_for(admin, "/admin")},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert repeated.status_code == 409
    assert "이미 처리된 신고" in repeated.text


def test_dismissal_does_not_reverse_manual_product_moderation(client_factory, settings):
    owner = client_factory()
    assert register_user(owner, "manualowner").status_code == 303
    product_id = create_product_http(owner, name="Manually Hidden Listing")
    reporters = [client_factory() for _ in range(3)]
    for index, reporter in enumerate(reporters, 1):
        username = f"manualreport{index}"
        assert register_user(reporter, username).status_code == 303
        make_reporter_eligible(settings, username)
        assert _report(reporter, "product", product_id).status_code == 303

    admin = client_factory()
    assert register_user(admin, "manualadmin").status_code == 303
    make_admin(settings, "manualadmin")
    admin_token = csrf_for(admin, "/admin")
    hidden = admin.post(
        f"/admin/products/{product_id}/visibility",
        data={"visible": "false", "csrf_token": admin_token},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert hidden.status_code == 303

    with connect(settings) as conn:
        report_id = conn.execute(
            "SELECT id FROM reports WHERE target_type = 'product' AND target_id = ? LIMIT 1",
            (product_id,),
        ).fetchone()[0]
    dismissed = admin.post(
        f"/admin/reports/{report_id}/resolve",
        data={"action": "dismiss", "csrf_token": csrf_for(admin, "/admin")},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert dismissed.status_code == 303
    with connect(settings) as conn:
        product = conn.execute(
            "SELECT is_hidden, visibility_source FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    assert dict(product) == {"is_hidden": 1, "visibility_source": "admin"}


def test_dismissal_reactivates_only_auto_suspended_user(client_factory, settings):
    target = client_factory()
    assert register_user(target, "reporteduser").status_code == 303
    with connect(settings) as conn:
        target_id = conn.execute("SELECT id FROM users WHERE username = 'reporteduser'").fetchone()[
            0
        ]

    reporters = [client_factory() for _ in range(3)]
    for index, reporter in enumerate(reporters, 1):
        username = f"userreport{index}"
        assert register_user(reporter, username).status_code == 303
        make_reporter_eligible(settings, username)
        assert _report(reporter, "user", target_id).status_code == 303

    with connect(settings) as conn:
        target_record = conn.execute(
            "SELECT status, status_source FROM users WHERE id = ?", (target_id,)
        ).fetchone()
    assert dict(target_record) == {"status": "suspended", "status_source": "auto_report"}

    admin = client_factory()
    assert register_user(admin, "userreportadmin").status_code == 303
    make_admin(settings, "userreportadmin")
    with connect(settings) as conn:
        report_id = conn.execute(
            "SELECT id FROM reports WHERE target_type = 'user' AND target_id = ? LIMIT 1",
            (target_id,),
        ).fetchone()[0]
    dismissed = admin.post(
        f"/admin/reports/{report_id}/resolve",
        data={"action": "dismiss", "csrf_token": csrf_for(admin, "/admin")},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert dismissed.status_code == 303
    with connect(settings) as conn:
        target_record = conn.execute(
            "SELECT status, status_source FROM users WHERE id = ?", (target_id,)
        ).fetchone()
    assert dict(target_record) == {"status": "active", "status_source": "none"}
