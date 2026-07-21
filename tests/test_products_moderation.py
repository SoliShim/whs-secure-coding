from __future__ import annotations

import re

from conftest import (
    TEST_ORIGIN,
    create_product_http,
    csrf_for,
    make_reporter_eligible,
    register_user,
)

from app.db import connect


def test_product_upload_is_reencoded_and_user_content_is_escaped(client, settings):
    assert register_user(client, "seller").status_code == 303
    product_id = create_product_http(
        client,
        name="<script>alert(1)</script>",
        description="<img src=x onerror=alert(2)> second-hand item description",
    )

    with connect(settings) as conn:
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    assert product is not None
    assert re.fullmatch(r"[0-9a-f]{40}\.jpg", product["image_filename"])
    image_path = settings.upload_dir / product["image_filename"]
    assert image_path.is_file()
    assert image_path.read_bytes().startswith(b"\xff\xd8")

    detail = client.get(f"/products/{product_id}")
    assert detail.status_code == 200
    assert "<script>alert(1)</script>" not in detail.text
    assert "<img src=x onerror=alert(2)>" not in detail.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail.text
    assert "&lt;img src=x onerror=alert(2)&gt;" in detail.text

    image = client.get(f"/uploads/{product['image_filename']}")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/jpeg")
    assert image.headers["x-content-type-options"] == "nosniff"


def test_invalid_image_upload_is_rejected_without_orphan_or_database_row(client, settings):
    assert register_user(client, "imageseller").status_code == 303
    token = csrf_for(client, "/products/new")

    response = client.post(
        "/products",
        data={
            "name": "Not an image",
            "description": "This payload pretends to be an image file.",
            "price": "1000",
            "csrf_token": token,
        },
        files={"image": ("attack.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "JPEG, PNG, WebP" in response.text or "지원하지 않는 이미지" in response.text
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
    assert list(settings.upload_dir.glob("*")) == []


def test_search_treats_sql_syntax_as_literal_data(client, settings):
    assert register_user(client, "searchseller").status_code == 303
    create_product_http(
        client,
        name="Needle Product",
        description="A uniquely searchable safe product description.",
    )

    injection = "%' OR 1=1 --"
    response = client.get("/", params={"q": injection})

    assert response.status_code == 200
    assert "Needle Product" not in response.text
    assert "internal_server_error" not in response.text
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_non_owner_cannot_edit_or_delete_product(client_factory, settings):
    owner = client_factory()
    attacker = client_factory()
    assert register_user(owner, "owneruser").status_code == 303
    assert register_user(attacker, "attacker").status_code == 303
    product_id = create_product_http(owner, name="Owner only item")

    assert attacker.get(f"/products/{product_id}/edit").status_code == 403
    token = csrf_for(attacker, "/")
    edit = attacker.post(
        f"/products/{product_id}/edit",
        data={
            "name": "Stolen item",
            "description": "The attacker must not be able to save this change.",
            "price": "1",
            "status": "sold",
            "csrf_token": token,
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert edit.status_code == 403

    delete = attacker.post(
        f"/products/{product_id}/delete",
        data={"csrf_token": token},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert delete.status_code == 403

    with connect(settings) as conn:
        product = conn.execute(
            "SELECT name, status, is_hidden FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    assert dict(product) == {
        "name": "Owner only item",
        "status": "available",
        "is_hidden": 0,
    }


def _report_product(client, product_id: int):
    token = csrf_for(client, "/")
    return client.post(
        "/reports",
        data={
            "target_type": "product",
            "target_id": str(product_id),
            "reason": "This listing appears fraudulent and needs review.",
            "csrf_token": token,
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )


def test_duplicate_report_is_rejected_and_three_unique_reporters_quarantine_product(
    client_factory, settings
):
    owner = client_factory()
    assert register_user(owner, "reportseller").status_code == 303
    product_id = create_product_http(owner, name="Reported Product")

    reporters = [client_factory() for _ in range(3)]
    for index, reporter in enumerate(reporters, start=1):
        username = f"reporter{index}"
        assert register_user(reporter, username).status_code == 303
        make_reporter_eligible(settings, username)

    first = _report_product(reporters[0], product_id)
    assert first.status_code == 303
    assert first.headers["location"].endswith("notice=reported")

    duplicate = _report_product(reporters[0], product_id)
    assert duplicate.status_code == 409
    assert "이미 신고한 대상" in duplicate.text

    second = _report_product(reporters[1], product_id)
    third = _report_product(reporters[2], product_id)
    assert second.status_code == 303
    assert second.headers["location"].endswith("notice=reported")
    assert third.status_code == 303
    assert third.headers["location"].endswith("notice=quarantined")

    with connect(settings) as conn:
        product = conn.execute(
            "SELECT report_count, is_hidden FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        reports = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE target_type = 'product' AND target_id = ?",
            (product_id,),
        ).fetchone()[0]
        auto_audit = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE event_type = 'moderation.auto_quarantine'"
        ).fetchone()[0]
    assert product["report_count"] == 3
    assert product["is_hidden"] == 1
    assert reports == 3
    assert auto_audit == 1

    anonymous = client_factory()
    assert anonymous.get(f"/products/{product_id}").status_code == 404
