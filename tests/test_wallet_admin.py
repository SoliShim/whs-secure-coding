from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from conftest import (
    DEFAULT_PASSWORD,
    TEST_ORIGIN,
    csrf_for,
    hidden_value,
    make_admin,
    register_user,
    user_id,
)

from app.db import connect
from app.repository import InsufficientBalanceError, transfer_funds


def _wallet_form(client):
    page = client.get("/wallet")
    assert page.status_code == 200, page.text
    return hidden_value(page.text, "csrf_token"), hidden_value(page.text, "idempotency_key")


def _send(
    client,
    *,
    recipient: str,
    amount: str,
    csrf_token: str,
    idempotency_key: str,
    password: str = DEFAULT_PASSWORD,
):
    return client.post(
        "/wallet/transfers",
        data={
            "recipient": recipient,
            "amount": amount,
            "memo": "Secure marketplace transaction",
            "current_password": password,
            "idempotency_key": idempotency_key,
            "csrf_token": csrf_token,
        },
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )


def test_transfer_moves_integer_balance_atomically_and_ledger_sums_to_zero(
    client_factory, settings
):
    sender = client_factory()
    recipient = client_factory()
    assert register_user(sender, "sender").status_code == 303
    assert register_user(recipient, "receiver").status_code == 303

    csrf_token, idempotency_key = _wallet_form(sender)
    response = _send(
        sender,
        recipient="receiver",
        amount="25000",
        csrf_token=csrf_token,
        idempotency_key=idempotency_key,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/wallet?notice=transferred"

    with connect(settings) as conn:
        balances = {
            row["username"]: row["wallet_balance"]
            for row in conn.execute(
                """
                SELECT username, wallet_balance FROM users
                WHERE username IN ('sender', 'receiver')
                """
            )
        }
        transfer = conn.execute("SELECT * FROM transfers").fetchone()
        ledger = conn.execute(
            "SELECT user_id, delta, balance_after FROM ledger_entries ORDER BY id"
        ).fetchall()
    assert balances == {"sender": 75_000, "receiver": 125_000}
    assert transfer["amount"] == 25_000
    assert len(ledger) == 2
    assert sum(int(entry["delta"]) for entry in ledger) == 0
    assert sorted(int(entry["delta"]) for entry in ledger) == [-25_000, 25_000]
    assert sorted(int(entry["balance_after"]) for entry in ledger) == [75_000, 125_000]


def test_insufficient_balance_and_self_transfer_leave_no_partial_state(client_factory, settings):
    sender = client_factory()
    recipient = client_factory()
    assert register_user(sender, "pooruser").status_code == 303
    assert register_user(recipient, "otheruser").status_code == 303

    csrf_token, first_key = _wallet_form(sender)
    insufficient = _send(
        sender,
        recipient="otheruser",
        amount="100001",
        csrf_token=csrf_token,
        idempotency_key=first_key,
    )
    assert insufficient.status_code == 400
    assert "잔액이 부족" in insufficient.text

    _, second_key = _wallet_form(sender)
    self_transfer = _send(
        sender,
        recipient="pooruser",
        amount="1000",
        csrf_token=csrf_token,
        idempotency_key=second_key,
    )
    assert self_transfer.status_code == 400
    assert "자기 자신" in self_transfer.text

    with connect(settings) as conn:
        balances = list(
            conn.execute(
                "SELECT wallet_balance FROM users WHERE username IN ('pooruser', 'otheruser')"
            )
        )
        assert {row["wallet_balance"] for row in balances} == {100_000}
        assert conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 0


def test_transfer_idempotency_prevents_duplicate_debit(client_factory, settings):
    sender = client_factory()
    recipient = client_factory()
    assert register_user(sender, "idemsender").status_code == 303
    assert register_user(recipient, "idemreceiver").status_code == 303

    csrf_token, key = _wallet_form(sender)
    first = _send(
        sender,
        recipient="idemreceiver",
        amount="12345",
        csrf_token=csrf_token,
        idempotency_key=key,
    )
    duplicate = _send(
        sender,
        recipient="idemreceiver",
        amount="12345",
        csrf_token=csrf_token,
        idempotency_key=key,
    )

    assert first.status_code == 303
    assert duplicate.status_code == 303
    assert duplicate.headers["location"] == "/wallet?notice=duplicate_transfer"

    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 2
        sender_balance = conn.execute(
            "SELECT wallet_balance FROM users WHERE username = 'idemsender'"
        ).fetchone()[0]
        recipient_balance = conn.execute(
            "SELECT wallet_balance FROM users WHERE username = 'idemreceiver'"
        ).fetchone()[0]
    assert sender_balance == 87_655
    assert recipient_balance == 112_345


def test_parallel_distinct_transfers_cannot_overdraw_balance(client_factory, settings):
    sender = client_factory()
    first_recipient = client_factory()
    second_recipient = client_factory()
    assert register_user(sender, "parallelpayer").status_code == 303
    assert register_user(first_recipient, "parallelone").status_code == 303
    assert register_user(second_recipient, "paralleltwo").status_code == 303
    sender_id = user_id(settings, "parallelpayer")
    barrier = threading.Barrier(2)

    def attempt(recipient: str, key: str):
        barrier.wait()
        return transfer_funds(
            settings,
            sender_id=sender_id,
            recipient_username=recipient,
            amount=70_000,
            memo="parallel balance test",
            idempotency_key=key,
            ip_hash=None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt, "parallelone", "parallel_key_00000000000000000001"),
            executor.submit(attempt, "paralleltwo", "parallel_key_00000000000000000002"),
        ]
    successes = [future.result() for future in futures if future.exception() is None]
    failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], InsufficientBalanceError)

    with connect(settings) as conn:
        sender_balance = conn.execute(
            "SELECT wallet_balance FROM users WHERE id = ?", (sender_id,)
        ).fetchone()[0]
        recipient_balances = [
            row["wallet_balance"]
            for row in conn.execute(
                """
                SELECT wallet_balance FROM users
                WHERE username IN ('parallelone', 'paralleltwo') ORDER BY username
                """
            )
        ]
        transfer_count = conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
        ledger = conn.execute("SELECT delta FROM ledger_entries").fetchall()
    assert sender_balance == 30_000
    assert sorted(recipient_balances) == [100_000, 170_000]
    assert transfer_count == 1
    assert len(ledger) == 2
    assert sum(int(row["delta"]) for row in ledger) == 0


