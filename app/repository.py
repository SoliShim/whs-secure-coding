"""파라미터 바인딩만 사용하는 SQLite 저장소 계층."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.db import connect, utc_now
from app.security import hash_token, new_token

AUTO_MODERATION_MIN_ACCOUNT_AGE_SECONDS = 10 * 60


class RepositoryError(RuntimeError):
    """도메인 저장소 오류."""


class ConflictError(RepositoryError):
    """고유 제약 또는 중복 작업."""


class NotFoundError(RepositoryError):
    """대상이 존재하지 않음."""


class InsufficientBalanceError(RepositoryError):
    """잔액 부족."""


class ForbiddenOperationError(RepositoryError):
    """정책상 허용되지 않는 작업."""


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def audit(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    actor_user_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> None:
    """토큰·비밀번호·메시지 본문을 받지 않는 구조화 감사 로그."""

    safe_metadata = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO audit_logs
            (actor_user_id, event_type, target_type, target_id, metadata, ip_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (actor_user_id, event_type, target_type, target_id, safe_metadata, ip_hash, utc_now()),
    )


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict[str, Any] | None:
    return row_dict(conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    return row_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    display_name: str,
    password_hash: str,
    role: str = "user",
    initial_balance: int = 100_000,
    ip_hash: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    try:
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, display_name, password_hash, bio, role, status,
                 wallet_balance, failed_login_count, created_at, updated_at)
            VALUES (?, ?, ?, '', ?, 'active', ?, 0, ?, ?)
            """,
            (username, display_name, password_hash, role, initial_balance, now, now),
        )
    except sqlite3.IntegrityError as exc:
        raise ConflictError("이미 사용 중인 아이디입니다.") from exc
    audit(
        conn,
        "user.registered",
        actor_user_id=cursor.lastrowid,
        target_type="user",
        target_id=cursor.lastrowid,
        ip_hash=ip_hash,
    )
    conn.commit()
    user = get_user_by_id(conn, int(cursor.lastrowid))
    if user is None:
        raise RepositoryError("가입 결과를 불러오지 못했습니다.")
    return user


def update_profile(
    conn: sqlite3.Connection, user_id: int, display_name: str, bio: str
) -> dict[str, Any]:
    conn.execute(
        "UPDATE users SET display_name = ?, bio = ?, updated_at = ? WHERE id = ?",
        (display_name, bio, utc_now(), user_id),
    )
    audit(
        conn, "user.profile_updated", actor_user_id=user_id, target_type="user", target_id=user_id
    )
    conn.commit()
    user = get_user_by_id(conn, user_id)
    if user is None:
        raise NotFoundError("사용자를 찾을 수 없습니다.")
    return user


