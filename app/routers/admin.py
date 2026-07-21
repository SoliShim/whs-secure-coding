"""관리자 RBAC, 신고 검토, 사용자·상품 관리."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.db import connect
from app.repository import (
    ConflictError,
    ForbiddenOperationError,
    NotFoundError,
    admin_snapshot,
    resolve_report,
    set_product_visibility,
    set_user_status,
)
from app.web import render, require_admin, verify_csrf

router = APIRouter()


def _change_user_status(settings, actor_id: int, target_id: int, account_status: str) -> None:
    with connect(settings) as conn:
        set_user_status(
            conn,
            actor_id=actor_id,
            target_id=target_id,
            status=account_status,
        )


@router.get("/admin")
def admin_dashboard(request: Request):
    require_admin(request)
    with connect(request.app.state.settings) as conn:
        snapshot = admin_snapshot(conn)
    counts = snapshot["counts"]
    stats = {
        "users": counts["users"],
        "products": counts["products"],
        "open_reports": counts["pending_reports"],
        "today_transfers": counts["transfers"],
    }
    return render(request, "admin/dashboard.html", stats=stats, **snapshot)


@router.post("/admin/users/{user_id}/status")
async def admin_user_status(
    request: Request,
    user_id: int,
    status: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    if status not in {"active", "suspended"}:
        return render(
            request,
            "error.html",
            status_code=400,
            title="입력 오류",
            error="허용되지 않은 계정 상태입니다.",
        )
    try:
        await run_in_threadpool(
            _change_user_status,
            settings,
            int(admin["id"]),
            user_id,
            status,
        )
        if status != "active":
            await request.app.state.chat_manager.disconnect_user(user_id)
        return RedirectResponse("/admin?notice=admin_saved", status_code=303)
    except NotFoundError as exc:
        return render(request, "error.html", status_code=404, title="대상 없음", error=str(exc))
    except ForbiddenOperationError as exc:
        return render(request, "error.html", status_code=403, title="조치 불가", error=str(exc))


@router.post("/admin/products/{product_id}/visibility")
def admin_product_visibility(
    request: Request,
    product_id: int,
    visible: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    if visible not in {"true", "false"}:
        return render(
            request,
            "error.html",
            status_code=400,
            title="입력 오류",
            error="허용되지 않은 공개 상태입니다.",
        )
    try:
        with connect(settings) as conn:
            set_product_visibility(
                conn,
                actor_id=int(admin["id"]),
                product_id=product_id,
                visible=visible == "true",
            )
        return RedirectResponse("/admin?notice=admin_saved", status_code=303)
    except NotFoundError as exc:
        return render(request, "error.html", status_code=404, title="대상 없음", error=str(exc))


@router.post("/admin/reports/{report_id}/resolve")
def admin_report_resolve(
    request: Request,
    report_id: int,
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    if action not in {"dismiss", "confirm"}:
        return render(
            request,
            "error.html",
            status_code=400,
            title="입력 오류",
            error="허용되지 않은 신고 처리 방식입니다.",
        )
    try:
        with connect(settings) as conn:
            resolve_report(
                conn,
                actor_id=int(admin["id"]),
                report_id=report_id,
                action=action,
                report_threshold=settings.report_threshold,
            )
        return RedirectResponse("/admin?notice=admin_saved", status_code=303)
    except NotFoundError as exc:
        return render(request, "error.html", status_code=404, title="신고 없음", error=str(exc))
    except ConflictError as exc:
        return render(request, "error.html", status_code=409, title="처리 완료", error=str(exc))
