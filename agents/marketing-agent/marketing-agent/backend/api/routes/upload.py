"""
Image upload routes — handles product image uploads.
"""
from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from config.settings import get_settings
from models.schemas import UploadResponse
from services.mongodb_service import save_product, get_session
from services.redis_service import cache_product_analysis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}


@router.post("", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    session_id: str = Form(...),
):
    """
    Upload a product image.
    Returns image_id that can be used in chat requests.
    """
    settings = get_settings()

    # Validate file type
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file.content_type} not allowed. Use: {', '.join(ALLOWED_TYPES)}",
        )

    # Read file content
    content = await file.read()

    # Validate size
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {settings.max_file_size_mb}MB",
        )

    # Validate session
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Generate unique filename
    ext = Path(file.filename or "image.jpg").suffix.lower() or ".jpg"
    image_id = f"{session_id}_{uuid.uuid4().hex}{ext}"

    # Save to disk
    upload_path = Path(settings.upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)
    file_path = upload_path / image_id

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    # Update session with image reference
    from services.mongodb_service import update_session
    await update_session(session_id, {"product_image_url": f"/uploads/{image_id}"})

    logger.info(f"Image uploaded: {image_id} ({len(content)} bytes) for session {session_id}")

    return UploadResponse(
        image_id=image_id,
        filename=file.filename or image_id,
        content_type=file.content_type,
        size=len(content),
        preview_url=f"/uploads/{image_id}",
    )


@router.get("/{image_id}")
async def serve_image(image_id: str):
    """Serve an uploaded image file."""
    settings = get_settings()
    file_path = Path(settings.upload_dir) / image_id

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    # Determine media type
    suffix = file_path.suffix.lower()
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                   ".webp": "image/webp", ".gif": "image/gif"}
    media_type = media_types.get(suffix, "image/jpeg")

    return FileResponse(file_path, media_type=media_type)
