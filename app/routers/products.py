"""상품 등록·조회·검색·소유자 관리 라우터."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.db import connect
from app.images import ImageValidationError, remove_product_image, save_product_image
from app.repository import (
    ForbiddenOperationError,
    NotFoundError,
    count_products,
    create_product,
    delete_product,
    get_product,
    list_products,
    list_user_products,
    update_product,
)
from app.web import client_ip_hash, render, require_user, verify_csrf

router = APIRouter()
ALLOWED_PRODUCT_STATUS = {"available", "reserved", "sold"}


def present_product(product: dict | None) -> dict | None:
    if product is None:
        return None
    presented = dict(product)
    presented["image_url"] = f"/uploads/{product['image_filename']}"
    presented["created_label"] = str(product.get("created_at", "")).replace("T", " ")[:16]
    return presented


def _product_fields(name: str, description: str, price: str, status: str = "available"):
    clean_name = " ".join(name.strip().split())
    clean_description = description.strip()
    if not 2 <= len(clean_name) <= 80 or "\x00" in clean_name:
        raise ValueError("상품명은 2~80자로 입력해 주세요.")
    if not 10 <= len(clean_description) <= 2_000 or "\x00" in clean_description:
        raise ValueError("상품 설명은 10~2,000자로 입력해 주세요.")
    try:
        clean_price = int(price)
    except (TypeError, ValueError) as exc:
        raise ValueError("가격은 숫자로 입력해 주세요.") from exc
    if not 1 <= clean_price <= 100_000_000:
        raise ValueError("가격은 1원 이상 1억원 이하로 입력해 주세요.")
    if status not in ALLOWED_PRODUCT_STATUS:
        raise ValueError("허용되지 않은 판매 상태입니다.")
    return clean_name, clean_description, clean_price, status


def _active_product_count(settings, seller_id: int) -> int:
    with connect(settings) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM products WHERE seller_id = ? AND status != 'deleted'",
                (seller_id,),
            ).fetchone()[0]
        )


def _create_product_record(settings, **values):
    with connect(settings) as conn:
        return create_product(conn, **values)


def _product_record(settings, product_id: int):
    with connect(settings) as conn:
        return get_product(conn, product_id)


def _update_product_record(settings, **values):
    with connect(settings) as conn:
        return update_product(conn, **values)


@router.get("/")
def home(request: Request, q: str = "", page: int = 1):
    settings = request.app.state.settings
    ip_hash = client_ip_hash(request, settings)
    if not request.app.state.rate_limiter.allow(f"search:{ip_hash}", 120, 60):
        return render(
            request,
            "error.html",
            status_code=429,
            title="요청 제한",
            error="검색 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
        )
    clean_query = q.strip()
    query_error = None
    if len(clean_query) > 80 or "\x00" in clean_query:
        query_error = "검색어는 80자 이하로 입력해 주세요."
        clean_query = ""
    page = min(max(page, 1), 1_000)
    page_size = 12
    with connect(settings) as conn:
        total = count_products(conn, clean_query)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        products = list_products(conn, clean_query, limit=page_size, offset=(page - 1) * page_size)
    products = [present_product(product) for product in products]
    return render(
        request,
        "home.html",
        products=products,
        query=clean_query,
        error=query_error,
        pagination={
            "page": page,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
            "prev_num": page - 1,
            "next_num": page + 1,
        },
    )


@router.get("/products/new")
def new_product_page(request: Request):
    require_user(request)
    return render(request, "products/form.html", product=None, form={}, mode="create")


@router.post("/products")
async def add_product(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    price: str = Form(...),
    csrf_token: str = Form(...),
    image: Annotated[UploadFile, File()] = ...,
):
    user = require_user(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    form = {"name": name, "description": description, "price": price, "status": "available"}
    resource_key = f"product-image:{user['id']}:{client_ip_hash(request, settings)}"
    if not request.app.state.rate_limiter.allow(resource_key, 12, 3600):
        return render(
            request,
            "products/form.html",
            status_code=429,
            error="상품 이미지 처리 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            product=None,
            form=form,
            mode="create",
        )
    active_count = await run_in_threadpool(_active_product_count, settings, int(user["id"]))
    if active_count >= 100:
        return render(
            request,
            "products/form.html",
            status_code=409,
            error="사용자당 활성 상품은 최대 100개입니다.",
            product=None,
            form=form,
            mode="create",
        )
    saved_filename: str | None = None
    try:
        clean_name, clean_description, clean_price, _ = _product_fields(name, description, price)
        saved_filename = await save_product_image(
            image, settings.upload_dir, request.app.state.image_processing_limiter
        )
        product = await run_in_threadpool(
            _create_product_record,
            settings,
            seller_id=int(user["id"]),
            name=clean_name,
            description=clean_description,
            price=clean_price,
            image_filename=saved_filename,
        )
        return RedirectResponse(
            f"/products/{product['id']}?notice=product_created", status_code=303
        )
    except (ValueError, ImageValidationError) as exc:
        if saved_filename:
            await run_in_threadpool(remove_product_image, saved_filename, settings.upload_dir)
        return render(
            request,
            "products/form.html",
            status_code=400,
            error=str(exc),
            product=None,
            form=form,
            mode="create",
        )
    except Exception:
        if saved_filename:
            await run_in_threadpool(remove_product_image, saved_filename, settings.upload_dir)
        raise


@router.get("/products/{product_id}")
def product_detail(request: Request, product_id: int):
    with connect(request.app.state.settings) as conn:
        product = present_product(get_product(conn, product_id))
    user = request.state.user
    can_manage = bool(
        user
        and product
        and (int(product["seller_id"]) == int(user["id"]) or user["role"] == "admin")
    )
    if (
        product is None
        or product["status"] == "deleted"
        or (product["is_hidden"] and not can_manage)
    ):
        return render(
            request,
            "error.html",
            status_code=404,
            title="상품 없음",
            error="상품을 찾을 수 없습니다.",
        )
    return render(
        request,
        "products/detail.html",
        product=product,
        can_manage=can_manage,
        is_owner=bool(user and int(product["seller_id"]) == int(user["id"])),
    )


@router.get("/products/{product_id}/edit")
def edit_product_page(request: Request, product_id: int):
    user = require_user(request)
    with connect(request.app.state.settings) as conn:
        product = present_product(get_product(conn, product_id))
    if product is None or product["status"] == "deleted":
        return render(
            request,
            "error.html",
            status_code=404,
            title="상품 없음",
            error="상품을 찾을 수 없습니다.",
        )
    if int(product["seller_id"]) != int(user["id"]) and user["role"] != "admin":
        return render(
            request,
            "error.html",
            status_code=403,
            title="접근 거부",
            error="이 상품을 수정할 권한이 없습니다.",
        )
    return render(request, "products/form.html", product=product, form=product, mode="edit")


@router.post("/products/{product_id}/edit")
async def save_product(
    request: Request,
    product_id: int,
    name: str = Form(...),
    description: str = Form(...),
    price: str = Form(...),
    status: str = Form(...),
    csrf_token: str = Form(...),
    image: Annotated[UploadFile | None, File()] = None,
):
    user = require_user(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    form = {"name": name, "description": description, "price": price, "status": status}
    saved_filename: str | None = None
    previous = present_product(await run_in_threadpool(_product_record, settings, product_id))
    try:
        clean_name, clean_description, clean_price, clean_status = _product_fields(
            name, description, price, status
        )
        if image and image.filename:
            resource_key = f"product-image:{user['id']}:{client_ip_hash(request, settings)}"
            if not request.app.state.rate_limiter.allow(resource_key, 20, 3600):
                raise ImageValidationError(
                    "상품 이미지 처리 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
                )
            saved_filename = await save_product_image(
                image, settings.upload_dir, request.app.state.image_processing_limiter
            )
        product = await run_in_threadpool(
            _update_product_record,
            settings,
            product_id=product_id,
            actor_id=int(user["id"]),
            actor_role=user["role"],
            name=clean_name,
            description=clean_description,
            price=clean_price,
            status=clean_status,
            image_filename=saved_filename,
        )
        if saved_filename and previous:
            await run_in_threadpool(
                remove_product_image,
                previous.get("image_filename"),
                settings.upload_dir,
            )
        return RedirectResponse(
            f"/products/{product['id']}?notice=product_updated", status_code=303
        )
    except (ValueError, ImageValidationError, ForbiddenOperationError, NotFoundError) as exc:
        if saved_filename:
            await run_in_threadpool(remove_product_image, saved_filename, settings.upload_dir)
        return render(
            request,
            "products/form.html",
            status_code=400 if not isinstance(exc, ForbiddenOperationError) else 403,
            error=str(exc),
            product=previous,
            form=form,
            mode="edit",
        )
    except Exception:
        if saved_filename:
            remove_product_image(saved_filename, settings.upload_dir)
        raise


@router.post("/products/{product_id}/delete")
def remove_product(request: Request, product_id: int, csrf_token: str = Form(...)):
    user = require_user(request)
    settings = request.app.state.settings
    verify_csrf(request, csrf_token, settings)
    try:
        with connect(settings) as conn:
            filename = delete_product(conn, product_id, int(user["id"]), user["role"])
        remove_product_image(filename, settings.upload_dir)
        return RedirectResponse("/my/products?notice=product_deleted", status_code=303)
    except NotFoundError as exc:
        return render(request, "error.html", status_code=404, title="상품 없음", error=str(exc))
    except ForbiddenOperationError as exc:
        return render(request, "error.html", status_code=403, title="접근 거부", error=str(exc))


@router.get("/my/products")
def my_products(request: Request):
    user = require_user(request)
    with connect(request.app.state.settings) as conn:
        products = list_user_products(conn, int(user["id"]))
    products = [present_product(product) for product in products]
    summary = {
        "total": len(products),
        "available": sum(item["status"] == "available" for item in products),
        "reserved": sum(item["status"] == "reserved" for item in products),
        "sold": sum(item["status"] == "sold" for item in products),
    }
    return render(request, "products/mine.html", products=products, summary=summary)
