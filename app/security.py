"""Small, audited security primitives used throughout the application."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from urllib.parse import urlsplit

from argon2 import PasswordHasher, Type
from argon2.exceptions import HashingError, InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings

# OWASP Password Storage Cheat Sheet minimum Argon2id profile:
# 19 MiB memory, two iterations, one lane.  The encoded hash carries these
# parameters, allowing needs_rehash() to support future upgrades.
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

_USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,29}$", re.ASCII)
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,200}$", re.ASCII)
_COMMON_PASSWORDS = {
    "123456789012",
    "qwertyuiop12",
    "password1234",
    "letmein123456",
    "adminadmin123",
}


def validate_username(value: str) -> str:
    """Normalize and validate the ASCII identifier used for login and lookup."""

    if not isinstance(value, str):
        raise ValueError("아이디 형식이 올바르지 않습니다.")
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    if not _USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "아이디는 영문 소문자로 시작하는 3~30자의 영문, 숫자, _ 조합이어야 합니다."
        )
    return normalized


def validate_password(password: str) -> None:
    """Apply length and basic compromised/default-password defenses.

    Composition rules are deliberately avoided so long passphrases remain
    usable.  The 128-character maximum bounds password-hashing resource use.
    """

    if not isinstance(password, str):
        raise ValueError("비밀번호 형식이 올바르지 않습니다.")
    if not 12 <= len(password) <= 128:
        raise ValueError("비밀번호는 12~128자로 입력해 주세요.")
    if len(password.encode("utf-8")) > 512:
        raise ValueError("비밀번호가 너무 깁니다.")
    if password.isspace() or "\x00" in password:
        raise ValueError("비밀번호에 허용되지 않는 문자가 있습니다.")
    if password.casefold() in _COMMON_PASSWORDS:
        raise ValueError("너무 흔한 비밀번호는 사용할 수 없습니다.")


def hash_password(password: str) -> str:
    """Hash a validated password with Argon2id and a random salt."""

    validate_password(password)
    try:
        return _PASSWORD_HASHER.hash(password)
    except HashingError as exc:
        raise RuntimeError("비밀번호 해시를 생성하지 못했습니다.") from exc


def verify_password(password_hash: str, password: str) -> bool:
    """Verify ``password`` against an encoded hash without surfacing details."""

    if not isinstance(password_hash, str) or not isinstance(password, str):
        return False
    if len(password) > 128 or len(password.encode("utf-8")) > 512:
        return False
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return true for invalid or obsolete password hash parameters."""

    if not isinstance(password_hash, str):
        return True
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except (InvalidHashError, TypeError):
        return True


def new_token() -> str:
    """Return a URL-safe opaque token containing 256 bits of entropy."""

    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the non-reversible lowercase SHA-256 session lookup key."""

    if not isinstance(token, str) or not token or len(token) > 200:
        raise ValueError("토큰 형식이 올바르지 않습니다.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_matches(expected: str | None, received: str | None) -> bool:
    """Compare CSRF tokens in constant time."""

    if not isinstance(expected, str) or not isinstance(received, str):
        return False
    if len(expected) > 200 or len(received) > 200:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), received.encode("utf-8"))


def validate_short_text(
    value: str,
    max_length: int = 500,
    *,
    min_length: int = 1,
    field_name: str = "텍스트",
    allow_newlines: bool = True,
) -> str:
    """Normalize bounded user text while leaving output escaping to Jinja2."""

    if not isinstance(value, str) or not 0 <= min_length <= max_length <= 10_000:
        raise ValueError(f"{field_name} 형식이 올바르지 않습니다.")
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = normalized.strip()
    for character in cleaned:
        category = unicodedata.category(character)
        if category == "Cc" and character not in {"\n", "\t"}:
            raise ValueError(f"{field_name}에 허용되지 않는 제어 문자가 있습니다.")
        if category == "Cf":
            raise ValueError(f"{field_name}에 허용되지 않는 숨은 문자가 있습니다.")
        if not allow_newlines and character in {"\n", "\t"}:
            raise ValueError(f"{field_name}에는 줄바꿈을 사용할 수 없습니다.")
    if not min_length <= len(cleaned) <= max_length:
        raise ValueError(f"{field_name}은 {min_length}~{max_length}자로 입력해 주세요.")
    return cleaned


def validate_idempotency_key(value: str) -> str:
    """Validate the browser-generated opaque key used to deduplicate transfers."""

    if not isinstance(value, str) or not _IDEMPOTENCY_PATTERN.fullmatch(value):
        raise ValueError("요청 식별자 형식이 올바르지 않습니다.")
    return value


def ip_fingerprint(ip_address: str, app_secret: str) -> str:
    """Pseudonymize an IP address for rate-limit and audit correlation."""

    if not isinstance(ip_address, str) or not isinstance(app_secret, str) or not app_secret:
        raise ValueError("IP 지문 입력이 올바르지 않습니다.")
    canonical = ip_address.strip().lower()[:255]
    return hmac.new(
        app_secret.encode("utf-8"),
        b"ip-fingerprint-v1\x00" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def security_headers(settings: Settings) -> dict[str, str]:
    """Build strict response headers for the server-rendered same-origin UI."""

    connect_sources = {"'self'"}
    for origin in settings.allowed_origins:
        parsed = urlsplit(origin)
        connect_sources.add(origin)
        websocket_scheme = "wss" if parsed.scheme == "https" else "ws"
        connect_sources.add(f"{websocket_scheme}://{parsed.netloc}")

    headers = {
        "Content-Security-Policy": "; ".join(
            (
                "default-src 'self'",
                "base-uri 'self'",
                "object-src 'none'",
                "frame-ancestors 'none'",
                "form-action 'self'",
                "script-src 'self'",
                "style-src 'self'",
                "img-src 'self' data:",
                f"connect-src {' '.join(sorted(connect_sources))}",
            )
        ),
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "0",
    }
    if settings.secure_cookies:
        headers["Content-Security-Policy"] += "; upgrade-insecure-requests"
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers
