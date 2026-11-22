from datetime import date
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AppError
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.purchases.schemas import (
    ConfirmPurchaseRequest,
    ConfirmPurchaseResponse,
    CreatePurchaseRequest,
    FoodConsumptionOut,
    OcrJsonRequest,
    PurchaseListResponse,
    PurchaseOut,
    UpdatePurchaseRequest,
)
from app.modules.purchases.service import (
    confirm_purchase,
    create_manual_draft,
    create_multi_ocr_draft,
    create_ocr_draft,
    discard_draft,
    get_purchase,
    list_purchases,
    merge_ocr_into_draft,
    update_draft,
    update_meal_classification,
)

router = APIRouter(prefix="/purchases", tags=["purchases"])


@router.post("/ocr", response_model=PurchaseOut, status_code=status.HTTP_201_CREATED)
async def ocr_purchase_draft(
    request: Request,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOut:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_uploads = list(form.getlist("files")) + list(form.getlist("file"))
        uploads = [u for u in raw_uploads if hasattr(u, "read")]
        if not uploads:
            raise AppError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="Multipart request must include at least one 'file' or 'files' field.",
            )
        file_tuples: list[tuple[bytes, str | None, str | None]] = []
        for up in uploads:
            file_bytes = await up.read()
            file_tuples.append(
                (file_bytes, getattr(up, "filename", None), getattr(up, "content_type", None))
            )

        purchase_id_raw = form.get("purchase_id")
        if purchase_id_raw:
            target_purchase_id = UUID(str(purchase_id_raw))
            result: PurchaseOut | None = None
            for f_bytes, f_name, f_ct in file_tuples:
                result = await merge_ocr_into_draft(
                    db,
                    household_id=ctx.household.id,
                    purchase_id=target_purchase_id,
                    file_bytes=f_bytes,
                    filename=f_name,
                    content_type=f_ct,
                )
            assert result is not None
            return result

        merchant_raw = form.get("merchant_name")
        purchased_raw = form.get("purchased_on")
        purchased_on = date.fromisoformat(str(purchased_raw)) if purchased_raw else None
        if len(file_tuples) == 1:
            f_bytes, f_name, f_ct = file_tuples[0]
            return await create_ocr_draft(
                db,
                household_id=ctx.household.id,
                user_id=ctx.user.id,
                file_bytes=f_bytes,
                filename=f_name,
                content_type=f_ct,
                merchant_name_override=str(merchant_raw) if merchant_raw else None,
                purchased_on_override=purchased_on,
            )
        return await create_multi_ocr_draft(
            db,
            household_id=ctx.household.id,
            user_id=ctx.user.id,
            files=file_tuples,
            merchant_name_override=str(merchant_raw) if merchant_raw else None,
            purchased_on_override=purchased_on,
        )

    body = await request.json()
    payload = OcrJsonRequest.model_validate(body)
    return await create_ocr_draft(
        db,
        household_id=ctx.household.id,
        user_id=ctx.user.id,
        raw_text=payload.raw_text,
        merchant_name_override=payload.merchant_name,
        purchased_on_override=payload.purchased_on,
    )


@router.post("", response_model=PurchaseOut, status_code=status.HTTP_201_CREATED)
async def create_purchase(
    payload: CreatePurchaseRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOut:
    return await create_manual_draft(
        db,
        household_id=ctx.household.id,
        user_id=ctx.user.id,
        payload=payload,
    )


@router.get("", response_model=PurchaseListResponse)
async def get_purchases(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseListResponse:
    items, total = await list_purchases(
        db,
        ctx.household.id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return PurchaseListResponse(items=items, total=total)


@router.get("/drafts", response_model=PurchaseListResponse)
async def get_purchase_drafts(
    status_filter: str | None = Query(default="draft", alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseListResponse:
    items, total = await list_purchases(
        db,
        ctx.household.id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return PurchaseListResponse(items=items, total=total)


@router.post("/drafts", response_model=PurchaseOut, status_code=status.HTTP_201_CREATED)
async def create_purchase_draft(
    payload: CreatePurchaseRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOut:
    return await create_manual_draft(
        db,
        household_id=ctx.household.id,
        user_id=ctx.user.id,
        payload=payload,
    )


@router.get("/drafts/{purchase_id}", response_model=PurchaseOut)
async def get_purchase_draft_detail(
    purchase_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOut:
    return await get_purchase(db, ctx.household.id, purchase_id)


@router.patch("/drafts/{purchase_id}", response_model=PurchaseOut)
async def patch_purchase_draft_alias(
    purchase_id: UUID,
    payload: UpdatePurchaseRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOut:
    return await update_draft(db, ctx.household.id, purchase_id, payload)


@router.delete("/drafts/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase_draft_alias(
    purchase_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    await discard_draft(db, ctx.household.id, purchase_id)


@router.post("/drafts/{purchase_id}/confirm", response_model=ConfirmPurchaseResponse)
async def confirm_purchase_draft_alias(
    purchase_id: UUID,
    payload: ConfirmPurchaseRequest | None = Body(default=None),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ConfirmPurchaseResponse:
    return await confirm_purchase(
        db,
        household_id=ctx.household.id,
        user_id=ctx.user.id,
        purchase_id=purchase_id,
        meal_payload=payload,
    )


@router.get("/{purchase_id}", response_model=PurchaseOut)
async def get_purchase_detail(
    purchase_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOut:
    return await get_purchase(db, ctx.household.id, purchase_id)


@router.patch("/{purchase_id}", response_model=PurchaseOut)
async def patch_purchase_draft(
    purchase_id: UUID,
    payload: UpdatePurchaseRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOut:
    return await update_draft(db, ctx.household.id, purchase_id, payload)


@router.patch("/{purchase_id}/meal", response_model=FoodConsumptionOut)
async def patch_purchase_meal(
    purchase_id: UUID,
    payload: ConfirmPurchaseRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> FoodConsumptionOut:
    return await update_meal_classification(
        db,
        household_id=ctx.household.id,
        purchase_id=purchase_id,
        payload=payload,
    )


@router.delete("/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase_draft(
    purchase_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    await discard_draft(db, ctx.household.id, purchase_id)


@router.post("/{purchase_id}/confirm", response_model=ConfirmPurchaseResponse)
async def confirm_purchase_draft(
    purchase_id: UUID,
    payload: ConfirmPurchaseRequest | None = Body(default=None),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ConfirmPurchaseResponse:
    return await confirm_purchase(
        db,
        household_id=ctx.household.id,
        user_id=ctx.user.id,
        purchase_id=purchase_id,
        meal_payload=payload,
    )
