"""웹 요청 공통 컨텍스트, 인증·CSRF·렌더링 보조 함수."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from app.config import Settings
from app.db import connect
from app.repository import load_session
from app.security import csrf_matches, ip_fingerprint, new_token

SESSION_COOKIE = "baromarket_session"

NOTICE_MESSAGES = {
    "registered": "회원가입이 완료되었습니다.",
    "logged_in": "안전하게 로그인했습니다.",
    "logged_out": "로그아웃했습니다.",
    "profile_saved": "프로필을 저장했습니다.",
    "credential_updated": "비밀번호를 변경했습니다. 다시 로그인해 주세요.",
    "product_created": "상품을 등록했습니다.",
    "product_updated": "상품 정보를 저장했습니다.",
    "product_deleted": "상품을 삭제했습니다.",
    "reported": "신고를 접수했습니다. 관리자가 확인합니다.",
    "quarantined": "신고를 접수했고 기준 도달로 대상을 임시 격리했습니다.",
    "transferred": "가상 지갑 송금이 완료되었습니다.",
    "duplicate_transfer": "이미 처리된 송금 요청입니다. 중복 출금하지 않았습니다.",
    "admin_saved": "관리자 조치를 반영했습니다.",
}

ANONYMOUS_SESSION_PREFIX = "a"
LOGGER = logging.getLogger("baromarket.security")


def public_user(session: dict[str, Any] | None) -> dict[str, Any] | None:
    if not session or session.get("user_id") is None:
        return None
    return {
        "id": int(session["user_id"]),
        "username": session["username"],
        "display_name": session["display_name"],
        "bio": session.get("bio", ""),
        "role": session["role"],
        "status": session["user_status"],
        "wallet_balance": int(session["wallet_balance"]),
    }


def _new_anonymous_session(settings: Settings) -> tuple[str, dict[str, Any]]:
    """DB 쓰기 없이 로그인·가입 폼용 서명 CSRF 세션을 만든다."""

    issued_at = str(int(time.time()))
    csrf_token = new_token()
    payload = f"{issued_at}.{csrf_token}"
    signature = hmac.new(
        settings.app_secret.encode("utf-8"),
        f"anonymous-session:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    raw_token = f"{ANONYMOUS_SESSION_PREFIX}.{payload}.{signature}"
    return raw_token, {
        "user_id": None,
        "csrf_token": csrf_token,
        "raw_token": raw_token,
        "anonymous": True,
    }


def _load_anonymous_session(raw_token: str, settings: Settings) -> dict[str, Any] | None:
    parts = raw_token.split(".")
    if len(parts) != 4 or parts[0] != ANONYMOUS_SESSION_PREFIX:
        return None
    _, issued_text, csrf_token, supplied_signature = parts
    if not issued_text.isascii() or not issued_text.isdigit() or not 32 <= len(csrf_token) <= 200:
        return None
    try:
        issued_at = int(issued_text)
    except ValueError:
        return None
    now = int(time.time())
    if issued_at > now + 60 or now - issued_at > settings.session_ttl_seconds:
        return None
    payload = f"{issued_text}.{csrf_token}"
    expected_signature = hmac.new(
        settings.app_secret.encode("utf-8"),
        f"anonymous-session:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, supplied_signature):
        return None
    return {
        "user_id": None,
        "csrf_token": csrf_token,
        "raw_token": raw_token,
        "anonymous": True,
    }


def attach_session(request: Request, settings: Settings, *, issue_anonymous: bool = False) -> None:
    """유효 세션을 연결한다; 익명 폼 세션은 서명 쿠키라 DB를 사용하지 않는다."""

    request.state.session_cookie_to_set = None
    request.state.session_cookie_to_delete = False
    raw_token = request.cookies.get(SESSION_COOKIE)
    session: dict[str, Any] | None = None
    if raw_token:
        if raw_token.startswith(f"{ANONYMOUS_SESSION_PREFIX}."):
            session = _load_anonymous_session(raw_token, settings)
        else:
            with connect(settings) as conn:
                session = load_session(conn, settings, raw_token)
        if session is None:
            request.state.session_cookie_to_delete = True
    if session is None and issue_anonymous:
        raw_token, session = _new_anonymous_session(settings)
        request.state.session_cookie_to_set = raw_token
        request.state.session_cookie_to_delete = False
    if session is None:
        session = {}
    request.state.session = session
    request.state.user = public_user(session)


def apply_session_cookie(request: Request, response: Response, settings: Settings) -> None:
    if getattr(request.state, "session_cookie_to_delete", False):
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return
    raw_token = getattr(request.state, "session_cookie_to_set", None)
    if raw_token:
        response.set_cookie(
            SESSION_COOKIE,
            raw_token,
            max_age=settings.session_ttl_seconds,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )


def client_ip_hash(request: Request, settings: Settings) -> str:
    # 신뢰 프록시 구성은 배포 계층 책임이다. 앱에서는 파싱되지 않은 X-Forwarded-For를 쓰지 않는다.
    host = request.client.host if request.client else "unknown"
    return ip_fingerprint(host, settings.app_secret)


def require_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한이 필요합니다."
        )
    return user


def origin_allowed(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") in settings.allowed_origins
    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return referer_origin in settings.allowed_origins
    # 비브라우저 클라이언트에는 Origin이 없을 수 있다. 예측 불가능한 CSRF 토큰 검증은 계속 적용된다.
    return True


def verify_csrf(request: Request, token: str, settings: Settings) -> None:
    expected = getattr(request.state, "session", {}).get("csrf_token")
    origin_ok = origin_allowed(request, settings)
    token_ok = csrf_matches(expected, token)
    if not origin_ok or not token_ok:
        # Never log either token.  The bounded origin value and booleans are
        # sufficient for operators to distinguish origin and session failures.
        LOGGER.warning(
            "csrf rejected path=%s origin=%r origin_ok=%s session_token=%s token_match=%s",
            request.url.path,
            request.headers.get("origin", "")[:255],
            origin_ok,
            bool(expected),
            token_ok,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="요청 검증에 실패했습니다. 페이지를 새로고침해 다시 시도해 주세요.",
        )


def render(
    request: Request,
    template_name: str,
    *,
    status_code: int = 200,
    notice: str | None = None,
    error: str | None = None,
    **context: Any,
) -> Response:
    notice_code = request.query_params.get("notice", "")
    common = {
        "user": getattr(request.state, "user", None),
        "csrf_token": getattr(request.state, "session", {}).get("csrf_token", ""),
        "notice": notice or NOTICE_MESSAGES.get(notice_code),
        "error": error,
        "response_status": status_code,
        "is_error_page": template_name == "error.html",
    }
    common.update(context)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=template_name,
        context=common,
        status_code=status_code,
    )


def safe_next(value: str | None, fallback: str = "/") -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return fallback
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or "\\" in value:
        return fallback
    return value
