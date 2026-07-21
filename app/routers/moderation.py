"""사용자·상품 신고와 자동 임시 격리."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.repository import (
    ConflictError,
    ForbiddenOperationError,
    NotFoundError,
    create_report,
)
from app.web import client_ip_hash, render, require_user, verify_csrf

router = APIRouter()


@router.post("/reports")
async def report_target(
    request: Request,
    target_type: str = Form(...),
    target_id: int = Form(...),
    reason: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_user(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    clean_reason = reason.strip()
    if not 10 <= len(clean_reason) <= 500 or "\x00" in clean_reason:
        return render(
            request,
            "error.html",
            status_code=400,
            title="신고 입력 오류",
            error="신고 사유는 10~500자로 구체적으로 입력해 주세요.",
        )
    ip_hash = client_ip_hash(request, settings)
    if not request.app.state.rate_limiter.allow(f"report:{user['id']}:{ip_hash}", 10, 3600):
        return render(
            request,
            "error.html",
            status_code=429,
            title="요청 제한",
            error="신고 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
        )
    try:
        _, auto_action = await run_in_threadpool(
            create_report,
            settings,
            reporter_id=int(user["id"]),
            target_type=target_type,
            target_id=target_id,
            reason=clean_reason,
        )
        if auto_action and target_type == "user":
            await request.app.state.chat_manager.disconnect_user(target_id)
        destination = f"/products/{target_id}" if target_type == "product" else "/"
        notice = "quarantined" if auto_action else "reported"
        return RedirectResponse(f"{destination}?notice={notice}", status_code=303)
    except ConflictError as exc:
        return render(request, "error.html", status_code=409, title="중복 신고", error=str(exc))
    except NotFoundError as exc:
        return render(request, "error.html", status_code=404, title="대상 없음", error=str(exc))
    except ForbiddenOperationError as exc:
        return render(request, "error.html", status_code=403, title="신고 불가", error=str(exc))
