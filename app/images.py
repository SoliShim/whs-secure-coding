"""상품 이미지 검증과 안전한 재인코딩."""

from __future__ import annotations

import io
import secrets
import warnings
from pathlib import Path

from anyio import CapacityLimiter
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_DIMENSION = 1_600
MAX_SOURCE_DIMENSION = 10_000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ImageValidationError(ValueError):
    """공개 가능한 이미지 검증 오류."""


def _validate_and_save(payload: bytes, upload_dir: Path) -> str:
    """CPU·메모리 집약적인 Pillow 처리를 worker thread에서 수행한다."""

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as probe:
                image_format = (probe.format or "").upper()
                if image_format not in ALLOWED_FORMATS:
                    raise ImageValidationError("JPEG, PNG, WebP 이미지만 사용할 수 있습니다.")
                width, height = probe.size
                if (
                    width <= 0
                    or height <= 0
                    or width * height > MAX_IMAGE_PIXELS
                    or max(width, height) > MAX_SOURCE_DIMENSION
                ):
                    raise ImageValidationError("이미지 해상도가 너무 큽니다.")
                probe.verify()

            with Image.open(io.BytesIO(payload)) as source:
                width, height = source.size
                if width * height > MAX_IMAGE_PIXELS or max(width, height) > MAX_SOURCE_DIMENSION:
                    raise ImageValidationError("이미지 해상도가 너무 큽니다.")
                source.load()
                image = ImageOps.exif_transpose(source)
                image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
                if image.mode != "RGB":
                    background = Image.new("RGB", image.size, "white")
                    if image.mode in {"RGBA", "LA"}:
                        background.paste(image, mask=image.getchannel("A"))
                    else:
                        background.paste(image.convert("RGB"))
                    image = background
                else:
                    image = image.copy()
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError("이미지 해상도가 너무 큽니다.") from exc
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise ImageValidationError("손상되었거나 지원하지 않는 이미지입니다.") from exc

    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_hex(20)}.jpg"
    destination = upload_dir / filename
    image.save(destination, format="JPEG", quality=86, optimize=True, progressive=True)
    return filename


async def save_product_image(upload: UploadFile, upload_dir: Path, limiter: CapacityLimiter) -> str:
    """업로드를 제한해 읽고, worker thread에서 안전한 JPEG로 재인코딩한다."""

    async with limiter:
        payload = await upload.read(MAX_UPLOAD_BYTES + 1)
        await upload.close()
        if not payload:
            raise ImageValidationError("상품 이미지를 선택해 주세요.")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ImageValidationError("이미지는 4MB 이하여야 합니다.")
        return await run_in_threadpool(_validate_and_save, payload, upload_dir)


def remove_product_image(filename: str | None, upload_dir: Path) -> None:
    """DB에서 이미 분리된, 서버가 생성한 이미지 이름만 삭제한다."""

    if not filename or len(filename) != 44 or not filename.endswith(".jpg"):
        return
    stem = filename[:-4]
    if any(character not in "0123456789abcdef" for character in stem):
        return
    candidate = (upload_dir / filename).resolve()
    if candidate.parent != upload_dir.resolve():
        return
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        # DB 변경은 성공했으므로 고아 파일 정리는 유지보수 작업에서 재시도한다.
        return
