"""Deterministic unit tests for Phase 3-5 OCR, HF LLM provider, and recipe discovery."""

from __future__ import annotations

import json

import httpx
import pytest

from app.infra.llm.hf_provider import HuggingFaceRecipeProvider
from app.infra.ocr.pipeline import (
    extract_receipt,
    normalize_metric_unit,
    parse_inr_to_paise,
)
from app.infra.recipes.reference_discovery import discover_recipe_references

# ---------------------------------------------------------------------------
# OCR & Receipt Parsing Tests
# ---------------------------------------------------------------------------


def test_parse_inr_to_paise_variants() -> None:
    assert parse_inr_to_paise("₹120.50") == 12050
    assert parse_inr_to_paise("Rs. 1,250.00") == 125000
    assert parse_inr_to_paise("INR 45") == 4500
    assert parse_inr_to_paise("Rs 99.99") == 9999
    assert parse_inr_to_paise("") is None
    assert parse_inr_to_paise("-10.00") is None


def test_normalize_metric_unit_variants() -> None:
    assert normalize_metric_unit(500, "g") == (500.0, "g")
    assert normalize_metric_unit(1.5, "kg") == (1500.0, "g")
    assert normalize_metric_unit(250, "ml") == (250.0, "ml")
    assert normalize_metric_unit(2, "l") == (2000.0, "ml")
    assert normalize_metric_unit(6, "pcs") == (6.0, "pcs")
    assert normalize_metric_unit(1, "dozen") == (12.0, "pcs")
    assert normalize_metric_unit(2, "pack") == (2.0, "pcs")
    assert normalize_metric_unit(1, "unknown_unit") == (None, None)


@pytest.mark.asyncio
async def test_extract_receipt_text_reconciles_math_and_units() -> None:
    transcript = """
    FreshMart Superstore
    Date: 2026-11-17
    Tata Salt 1 kg          ₹28.00
    Amul Milk 500ml x 2     Rs. 54.00
    Farm Eggs 1 dozen       INR 84.00
    Brown Bread 1 pack      40.00
    Subtotal                ₹206.00
    CGST                    ₹5.00
    SGST                    ₹5.00
    Discount                ₹10.00
    Grand Total             ₹206.00
    """
    result = await extract_receipt(
        filename="receipt.txt",
        content_type="text/plain",
        data=transcript.encode("utf-8"),
    )

    assert result.source_type == "text"
    assert result.merchant_name == "FreshMart Superstore"
    assert result.receipt_date == "2026-11-17"
    assert len(result.lines) == 4
    assert result.lines[0].normalized_qty == 1000.0
    assert result.lines[0].base_unit == "g"
    assert result.lines[0].total_paise == 2800
    assert result.lines[1].normalized_qty == 1000.0
    assert result.lines[1].base_unit == "ml"
    assert result.lines[1].total_paise == 5400
    assert result.lines[2].normalized_qty == 12.0
    assert result.lines[2].base_unit == "pcs"
    assert result.tax_paise == 1000
    assert result.discount_paise == 1000
    assert result.grand_total_paise == 20600
    assert result.computed_total_paise == 20600
    assert result.math_reconciled is True
    assert result.needs_review is False


