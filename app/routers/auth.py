"""가입, 로그인, 프로필, 비밀번호 라우터."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.db import connect
from app.repository import (
    ConflictError,
    audit,
    create_user,
    delete_session,
    get_user_by_id,
    get_user_by_username,
    record_login_failure,
    reset_login_failures,
    rotate_session,
    update_password,
    update_profile,
)
from app.routers.products import present_product
from app.security import hash_password, validate_password, validate_username, verify_password
from app.web import (
    client_ip_hash,
    public_user,
    render,
    require_user,
    safe_next,
    verify_csrf,
)

router = APIRouter()


def _limited(request: Request, bucket: str, key: str, limit: int, window: int) -> bool:
    return not request.app.state.rate_limiter.allow(f"{bucket}:{key}", limit, window)


def _display_name(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not 2 <= len(cleaned) <= 30 or any(ord(character) < 32 for character in cleaned):
        raise ValueError("표시 이름은 2~30자의 일반 문자로 입력해 주세요.")
    return cleaned


def _bio(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > 500 or "\x00" in cleaned:
        raise ValueError("소개글은 500자 이하로 입력해 주세요.")
    return cleaned


def _logout_record(settings, raw_token: str | None, user: dict | None) -> None:
    with connect(settings) as conn:
        delete_session(conn, raw_token)
        if user:
            audit(
                conn,
                "auth.logout",
                actor_user_id=int(user["id"]),
                target_type="user",
                target_id=int(user["id"]),
            )
            conn.commit()


def _account_record(settings, user_id: int):
    with connect(settings) as conn:
        return get_user_by_id(conn, user_id)


def _commit_password_change(
    settings,
    user_id: int,
    expected_password_hash: str,
    new_password_hash: str,
) -> None:
    with connect(settings) as conn:
        update_password(
            conn,
            user_id,
            expected_password_hash,
            new_password_hash,
        )


@router.get("/register")
def register_page(request: Request):
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/register.html", form={})


@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_token: str = Form(...),
):
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    ip_hash = client_ip_hash(request, settings)
    form = {"username": username, "display_name": display_name}
    if _limited(request, "register", ip_hash, 5, 3600):
        return render(
            request,
            "auth/register.html",
            status_code=429,
            error="가입 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            form=form,
        )
    try:
        normalized_username = validate_username(username)
        clean_name = _display_name(display_name)
        validate_password(password)
        if password != password_confirm:
            raise ValueError("비밀번호 확인이 일치하지 않습니다.")
        password_hash = hash_password(password)
        with connect(settings) as conn:
            user = create_user(
                conn,
                username=normalized_username,
                display_name=clean_name,
                password_hash=password_hash,
                ip_hash=ip_hash,
            )
            raw_token, session = rotate_session(
                conn,
                settings,
                request.state.session.get("raw_token"),
                int(user["id"]),
            )
            loaded_user = get_user_by_id(conn, int(user["id"]))
        session.update(
            {
                "username": loaded_user["username"],
                "display_name": loaded_user["display_name"],
                "bio": loaded_user["bio"],
                "role": loaded_user["role"],
                "user_status": loaded_user["status"],
                "wallet_balance": loaded_user["wallet_balance"],
                "raw_token": raw_token,
            }
        )
        request.state.session = session
        request.state.user = public_user(session)
        request.state.session_cookie_to_set = raw_token
        return RedirectResponse("/?notice=registered", status_code=303)
    except (ValueError, ConflictError) as exc:
        return render(
            request,
            "auth/register.html",
            status_code=400,
            error=str(exc),
            form=form,
        )


@router.get("/login")
def login_page(request: Request, next: str | None = None):  # noqa: A002
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/login.html", next=safe_next(next), form={})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    next: str = Form("/"),  # noqa: A002
):
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    ip_hash = client_ip_hash(request, settings)
    generic_error = "아이디 또는 비밀번호를 확인해 주세요."
    if _limited(request, "login", ip_hash, 20, 300):
        return render(
            request,
            "auth/login.html",
            status_code=429,
            error="로그인 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            form={"username": username},
            next=safe_next(next),
        )

    try:
        normalized_username = validate_username(username)
    except ValueError:
        normalized_username = "invalid-user"

    with connect(settings) as conn:
        user = get_user_by_username(conn, normalized_username)
        password_hash = user["password_hash"] if user else request.app.state.dummy_password_hash
        password_ok = verify_password(password_hash, password)
        locked = False
        if user and user.get("locked_until"):
            try:
                locked = datetime.fromisoformat(user["locked_until"]) > datetime.now(UTC)
            except (TypeError, ValueError):
                locked = True
        if not user or not password_ok or locked or user["status"] != "active":
            if user and not locked:
                record_login_failure(conn, int(user["id"]))
            return render(
                request,
                "auth/login.html",
                status_code=400,
                error=generic_error,
                form={"username": username},
                next=safe_next(next),
            )

        reset_login_failures(conn, int(user["id"]))
        raw_token, session = rotate_session(
            conn,
            settings,
            request.state.session.get("raw_token"),
            int(user["id"]),
        )
        audit(
            conn,
            "auth.login_succeeded",
            actor_user_id=int(user["id"]),
            target_type="user",
            target_id=int(user["id"]),
            ip_hash=ip_hash,
        )
        conn.commit()

    session.update(
        {
            "username": user["username"],
            "display_name": user["display_name"],
            "bio": user["bio"],
            "role": user["role"],
            "user_status": user["status"],
            "wallet_balance": user["wallet_balance"],
            "raw_token": raw_token,
        }
    )
    request.state.session = session
    request.state.user = public_user(session)
    request.state.session_cookie_to_set = raw_token
    return RedirectResponse(f"{safe_next(next)}?notice=logged_in", status_code=303)


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...)):
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    user = request.state.user
    await run_in_threadpool(
        _logout_record,
        settings,
        request.state.session.get("raw_token"),
        user,
    )
    request.state.session_cookie_to_delete = True
    request.state.user = None
    if user:
        await request.app.state.chat_manager.disconnect_user(int(user["id"]))
    return RedirectResponse("/?notice=logged_out", status_code=303)


@router.get("/users/{username}")
def user_profile(request: Request, username: str):
    settings = request.app.state.settings
    try:
        normalized = validate_username(username)
    except ValueError:
        normalized = "invalid-user"
    with connect(settings) as conn:
        profile = get_user_by_username(conn, normalized)
        profile_products = []
        if profile is not None:
            profile_products = [
                present_product(dict(row))
                for row in conn.execute(
                    """
                    SELECT p.*, u.username AS seller_username,
                           u.display_name AS seller_display_name
                    FROM products p JOIN users u ON u.id = p.seller_id
                    WHERE p.seller_id = ? AND p.is_hidden = 0
                      AND p.status IN ('available', 'reserved', 'sold')
                    ORDER BY p.id DESC LIMIT 24
                    """,
                    (profile["id"],),
                ).fetchall()
            ]
    viewer = request.state.user
    admin_view = bool(viewer and viewer["role"] == "admin")
    if profile is None or (profile["status"] != "active" and not admin_view):
        return render(
            request,
            "error.html",
            status_code=404,
            error="사용자를 찾을 수 없습니다.",
            title="사용자 없음",
        )
    return render(
        request,
        "users/profile.html",
        profile=profile,
        profile_user=profile,
        products=profile_products,
        product_count=len(profile_products),
    )


@router.get("/settings")
def settings_page(request: Request):
    user = require_user(request)
    with connect(request.app.state.settings) as conn:
        profile = get_user_by_id(conn, int(user["id"]))
    return render(request, "users/edit.html", profile=profile)


@router.post("/settings/profile")
def save_profile(
    request: Request,
    display_name: str = Form(...),
    bio: str = Form(""),
    csrf_token: str = Form(...),
):
    user = require_user(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    try:
        clean_name = _display_name(display_name)
        clean_bio = _bio(bio)
        with connect(settings) as conn:
            update_profile(conn, int(user["id"]), clean_name, clean_bio)
        request.state.session["display_name"] = clean_name
        request.state.session["bio"] = clean_bio
        request.state.user = public_user(request.state.session)
        return RedirectResponse("/settings?notice=profile_saved", status_code=303)
    except ValueError as exc:
        return render(
            request,
            "users/edit.html",
            status_code=400,
            error=str(exc),
            profile={**user, "display_name": display_name, "bio": bio},
        )


@router.post("/settings/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_user(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    ip_hash = client_ip_hash(request, settings)
    if _limited(request, "credential-change", f"{user['id']}:{ip_hash}", 5, 900):
        return render(
            request,
            "users/edit.html",
            status_code=429,
            error="비밀번호 변경 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            profile=user,
        )
    record = await run_in_threadpool(_account_record, settings, int(user["id"]))
    password_ok = bool(
        record
        and await run_in_threadpool(verify_password, record["password_hash"], current_password)
    )
    if not password_ok:
        return render(
            request,
            "users/edit.html",
            status_code=400,
            error="현재 비밀번호가 올바르지 않습니다.",
            profile=record or user,
        )
    try:
        validate_password(new_password)
        if new_password != new_password_confirm:
            raise ValueError("새 비밀번호 확인이 일치하지 않습니다.")
        if await run_in_threadpool(verify_password, record["password_hash"], new_password):
            raise ValueError("기존 비밀번호와 다른 비밀번호를 사용해 주세요.")
    except ValueError as exc:
        return render(
            request,
            "users/edit.html",
            status_code=400,
            error=str(exc),
            profile=record,
        )
    new_password_hash = await run_in_threadpool(hash_password, new_password)
    try:
        await run_in_threadpool(
            _commit_password_change,
            settings,
            int(user["id"]),
            record["password_hash"],
            new_password_hash,
        )
    except ConflictError as exc:
        return render(
            request,
            "users/edit.html",
            status_code=409,
            error=str(exc),
            profile=record,
        )
    request.state.session_cookie_to_delete = True
    request.state.user = None
    await request.app.state.chat_manager.disconnect_user(int(user["id"]))
    return RedirectResponse("/login?notice=credential_updated", status_code=303)
