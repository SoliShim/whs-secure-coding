"""가상 원화 지갑과 원자적 사용자 간 송금."""

from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.db import connect
from app.repository import (
    ForbiddenOperationError,
    InsufficientBalanceError,
    NotFoundError,
    get_user_by_id,
    transfer_funds,
    wallet_history,
)
from app.security import new_token, validate_username, verify_password
from app.web import client_ip_hash, render, require_user, verify_csrf

router = APIRouter()
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,200}$")


def _present_transfers(transfers: list[dict], user_id: int) -> list[dict]:
    presented = []
    for transfer in transfers:
        item = dict(transfer)
        outgoing = int(item["sender_id"]) == user_id
        item["direction"] = "out" if outgoing else "in"
        item["counterparty_username"] = (
            item["recipient_username"] if outgoing else item["sender_username"]
        )
        item["counterparty_display_name"] = item["counterparty_username"]
        item["created_label"] = str(item.get("created_at", "")).replace("T", " ")[:16]
        presented.append(item)
    return presented


@router.get("/wallet")
def wallet_page(request: Request, recipient: str = "", amount: str = ""):
    user = require_user(request)
    with connect(request.app.state.settings) as conn:
        account = get_user_by_id(conn, int(user["id"]))
        history = _present_transfers(wallet_history(conn, int(user["id"])), int(user["id"]))
    return render(
        request,
        "wallet.html",
        account=account,
        balance=int(account["wallet_balance"]),
        transfers=history,
        idempotency_key=new_token(),
        recipient=recipient,
        amount=amount,
        form={},
    )


@router.post("/wallet/transfers")
def send_money(
    request: Request,
    recipient: str = Form(...),
    amount: str = Form(...),
    memo: str = Form(""),
    current_password: str = Form(...),
    idempotency_key: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_user(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    try:
        recipient_username = validate_username(recipient)
        clean_amount = int(amount)
        clean_memo = memo.strip()
        if len(clean_memo) > 100 or "\x00" in clean_memo:
            raise ValueError("메모는 100자 이하로 입력해 주세요.")
        if not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("송금 요청 식별자가 올바르지 않습니다. 페이지를 새로고침해 주세요.")
    except (TypeError, ValueError) as exc:
        with connect(settings) as conn:
            account = get_user_by_id(conn, int(user["id"]))
            history = _present_transfers(wallet_history(conn, int(user["id"])), int(user["id"]))
        return render(
            request,
            "wallet.html",
            status_code=400,
            error=str(exc) if str(exc) else "송금 정보를 확인해 주세요.",
            account=account,
            balance=int(account["wallet_balance"]) if account else 0,
            transfers=history,
            idempotency_key=new_token(),
            form={"recipient": recipient, "amount": amount, "memo": memo},
        )

    ip_hash = client_ip_hash(request, settings)
    if not request.app.state.rate_limiter.allow(f"transfer:{user['id']}:{ip_hash}", 10, 600):
        return render(
            request,
            "error.html",
            status_code=429,
            title="요청 제한",
            error="송금 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
        )

    with connect(settings) as conn:
        account = get_user_by_id(conn, int(user["id"]))
    if account is None or not verify_password(account["password_hash"], current_password):
        with connect(settings) as conn:
            history = _present_transfers(wallet_history(conn, int(user["id"])), int(user["id"]))
        return render(
            request,
            "wallet.html",
            status_code=400,
            error="현재 비밀번호가 올바르지 않습니다.",
            account=account,
            balance=int(account["wallet_balance"]) if account else 0,
            transfers=history,
            idempotency_key=idempotency_key,
            form={"recipient": recipient, "amount": amount, "memo": memo},
        )

    try:
        _, duplicate = transfer_funds(
            settings,
            sender_id=int(user["id"]),
            recipient_username=recipient_username,
            amount=clean_amount,
            memo=clean_memo,
            idempotency_key=idempotency_key,
            ip_hash=ip_hash,
        )
        notice = "duplicate_transfer" if duplicate else "transferred"
        return RedirectResponse(f"/wallet?notice={notice}", status_code=303)
    except (NotFoundError, ForbiddenOperationError, InsufficientBalanceError) as exc:
        with connect(settings) as conn:
            account = get_user_by_id(conn, int(user["id"]))
            history = _present_transfers(wallet_history(conn, int(user["id"])), int(user["id"]))
        return render(
            request,
            "wallet.html",
            status_code=400,
            error=str(exc),
            account=account,
            balance=int(account["wallet_balance"]) if account else 0,
            transfers=history,
            idempotency_key=idempotency_key,
            form={"recipient": recipient, "amount": amount, "memo": memo},
        )