@pytest.mark.asyncio
async def test_extract_receipt_flags_math_mismatch() -> None:
    transcript = """
    Corner Kirana
    Basmati Rice 2 kg   ₹240.00
    Toor Dal 1 kg       ₹160.00
    Grand Total         ₹500.00
    """
    result = await extract_receipt(
        filename="mismatch.txt",
        content_type="text/plain",
        data=transcript.encode("utf-8"),
    )

    assert result.computed_total_paise == 40000
    assert result.grand_total_paise == 50000
    assert result.math_reconciled is False
    assert result.needs_review is True
    assert any("Math mismatch" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_extract_receipt_image_graceful_fallback_without_tesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.infra.ocr.pipeline.shutil.which", lambda _cmd: None)
    # Minimal fake PNG header bytes
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    result = await extract_receipt(
        filename="camera.png",
        content_type="image/png",
        data=fake_png,
    )
    assert result.source_type == "image"
    assert result.needs_review is True
    assert len(result.warnings) >= 1


# ---------------------------------------------------------------------------
# HuggingFaceRecipeProvider Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hf_provider_missing_token_uses_deterministic_inventory_grounded_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    provider = HuggingFaceRecipeProvider(hf_token="")
    inventory = [
        {
            "id": "inv-1",
            "name": "Spinach",
            "quantity": 200,
            "unit": "g",
            "is_expiring_soon": True,
        },
        {
            "id": "inv-2",
            "name": "Paneer",
            "quantity": 250,
            "unit": "g",
            "is_expiring_soon": False,
        },
        {
            "id": "inv-3",
            "name": "Garlic",
            "quantity": 0.25,
            "unit": "pcs",
            "is_low_stock": True,
        },
    ]

    result = await provider.generate_recipes(
        message="Quick high-protein dinner using expiring vegetables",
        inventory_items=inventory,
        servings=3,
        max_prep_minutes=20,
        dietary="vegetarian",
    )

    assert result.provider_status == "fallback_missing_token"
    assert "HF_TOKEN is not configured" in result.provider_message
    assert len(result.recipes) >= 1
    top = result.recipes[0]
    assert top.servings == 3
    assert top.prep_minutes <= 20
    assert any(ing.name.lower() == "paneer" for ing in top.available_ingredients)
    assert any(ing.name.lower() == "garlic" for ing in top.partial_ingredients)
    assert any(ing.needs_purchase is True for ing in top.missing_ingredients)
    assert "Spinach" in top.uses_expiring_items


@pytest.mark.asyncio
async def test_hf_provider_empty_inventory_never_fabricates_stock() -> None:
    provider = HuggingFaceRecipeProvider(hf_token="")
    result = await provider.generate_recipes(
        message="South Indian quick breakfast",
        inventory_items=[],
        servings=2,
    )

    assert result.provider_status == "fallback_missing_token"
    assert len(result.recipes) >= 1
    for recipe in result.recipes:
        assert recipe.available_ingredients == []
        assert recipe.partial_ingredients == []
        assert len(recipe.missing_ingredients) > 0
        assert all(
            ing.status == "missing" and ing.needs_purchase is True
            for ing in recipe.missing_ingredients
        )
    assert len(result.follow_up_questions) >= 1


@pytest.mark.asyncio
async def test_hf_provider_live_mocked_response(monkeypatch: pytest.MonkeyPatch) -> None:
    llm_json = {
        "recipes": [
            {
                "title": "Tomato Rasam & Rice",
                "description": "Light South Indian spiced tomato broth.",
                "servings": 2,
                "prep_minutes": 15,
                "cuisine": "South Indian",
                "dietary_tags": ["vegan", "quick"],
                "available_ingredients": [
                    {
                        "name": "Tomato",
                        "quantity": "2 medium",
                        "status": "available",
                        "needs_purchase": False,
                        "inventory_item_id": "item-1",
                    }
                ],
                "partial_ingredients": [],
                "missing_ingredients": [
                    {
                        "name": "Curry leaves",
                        "quantity": "8 leaves",
                        "status": "missing",
                        "needs_purchase": True,
                        "inventory_item_id": None,
                    }
                ],
                "steps": ["Simmer tomatoes with spices.", "Temper mustard seeds."],
                "uses_expiring_items": ["Tomato"],
            }
        ],
        "follow_up_questions": ["Would you like to add curry leaves to your shopping list?"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-test-token"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(llm_json)}}]},
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("app.infra.llm.hf_provider.httpx.AsyncClient", mock_client_factory)

    provider = HuggingFaceRecipeProvider(hf_token="secret-test-token")
    result = await provider.generate_recipes(
        message="Quick South Indian soup",
        inventory_items=[{"id": "item-1", "name": "Tomato", "quantity": 3}],
    )

    assert result.provider_status == "live_hf"
    assert len(result.recipes) == 1
    assert result.recipes[0].title == "Tomato Rasam & Rice"
    assert result.recipes[0].available_ingredients[0].name == "Tomato"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,body_text",
    [
        (429, "rate limited"),
        (401, "unauthorized"),
        (200, json.dumps({"choices": [{"message": {"content": "NOT_VALID_JSON"}}]})),
    ],
)
async def test_hf_provider_error_fallback_never_crashes_or_leaks_token(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body_text: str,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=body_text)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("app.infra.llm.hf_provider.httpx.AsyncClient", mock_client_factory)

    secret_token = "hf_super_secret_should_never_leak"
    provider = HuggingFaceRecipeProvider(hf_token=secret_token)
    result = await provider.generate_recipes(
        message="Quick dinner",
        inventory_items=[{"id": "1", "name": "Eggs", "quantity": 6}],
    )

    assert result.provider_status == "fallback_error"
    assert secret_token not in result.provider_message
    assert len(result.recipes) >= 1


# ---------------------------------------------------------------------------
# Reference Discovery Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_recipe_references_validates_robots_and_schema() -> None:
    ddg_html = """
    <html><body>
      <a class="result__a" href="https://blocked.example.com/dal">Blocked Dal</a>
      <a class="result__a" href="https://unrelated.example.com/cars">Car Review</a>
      <a class="result__a" href="https://recipes.example.org/dal-tadka">Dal Tadka</a>
    </body></html>
    """
    valid_recipe_html = """
    <html>
      <head>
        <title>South Indian Dal Tadka Recipe</title>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Recipe",
            "name": "South Indian Dal Tadka",
            "description": "Tempered yellow lentils with garlic and curry leaves.",
            "recipeIngredient": ["1 cup toor dal", "2 tomatoes", "8 curry leaves"]
          }
        </script>
      </head>
      <body><p>Classic tempered toor dal with tomato.</p></body>
    </html>
    """
    unrelated_html = """
    <html>
      <head><title>2026 Electric Car Review</title></head>
      <body><p>Battery range and charging speed.</p></body>
    </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://html.duckduckgo.com/html/"):
            return httpx.Response(200, text=ddg_html, headers={"content-type": "text/html"})
        if url == "https://blocked.example.com/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        if url == "https://unrelated.example.com/robots.txt":
            return httpx.Response(404)
        if url == "https://unrelated.example.com/cars":
            return httpx.Response(200, text=unrelated_html, headers={"content-type": "text/html"})
        if url == "https://recipes.example.org/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if url == "https://recipes.example.org/dal-tadka":
            return httpx.Response(
                200, text=valid_recipe_html, headers={"content-type": "text/html"}
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        refs = await discover_recipe_references(
            recipe_title="South Indian Dal Tadka",
            key_ingredients=["toor dal", "tomato"],
            limit=2,
            client=client,
        )

    assert len(refs) == 1
    assert refs[0].url == "https://recipes.example.org/dal-tadka"
    assert refs[0].source == "recipes.example.org"
    assert refs[0].verified is True
    assert "toor dal" in refs[0].matched_ingredients


@pytest.mark.asyncio
async def test_discover_recipe_references_offline_returns_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Network unreachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        refs = await discover_recipe_references(
            recipe_title="Paneer Stir-Fry",
            key_ingredients=["paneer", "spinach"],
            client=client,
        )

    assert refs == []
