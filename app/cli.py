"""데이터베이스 초기화, 관리자 생성, 선택적 데모 데이터 CLI."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import UTC, datetime

from PIL import Image, ImageDraw

from app.config import Settings
from app.db import connect, init_database
from app.repository import ConflictError, create_product, create_user, get_user_by_username
from app.security import hash_password, validate_password, validate_username


def _display_name(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not 2 <= len(cleaned) <= 30:
        raise ValueError("표시 이름은 2~30자로 입력해 주세요.")
    return cleaned


def init_db(settings: Settings) -> None:
    init_database(settings)
    print(f"데이터베이스 준비 완료: {settings.database_path}")


def create_admin(settings: Settings) -> None:
    init_database(settings)
    username = validate_username(input("관리자 아이디: "))
    display_name = _display_name(input("표시 이름: "))
    password = getpass.getpass("비밀번호(12자 이상): ")
    validate_password(password)
    if password != getpass.getpass("비밀번호 확인: "):
        raise ValueError("비밀번호 확인이 일치하지 않습니다.")
    with connect(settings) as conn:
        create_user(
            conn,
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            role="admin",
            initial_balance=0,
        )
    print(f"관리자 생성 완료: {username}")


def _demo_image(settings: Settings, name: str, color: str, label: str) -> str:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_hex(20)}.jpg"
    image = Image.new("RGB", (960, 720), color)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((180, 180, 780, 540), radius=48, fill="#ffffff")
    draw.text((420, 340), label, fill="#17211b", anchor="mm")
    image.save(settings.upload_dir / filename, "JPEG", quality=88, optimize=True)
    return filename


def seed_demo(settings: Settings) -> None:
    init_database(settings)
    password = secrets.token_urlsafe(18)
    credentials: list[str] = []
    with connect(settings) as conn:
        users = []
        for username, display_name in (
            ("alice", "앨리스"),
            ("bob", "밥 이웃"),
            ("charlie", "찰리"),
        ):
            user = get_user_by_username(conn, username)
            if user is None:
                user = create_user(
                    conn,
                    username=username,
                    display_name=display_name,
                    password_hash=hash_password(password),
                )
                credentials.append(username)
            users.append(user)
        product_count = int(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])
        if product_count == 0:
            samples = (
                (
                    users[0],
                    "기계식 키보드",
                    "조용한 적축 키보드입니다. 사용감이 적고 구성품이 모두 있습니다.",
                    45_000,
                    "#dff5e7",
                    "KEYBOARD",
                ),
                (
                    users[1],
                    "휴대용 모니터",
                    "15인치 USB-C 휴대용 모니터입니다. 직거래 시 동작을 확인해 드립니다.",
                    120_000,
                    "#e6ecff",
                    "MONITOR",
                ),
                (
                    users[2],
                    "보안 입문서 세트",
                    "웹 보안과 네트워크 기초 서적 세 권을 한 번에 판매합니다.",
                    30_000,
                    "#fff0d9",
                    "BOOKS",
                ),
            )
            for owner, name, description, price, color, label in samples:
                image_filename = _demo_image(settings, name, color, label)
                create_product(
                    conn,
                    seller_id=int(owner["id"]),
                    name=name,
                    description=description,
                    price=price,
                    image_filename=image_filename,
                )
    if credentials:
        print("데모 계정 생성 완료(최초 1회만 표시):")
        print(f"  아이디: {', '.join(credentials)}")
        print(f"  공통 임시 비밀번호: {password}")
        print("실제 운영 데이터에는 seed-demo를 사용하지 마세요.")
    else:
        print("기존 데모 계정이 있어 상품만 확인했습니다. 비밀번호는 변경하지 않았습니다.")


def cleanup_sessions(settings: Settings) -> None:
    with connect(settings) as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE expires_at <= ?", (datetime.now(UTC).isoformat(),)
        )
        conn.commit()
    print(f"만료 세션 {cursor.rowcount}개를 삭제했습니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="바로마켓 관리 CLI")
    parser.add_argument(
        "command", choices=("init-db", "create-admin", "seed-demo", "cleanup-sessions")
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    try:
        if args.command == "init-db":
            init_db(settings)
        elif args.command == "create-admin":
            create_admin(settings)
        elif args.command == "seed-demo":
            seed_demo(settings)
        else:
            cleanup_sessions(settings)
    except (ValueError, ConflictError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