def update_password(
    conn: sqlite3.Connection,
    user_id: int,
    expected_password_hash: str,
    new_password_hash: str,
) -> None:
    cursor = conn.execute(
        """
        UPDATE users SET password_hash = ?, updated_at = ?
        WHERE id = ? AND password_hash = ?
        """,
        (new_password_hash, utc_now(), user_id, expected_password_hash),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise ConflictError("계정 정보가 변경되었습니다. 다시 로그인해 시도해 주세요.")
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(
        conn, "user.password_changed", actor_user_id=user_id, target_type="user", target_id=user_id
    )
    conn.commit()


def record_login_failure(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT failed_login_count FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    failures = int(row["failed_login_count"]) + 1
    locked_until: str | None = None
    if failures >= 5:
        locked_until = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
        failures = 0
    conn.execute(
        "UPDATE users SET failed_login_count = ?, locked_until = ?, updated_at = ? WHERE id = ?",
        (failures, locked_until, utc_now(), user_id),
    )
    audit(conn, "auth.login_failed", actor_user_id=user_id, target_type="user", target_id=user_id)
    conn.commit()
    return locked_until


def reset_login_failures(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
        (utc_now(), user_id),
    )
    conn.commit()


def list_chat_users(conn: sqlite3.Connection, excluding_user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, username, display_name
        FROM users
        WHERE status = 'active' AND id != ?
        ORDER BY display_name COLLATE NOCASE, username
        """,
        (excluding_user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def create_session(
    conn: sqlite3.Connection, settings: Settings, user_id: int | None = None
) -> tuple[str, dict[str, Any]]:
    raw_token = new_token()
    csrf_token = new_token()
    now = datetime.now(UTC)
    session = {
        "token_hash": hash_token(raw_token),
        "user_id": user_id,
        "csrf_token": csrf_token,
        "created_at": now.isoformat(),
        "last_seen_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=settings.session_ttl_seconds)).isoformat(),
    }
    conn.execute(
        """
        INSERT INTO sessions
            (token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at)
        VALUES (:token_hash, :user_id, :csrf_token, :created_at, :last_seen_at, :expires_at)
        """,
        session,
    )
    conn.commit()
    return raw_token, session


def load_session(
    conn: sqlite3.Connection, settings: Settings, raw_token: str | None
) -> dict[str, Any] | None:
    if not raw_token or len(raw_token) > 200:
        return None
    row = conn.execute(
        """
        SELECT s.*, u.username, u.display_name, u.bio, u.role, u.status AS user_status,
               u.wallet_balance
        FROM sessions s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ?
        """,
        (hash_token(raw_token),),
    ).fetchone()
    if row is None:
        return None
    session = dict(row)
    now = datetime.now(UTC)
    try:
        expires_at = datetime.fromisoformat(session["expires_at"])
        last_seen_at = datetime.fromisoformat(session["last_seen_at"])
    except (TypeError, ValueError):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
        conn.commit()
        return None
    if expires_at <= now or last_seen_at + timedelta(seconds=settings.session_idle_seconds) <= now:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
        conn.commit()
        return None
    if session["user_id"] is not None and session.get("user_status") != "active":
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
        conn.commit()
        return None
    if (now - last_seen_at).total_seconds() >= 60:
        session["last_seen_at"] = now.isoformat()
        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
            (session["last_seen_at"], session["token_hash"]),
        )
        conn.commit()
    session["raw_token"] = raw_token
    return session


def delete_session(conn: sqlite3.Connection, raw_token: str | None) -> None:
    if raw_token and len(raw_token) <= 200:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(raw_token),))
        conn.commit()


def rotate_session(
    conn: sqlite3.Connection,
    settings: Settings,
    old_raw_token: str | None,
    user_id: int,
) -> tuple[str, dict[str, Any]]:
    delete_session(conn, old_raw_token)
    return create_session(conn, settings, user_id)


def escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def list_products(
    conn: sqlite3.Connection,
    query: str = "",
    *,
    include_hidden: bool = False,
    limit: int = 60,
    offset: int = 0,
) -> list[dict[str, Any]]:
    pattern = f"%{escape_like(query)}%"
    parameters = (
        1 if include_hidden else 0,
        query,
        pattern,
        pattern,
        min(max(limit, 1), 100),
        min(max(offset, 0), 100_000),
    )
    rows = conn.execute(
        """
        SELECT p.*, u.username AS seller_username, u.display_name AS seller_display_name
        FROM products p
        JOIN users u ON u.id = p.seller_id
        WHERE p.status != 'deleted'
          AND (? = 1 OR p.is_hidden = 0)
          AND (? = '' OR p.name LIKE ? ESCAPE '!' OR p.description LIKE ? ESCAPE '!')
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def count_products(
    conn: sqlite3.Connection, query: str = "", *, include_hidden: bool = False
) -> int:
    pattern = f"%{escape_like(query)}%"
    row = conn.execute(
        """
        SELECT COUNT(*) AS count FROM products
        WHERE status != 'deleted'
          AND (? = 1 OR is_hidden = 0)
          AND (? = '' OR name LIKE ? ESCAPE '!' OR description LIKE ? ESCAPE '!')
        """,
        (1 if include_hidden else 0, query, pattern, pattern),
    ).fetchone()
    return int(row["count"])


def get_product(conn: sqlite3.Connection, product_id: int) -> dict[str, Any] | None:
    return row_dict(
        conn.execute(
            """
            SELECT p.*, u.username AS seller_username, u.display_name AS seller_display_name
            FROM products p JOIN users u ON u.id = p.seller_id
            WHERE p.id = ?
            """,
            (product_id,),
        ).fetchone()
    )


def create_product(
    conn: sqlite3.Connection,
    *,
    seller_id: int,
    name: str,
    description: str,
    price: int,
    image_filename: str,
) -> dict[str, Any]:
    now = utc_now()
    cursor = conn.execute(
        """
        INSERT INTO products
            (seller_id, name, description, price, image_filename, status, is_hidden,
             report_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'available', 0, 0, ?, ?)
        """,
        (seller_id, name, description, price, image_filename, now, now),
    )
    audit(
        conn,
        "product.created",
        actor_user_id=seller_id,
        target_type="product",
        target_id=cursor.lastrowid,
    )
    conn.commit()
    product = get_product(conn, int(cursor.lastrowid))
    if product is None:
        raise RepositoryError("상품 등록 결과를 불러오지 못했습니다.")
    return product


def update_product(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    actor_id: int,
    actor_role: str,
    name: str,
    description: str,
    price: int,
    status: str,
    image_filename: str | None,
) -> dict[str, Any]:
    product = get_product(conn, product_id)
    if product is None or product["status"] == "deleted":
        raise NotFoundError("상품을 찾을 수 없습니다.")
    if int(product["seller_id"]) != actor_id and actor_role != "admin":
        raise ForbiddenOperationError("이 상품을 수정할 권한이 없습니다.")
    next_image = image_filename or product["image_filename"]
    conn.execute(
        """
        UPDATE products
        SET name = ?, description = ?, price = ?, status = ?, image_filename = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, description, price, status, next_image, utc_now(), product_id),
    )
    audit(
        conn,
        "product.updated",
        actor_user_id=actor_id,
        target_type="product",
        target_id=product_id,
    )
    conn.commit()
    updated = get_product(conn, product_id)
    if updated is None:
        raise RepositoryError("상품 수정 결과를 불러오지 못했습니다.")
    return updated


def delete_product(
    conn: sqlite3.Connection, product_id: int, actor_id: int, actor_role: str
) -> str | None:
    product = get_product(conn, product_id)
    if product is None or product["status"] == "deleted":
        raise NotFoundError("상품을 찾을 수 없습니다.")
    if int(product["seller_id"]) != actor_id and actor_role != "admin":
        raise ForbiddenOperationError("이 상품을 삭제할 권한이 없습니다.")
    conn.execute(
        "UPDATE products SET status = 'deleted', is_hidden = 1, updated_at = ? WHERE id = ?",
        (utc_now(), product_id),
    )
    audit(
        conn,
        "product.deleted",
        actor_user_id=actor_id,
        target_type="product",
        target_id=product_id,
    )
    conn.commit()
    return product.get("image_filename")


def list_user_products(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM products
        WHERE seller_id = ? AND status != 'deleted'
        ORDER BY created_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def create_message(
    conn: sqlite3.Connection,
    *,
    sender_id: int,
    recipient_id: int | None,
    body: str,
    client_nonce: str,
) -> dict[str, Any]:
    if recipient_id is not None:
        recipient = get_user_by_id(conn, recipient_id)
        if recipient is None or recipient["status"] != "active" or recipient_id == sender_id:
            raise ForbiddenOperationError("대화 상대를 확인할 수 없습니다.")
    try:
        cursor = conn.execute(
            """
            INSERT INTO messages (sender_id, recipient_id, body, client_nonce, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sender_id, recipient_id, body, client_nonce, utc_now()),
        )
    except sqlite3.IntegrityError as exc:
        raise ConflictError("이미 처리한 메시지입니다.") from exc
    conn.commit()
    row = conn.execute(
        """
        SELECT m.*, u.username AS sender_username, u.display_name AS sender_display_name
        FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    if row is None:
        raise RepositoryError("메시지를 불러오지 못했습니다.")
    return dict(row)


def message_history(
    conn: sqlite3.Connection, user_id: int, recipient_id: int | None, limit: int = 100
) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 100)
    if recipient_id is None:
        rows = conn.execute(
            """
            SELECT m.*, u.username AS sender_username, u.display_name AS sender_display_name
            FROM messages m JOIN users u ON u.id = m.sender_id
            WHERE m.recipient_id IS NULL
            ORDER BY m.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT m.*, u.username AS sender_username, u.display_name AS sender_display_name
            FROM messages m JOIN users u ON u.id = m.sender_id
            WHERE (m.sender_id = ? AND m.recipient_id = ?)
               OR (m.sender_id = ? AND m.recipient_id = ?)
            ORDER BY m.id DESC LIMIT ?
            """,
            (user_id, recipient_id, recipient_id, user_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def create_report(
    settings: Settings,
    *,
    reporter_id: int,
    target_type: str,
    target_id: int,
    reason: str,
) -> tuple[dict[str, Any], bool]:
    conn = connect(settings)
    auto_action = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        reporter = conn.execute(
            "SELECT id, status, created_at FROM users WHERE id = ?", (reporter_id,)
        ).fetchone()
        if reporter is None or reporter["status"] != "active":
            raise ForbiddenOperationError("신고 권한이 없는 계정입니다.")
        now = datetime.now(UTC)
        try:
            reporter_created_at = datetime.fromisoformat(reporter["created_at"])
        except (TypeError, ValueError):
            reporter_created_at = now
        auto_eligible = int(
            (now - reporter_created_at).total_seconds() >= AUTO_MODERATION_MIN_ACCOUNT_AGE_SECONDS
        )
        if target_type == "product":
            target = conn.execute(
                "SELECT id, seller_id, status, is_hidden, visibility_source "
                "FROM products WHERE id = ?",
                (target_id,),
            ).fetchone()
            if target is None or target["status"] == "deleted":
                raise NotFoundError("신고할 상품을 찾을 수 없습니다.")
            if int(target["seller_id"]) == reporter_id:
                raise ForbiddenOperationError("자신의 상품은 신고할 수 없습니다.")
        elif target_type == "user":
            target = conn.execute(
                "SELECT id, role, status, status_source FROM users WHERE id = ?", (target_id,)
            ).fetchone()
            if target is None:
                raise NotFoundError("신고할 사용자를 찾을 수 없습니다.")
            if target_id == reporter_id:
                raise ForbiddenOperationError("자신을 신고할 수 없습니다.")
        else:
            raise ForbiddenOperationError("허용되지 않은 신고 유형입니다.")

        try:
            cursor = conn.execute(
                """
                INSERT INTO reports
                    (reporter_id, target_type, target_id, reason, status,
                     auto_eligible, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (reporter_id, target_type, target_id, reason, auto_eligible, utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("이미 신고한 대상입니다.") from exc

        count = int(
            conn.execute(
                """
                SELECT COUNT(*) AS count FROM reports
                WHERE target_type = ? AND target_id = ?
                  AND status IN ('pending', 'actioned') AND auto_eligible = 1
                """,
                (target_type, target_id),
            ).fetchone()["count"]
        )
        if target_type == "product":
            conn.execute("UPDATE products SET report_count = ? WHERE id = ?", (count, target_id))
            if count >= settings.report_threshold:
                conn.execute(
                    """
                    UPDATE products
                    SET is_hidden = 1,
                        visibility_source = CASE
                            WHEN is_hidden = 0 THEN 'auto_report' ELSE visibility_source END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (utc_now(), target_id),
                )
                auto_action = True
        else:
            if count >= settings.report_threshold and target["role"] != "admin":
                conn.execute(
                    """
                    UPDATE users
                    SET status = 'suspended',
                        status_source = CASE
                            WHEN status = 'active' THEN 'auto_report' ELSE status_source END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (utc_now(), target_id),
                )
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
                auto_action = True
        audit(
            conn,
            "report.created",
            actor_user_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            metadata={
                "report_id": cursor.lastrowid,
                "auto_action": auto_action,
                "auto_eligible": bool(auto_eligible),
            },
        )
        if auto_action:
            audit(
                conn,
                "moderation.auto_quarantine",
                target_type=target_type,
                target_id=target_id,
                metadata={"unique_report_count": count},
            )
        conn.commit()
        report = row_dict(
            conn.execute("SELECT * FROM reports WHERE id = ?", (cursor.lastrowid,)).fetchone()
        )
        if report is None:
            raise RepositoryError("신고 결과를 불러오지 못했습니다.")
        return report, auto_action
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def transfer_funds(
    settings: Settings,
    *,
    sender_id: int,
    recipient_username: str,
    amount: int,
    memo: str,
    idempotency_key: str,
    ip_hash: str | None,
) -> tuple[dict[str, Any], bool]:
    """BEGIN IMMEDIATE와 멱등키로 이중 출금을 방지한다."""

    conn = connect(settings)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT t.*, u.username AS recipient_username
            FROM transfers t JOIN users u ON u.id = t.recipient_id
            WHERE t.sender_id = ? AND t.idempotency_key = ?
            """,
            (sender_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if (
                existing["recipient_username"] != recipient_username
                or int(existing["amount"]) != amount
                or existing["memo"] != memo
            ):
                raise ConflictError("이미 다른 송금에 사용한 요청 식별자입니다.")
            conn.commit()
            return dict(existing), True

        sender = conn.execute(
            "SELECT id, status, wallet_balance FROM users WHERE id = ?", (sender_id,)
        ).fetchone()
        recipient = conn.execute(
            "SELECT id, status, wallet_balance FROM users WHERE username = ?",
            (recipient_username,),
        ).fetchone()
        if sender is None or sender["status"] != "active":
            raise ForbiddenOperationError("송금할 수 없는 계정입니다.")
        if recipient is None or recipient["status"] != "active":
            raise NotFoundError("받는 사용자를 찾을 수 없습니다.")
        if int(recipient["id"]) == sender_id:
            raise ForbiddenOperationError("자기 자신에게 송금할 수 없습니다.")
        if amount < 1 or amount > 1_000_000:
            raise ForbiddenOperationError("송금액은 1원 이상 100만원 이하여야 합니다.")

        debit = conn.execute(
            """
            UPDATE users SET wallet_balance = wallet_balance - ?, updated_at = ?
            WHERE id = ? AND status = 'active' AND wallet_balance >= ?
            """,
            (amount, utc_now(), sender_id, amount),
        )
        if debit.rowcount != 1:
            raise InsufficientBalanceError("잔액이 부족합니다.")
        credit = conn.execute(
            """
            UPDATE users SET wallet_balance = wallet_balance + ?, updated_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (amount, utc_now(), int(recipient["id"])),
        )
        if credit.rowcount != 1:
            raise ForbiddenOperationError("받는 계정이 송금을 받을 수 없습니다.")

        created_at = utc_now()
        cursor = conn.execute(
            """
            INSERT INTO transfers
                (sender_id, recipient_id, amount, memo, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sender_id, int(recipient["id"]), amount, memo, idempotency_key, created_at),
        )
        transfer_id = int(cursor.lastrowid)
        sender_balance = int(sender["wallet_balance"]) - amount
        recipient_balance = int(recipient["wallet_balance"]) + amount
        conn.executemany(
            """
            INSERT INTO ledger_entries
                (transfer_id, user_id, delta, balance_after, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (transfer_id, sender_id, -amount, sender_balance, created_at),
                (transfer_id, int(recipient["id"]), amount, recipient_balance, created_at),
            ),
        )
        audit(
            conn,
            "wallet.transfer",
            actor_user_id=sender_id,
            target_type="user",
            target_id=int(recipient["id"]),
            metadata={"transfer_id": transfer_id, "amount": amount},
            ip_hash=ip_hash,
        )
        conn.commit()
        transfer = row_dict(
            conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
        )
        if transfer is None:
            raise RepositoryError("송금 결과를 불러오지 못했습니다.")
        return transfer, False
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wallet_history(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.*,
               sender.username AS sender_username,
               recipient.username AS recipient_username
        FROM transfers t
        JOIN users sender ON sender.id = t.sender_id
        JOIN users recipient ON recipient.id = t.recipient_id
        WHERE t.sender_id = ? OR t.recipient_id = ?
        ORDER BY t.id DESC LIMIT 100
        """,
        (user_id, user_id),
    ).fetchall()
    return [dict(row) for row in rows]


def admin_snapshot(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]] | dict[str, int]]:
    counts = {
        "users": int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]),
        "products": int(
            conn.execute("SELECT COUNT(*) FROM products WHERE status != 'deleted'").fetchone()[0]
        ),
        "pending_reports": int(
            conn.execute("SELECT COUNT(*) FROM reports WHERE status = 'pending'").fetchone()[0]
        ),
        "transfers": int(conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]),
    }
    users = [
        dict(row)
        for row in conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.bio, u.role, u.status,
                   u.wallet_balance, u.failed_login_count, u.locked_until,
                   u.created_at, u.updated_at,
                   (SELECT COUNT(*) FROM reports r
                    WHERE r.target_type = 'user' AND r.target_id = u.id
                      AND r.status IN ('pending', 'actioned')) AS report_count
            FROM users u ORDER BY u.id DESC LIMIT 100
            """
        )
    ]
    products = [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.*, u.username AS seller_username FROM products p
            JOIN users u ON u.id = p.seller_id
            WHERE p.status != 'deleted'
            ORDER BY p.id DESC LIMIT 100
            """
        )
    ]
    reports = [
        dict(row)
        for row in conn.execute(
            """
            SELECT r.*, u.username AS reporter_username,
                   COALESCE(target_user.username, target_product.name) AS target_label,
                   target_user.username AS target_username,
                   CASE WHEN target_product.status != 'deleted' THEN target_product.id END
                       AS target_product_id
            FROM reports r
            JOIN users u ON u.id = r.reporter_id
            LEFT JOIN users target_user
              ON r.target_type = 'user' AND target_user.id = r.target_id
            LEFT JOIN products target_product
              ON r.target_type = 'product' AND target_product.id = r.target_id
            WHERE r.status = 'pending' ORDER BY r.id DESC LIMIT 100
            """
        )
    ]
    transfers = [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.id, t.sender_id, t.recipient_id, t.amount, t.memo, t.created_at,
                   sender.username AS sender_username,
                   recipient.username AS recipient_username
            FROM transfers t
            JOIN users sender ON sender.id = t.sender_id
            JOIN users recipient ON recipient.id = t.recipient_id
            ORDER BY t.id DESC LIMIT 50
            """
        )
    ]
    audit_logs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT a.id, a.event_type, a.target_type, a.target_id, a.metadata,
                   a.created_at, u.username AS actor_username
            FROM audit_logs a LEFT JOIN users u ON u.id = a.actor_user_id
            ORDER BY a.id DESC LIMIT 100
            """
        )
    ]
    return {
        "counts": counts,
        "users": users,
        "products": products,
        "reports": reports,
        "transfers": transfers,
        "audit_logs": audit_logs,
    }


def set_user_status(
    conn: sqlite3.Connection, *, actor_id: int, target_id: int, status: str
) -> None:
    target = get_user_by_id(conn, target_id)
    if target is None:
        raise NotFoundError("사용자를 찾을 수 없습니다.")
    if target["role"] == "admin" and status != "active":
        raise ForbiddenOperationError("관리자 계정은 이 화면에서 정지할 수 없습니다.")
    conn.execute(
        "UPDATE users SET status = ?, status_source = ?, updated_at = ? WHERE id = ?",
        (status, "none" if status == "active" else "admin", utc_now(), target_id),
    )
    if status != "active":
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    audit(
        conn,
        "admin.user_status_changed",
        actor_user_id=actor_id,
        target_type="user",
        target_id=target_id,
        metadata={"status": status},
    )
    conn.commit()


def set_product_visibility(
    conn: sqlite3.Connection, *, actor_id: int, product_id: int, visible: bool
) -> None:
    product = get_product(conn, product_id)
    if product is None:
        raise NotFoundError("상품을 찾을 수 없습니다.")
    if product["status"] == "deleted":
        raise NotFoundError("삭제된 상품은 노출 상태를 변경할 수 없습니다.")
    conn.execute(
        """
        UPDATE products SET is_hidden = ?, visibility_source = ?, updated_at = ? WHERE id = ?
        """,
        (0 if visible else 1, "none" if visible else "admin", utc_now(), product_id),
    )
    audit(
        conn,
        "admin.product_visibility_changed",
        actor_user_id=actor_id,
        target_type="product",
        target_id=product_id,
        metadata={"visible": visible},
    )
    conn.commit()


def resolve_report(
    conn: sqlite3.Connection,
    *,
    actor_id: int,
    report_id: int,
    action: str,
    report_threshold: int,
) -> None:
    report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise NotFoundError("신고를 찾을 수 없습니다.")
    if report["status"] != "pending":
        raise ConflictError("이미 처리된 신고입니다.")
    status = "dismissed" if action == "dismiss" else "actioned"
    conn.execute(
        """
        UPDATE reports SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ?
        """,
        (status, utc_now(), actor_id, report_id),
    )
    if report["target_type"] == "product":
        active_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM reports
                WHERE target_type = 'product' AND target_id = ?
                  AND status IN ('pending', 'actioned') AND auto_eligible = 1
                """,
                (report["target_id"],),
            ).fetchone()[0]
        )
        conn.execute(
            "UPDATE products SET report_count = ?, updated_at = ? WHERE id = ?",
            (active_count, utc_now(), report["target_id"]),
        )
        if action == "dismiss" and active_count < report_threshold:
            conn.execute(
                """
                UPDATE products
                SET is_hidden = 0, visibility_source = 'none', updated_at = ?
                WHERE id = ? AND visibility_source = 'auto_report'
                """,
                (utc_now(), report["target_id"]),
            )
    else:
        active_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM reports
                WHERE target_type = 'user' AND target_id = ?
                  AND status IN ('pending', 'actioned') AND auto_eligible = 1
                """,
                (report["target_id"],),
            ).fetchone()[0]
        )
        if action == "dismiss" and active_count < report_threshold:
            conn.execute(
                """
                UPDATE users
                SET status = 'active', status_source = 'none', updated_at = ?
                WHERE id = ? AND status_source = 'auto_report'
                """,
                (utc_now(), report["target_id"]),
            )
    audit(
        conn,
        "admin.report_resolved",
        actor_user_id=actor_id,
        target_type=report["target_type"],
        target_id=int(report["target_id"]),
        metadata={"report_id": report_id, "action": action},
    )
    conn.commit()
