"""Application configuration loaded from environment variables.

The project intentionally avoids a settings dependency.  A small, non-expanding
``.env`` reader is used for local development and the real process environment
always wins.  Production configuration is fail-closed: an explicit high-entropy
secret, HTTPS cookies, hosts, and origins are required.
"""

from __future__ import annotations

import os
import secrets
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = APP_ROOT / ".env"


class ConfigurationError(RuntimeError):
    """Raised when settings are unsafe or internally inconsistent."""


def _read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE pairs without interpolation or code execution."""

    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"{path}:{line_number}: KEY=VALUE 형식이 아닙니다.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "A").isalnum():
            raise ConfigurationError(f"{path}:{line_number}: 환경 변수 이름이 올바르지 않습니다.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name}은 true 또는 false여야 합니다.")


def _parse_int(
    value: str,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise ConfigurationError(f"{name}은 정수여야 합니다.") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name}은 {minimum}~{maximum} 범위여야 합니다.")
    return parsed


def _csv(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(item.strip().rstrip("/") for item in value.split(",") if item.strip())
    )


def _resolve_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = APP_ROOT / candidate
    return candidate.resolve()


def _validate_host(host: str) -> None:
    if not host or any(character in host for character in "/\\\r\n\t"):
        raise ConfigurationError(f"허용 호스트가 올바르지 않습니다: {host!r}")
    if "://" in host:
        raise ConfigurationError("ALLOWED_HOSTS에는 스킴을 넣지 마세요.")


def _validate_origin(origin: str, *, production: bool) -> None:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"허용 오리진이 올바르지 않습니다: {origin!r}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        raise ConfigurationError(
            f"오리진에는 경로, 인증정보, 질의문을 넣을 수 없습니다: {origin!r}"
        )
    if production and parsed.scheme != "https":
        raise ConfigurationError("production의 ALLOWED_ORIGINS는 모두 https여야 합니다.")


def _weak_secret(secret: str) -> bool:
    lowered = secret.lower()
    markers = (
        "change-me",
        "change-this",
        "changeme",
        "replace-me",
        "example",
        "dev-secret",
    )
    return (
        len(secret.encode("utf-8")) < 32
        or len(set(secret)) < 8
        or any(marker in lowered for marker in markers)
    )


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated immutable application settings.

    Direct construction is supported for isolated tests.  Normal application
    startup should use :meth:`from_env`.
    """

    env: str = "development"
    database_path: Path = APP_ROOT / "app" / "data" / "market.sqlite3"
    upload_dir: Path = APP_ROOT / "app" / "data" / "uploads"
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    allowed_origins: tuple[str, ...] = (
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://testserver",
    )
    secure_cookies: bool = False
    session_ttl_seconds: int = 8 * 60 * 60
    session_idle_seconds: int = 30 * 60
    report_threshold: int = 3
    app_secret: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    debug: bool = False

    def __post_init__(self) -> None:
        normalized_env = self.env.strip().lower()
        if normalized_env not in {"development", "test", "production"}:
            raise ConfigurationError("APP_ENV는 development, test, production 중 하나여야 합니다.")
        object.__setattr__(self, "env", normalized_env)
        object.__setattr__(self, "database_path", Path(self.database_path).expanduser().resolve())
        object.__setattr__(self, "upload_dir", Path(self.upload_dir).expanduser().resolve())

        if not self.allowed_hosts:
            raise ConfigurationError("ALLOWED_HOSTS를 한 개 이상 지정해야 합니다.")
        for host in self.allowed_hosts:
            _validate_host(host)
        if not self.allowed_origins:
            raise ConfigurationError("ALLOWED_ORIGINS를 한 개 이상 지정해야 합니다.")
        for origin in self.allowed_origins:
            _validate_origin(origin, production=self.is_production)

        if not 300 <= self.session_ttl_seconds <= 7 * 24 * 60 * 60:
            raise ConfigurationError("SESSION_TTL_SECONDS는 300초~7일 범위여야 합니다.")
        if not 60 <= self.session_idle_seconds <= self.session_ttl_seconds:
            raise ConfigurationError(
                "SESSION_IDLE_SECONDS는 60초 이상이며 전체 만료보다 짧아야 합니다."
            )
        if not 2 <= self.report_threshold <= 20:
            raise ConfigurationError("REPORT_THRESHOLD는 2~20 범위여야 합니다.")

        if self.is_production:
            if _weak_secret(self.app_secret):
                raise ConfigurationError(
                    "production의 APP_SECRET은 예측 불가능한 32바이트 이상 값이어야 합니다."
                )
            if not self.secure_cookies:
                raise ConfigurationError("production에서는 SECURE_COOKIES=true가 필수입니다.")
            if self.debug:
                raise ConfigurationError("production에서는 DEBUG=false가 필수입니다.")
            if "*" in self.allowed_hosts:
                raise ConfigurationError("production에서는 ALLOWED_HOSTS='*'를 사용할 수 없습니다.")

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        """Build settings from ``.env`` and the process environment.

        The process environment takes precedence.  Missing development secrets
        are generated per process so no reusable secret is committed to Git.
        """

        file_values = _read_env_file(env_file or DEFAULT_ENV_FILE)
        values = {**file_values, **os.environ}
        app_env = values.get("APP_ENV", "development").strip().lower()
        production = app_env == "production"

        app_secret = values.get("APP_SECRET", "")
        if not app_secret:
            if production:
                raise ConfigurationError("production에서는 APP_SECRET을 반드시 지정해야 합니다.")
            app_secret = secrets.token_urlsafe(48)
            warnings.warn(
                "APP_SECRET이 없어 프로세스 수명 동안만 유효한 개발용 키를 생성했습니다.",
                RuntimeWarning,
                stacklevel=2,
            )
        if production and "ALLOWED_HOSTS" not in values:
            raise ConfigurationError("production에서는 ALLOWED_HOSTS를 명시적으로 지정해야 합니다.")
        if production and "ALLOWED_ORIGINS" not in values:
            raise ConfigurationError(
                "production에서는 ALLOWED_ORIGINS를 명시적으로 지정해야 합니다."
            )

        secure_default = "true" if production else "false"
        debug_default = "false"
        database_path = _resolve_path(values.get("DATABASE_PATH", "app/data/market.sqlite3"))
        upload_dir = _resolve_path(values.get("UPLOAD_DIR", "app/data/uploads"))
        hosts = _csv(values.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"))
        origins = _csv(
            values.get(
                "ALLOWED_ORIGINS",
                "http://localhost:8000,http://127.0.0.1:8000,http://testserver",
            )
        )

        return cls(
            env=app_env,
            database_path=database_path,
            upload_dir=upload_dir,
            allowed_hosts=hosts,
            allowed_origins=origins,
            secure_cookies=_parse_bool(
                values.get("SECURE_COOKIES", secure_default), name="SECURE_COOKIES"
            ),
            session_ttl_seconds=_parse_int(
                values.get("SESSION_TTL_SECONDS", str(8 * 60 * 60)),
                name="SESSION_TTL_SECONDS",
                minimum=300,
                maximum=7 * 24 * 60 * 60,
            ),
            session_idle_seconds=_parse_int(
                values.get("SESSION_IDLE_SECONDS", str(30 * 60)),
                name="SESSION_IDLE_SECONDS",
                minimum=60,
                maximum=7 * 24 * 60 * 60,
            ),
            report_threshold=_parse_int(
                values.get("REPORT_THRESHOLD", "3"),
                name="REPORT_THRESHOLD",
                minimum=2,
                maximum=20,
            ),
            app_secret=app_secret,
            debug=_parse_bool(values.get("DEBUG", debug_default), name="DEBUG"),
        )
