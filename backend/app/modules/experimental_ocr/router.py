"""Experimental OCR API Router (/api/experimental/receipts/*)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, Request, UploadFile, status

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.experimental_ocr.schemas import (
    ExperimentalHealthOut,
    ExperimentalJobOut,
    ExtractModeType,
)
from app.modules.experimental_ocr.service import (
    attach_ground_truth,
    get_experimental_health,
    get_experimental_job,
    run_experimental_extract,
)

router = APIRouter(prefix="/api/experimental/receipts", tags=["experimental-ocr"])


@router.get("/health", response_model=ExperimentalHealthOut)
async def experimental_health(request: Request) -> ExperimentalHealthOut:
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return get_experimental_health(settings)


@router.post("/extract", response_model=ExperimentalJobOut, status_code=status.HTTP_201_CREATED)
async def experimental_extract(
    request: Request,
    file: UploadFile = File(...),
    mode: ExtractModeType = Form(default="experimental"),
    ctx: HouseholdContext = Depends(get_household_context),
) -> ExperimentalJobOut:
    data = await file.read()
    if not data:
        raise AppError(
            status_code=422, code="EMPTY_FILE", message="Uploaded receipt file is empty."
        )
    if len(data) > 10 * 1024 * 1024:
        raise AppError(
            status_code=422, code="FILE_TOO_LARGE", message="Receipt file exceeds 10 MB limit."
        )

    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return await run_experimental_extract(
        settings=settings,
        household_id=str(ctx.household.id),
        filename=file.filename or "receipt.jpg",
        content_type=file.content_type or "image/jpeg",
        image_bytes=data,
        mode=mode,
    )


@router.get("/jobs/{job_id}", response_model=ExperimentalJobOut)
async def get_job(
    job_id: str,
    ctx: HouseholdContext = Depends(get_household_context),
) -> ExperimentalJobOut:
    job = get_experimental_job(str(ctx.household.id), job_id)
    if job is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Experimental OCR job not found.")
    return job


@router.post("/jobs/{job_id}/ground-truth", response_model=ExperimentalJobOut)
async def set_job_ground_truth(
    job_id: str,
    ground_truth: dict[str, Any] = Body(...),
    ctx: HouseholdContext = Depends(get_household_context),
) -> ExperimentalJobOut:
    job = attach_ground_truth(str(ctx.household.id), job_id, ground_truth)
    if job is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Experimental OCR job not found.")
    return job