def test_parallel_same_idempotency_key_creates_one_transfer(client_factory, settings):
    sender = client_factory()
    recipient = client_factory()
    assert register_user(sender, "parallelidem").status_code == 303
    assert register_user(recipient, "parallelidemreceiver").status_code == 303
    sender_id = user_id(settings, "parallelidem")
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        return transfer_funds(
            settings,
            sender_id=sender_id,
            recipient_username="parallelidemreceiver",
            amount=25_000,
            memo="parallel idempotency test",
            idempotency_key="same_parallel_key_000000000000001",
            ip_hash=None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result() for future in (executor.submit(attempt), executor.submit(attempt))
        ]

    assert sorted(duplicate for _, duplicate in results) == [False, True]
    assert results[0][0]["id"] == results[1][0]["id"]
    with connect(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] == 1
        ledger = conn.execute("SELECT delta FROM ledger_entries").fetchall()
        sender_balance = conn.execute(
            "SELECT wallet_balance FROM users WHERE id = ?", (sender_id,)
        ).fetchone()[0]
    assert len(ledger) == 2
    assert sum(int(row["delta"]) for row in ledger) == 0
    assert sender_balance == 75_000


def test_admin_dashboard_is_forbidden_to_user_and_available_to_admin(client_factory, settings):
    ordinary = client_factory()
    assert register_user(ordinary, "ordinary").status_code == 303
    assert ordinary.get("/admin").status_code == 403

    admin = client_factory()
    assert register_user(admin, "siteadmin").status_code == 303
    make_admin(settings, "siteadmin")
    dashboard = admin.get("/admin")
    assert dashboard.status_code == 200, dashboard.text
    assert "관리자 대시보드" in dashboard.text


def test_only_admin_can_change_user_status_and_suspension_revokes_sessions(
    client_factory, settings
):
    target = client_factory()
    ordinary = client_factory()
    admin = client_factory()
    assert register_user(target, "statustarget").status_code == 303
    assert register_user(ordinary, "normalactor").status_code == 303
    assert register_user(admin, "statusadmin").status_code == 303
    make_admin(settings, "statusadmin")
    target_id = user_id(settings, "statustarget")

    ordinary_csrf = csrf_for(ordinary, "/")
    denied = ordinary.post(
        f"/admin/users/{target_id}/status",
        data={"status": "suspended", "csrf_token": ordinary_csrf},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert denied.status_code == 403

    with connect(settings) as conn:
        status = conn.execute("SELECT status FROM users WHERE id = ?", (target_id,)).fetchone()[0]
    assert status == "active"

    admin_csrf = csrf_for(admin, "/")
    changed = admin.post(
        f"/admin/users/{target_id}/status",
        data={"status": "suspended", "csrf_token": admin_csrf},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )
    assert changed.status_code == 303

    with connect(settings) as conn:
        account = conn.execute("SELECT status FROM users WHERE id = ?", (target_id,)).fetchone()
        sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (target_id,)
        ).fetchone()[0]
        event = conn.execute(
            """
            SELECT actor_user_id, target_id FROM audit_logs
            WHERE event_type = 'admin.user_status_changed' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    assert account["status"] == "suspended"
    assert sessions == 0
    assert event["target_id"] == target_id
    assert target.get("/settings").status_code == 401
