import base64
import json
import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings
from app.main import create_app
from tests.conftest import EphemeralJWKSProvider, assert_error_envelope


def _b64json(data: dict[str, object]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


async def test_auth_me_auto_provisions_default_household_and_updates_profile(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token = jwks_provider.make_token(
        sub="user_clerk_001",
        email="megha@example.com",
        name="Megha S",
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["clerk_user_id"] == "user_clerk_001"
    assert body["user"]["email"] == "megha@example.com"
    assert body["user"]["display_name"] == "Megha S"
    assert body["active_household"]["name"] == "My Kitchen"
    assert body["active_household"]["currency_code"] == "INR"
    assert body["active_household"]["role"] == "owner"
    assert len(body["households"]) == 1
    assert body["households"][0]["id"] == body["active_household"]["id"]

    # Calling /auth/me again is idempotent and does not duplicate household
    resp2 = await client.get("/api/v1/auth/me", headers=headers)
    assert resp2.status_code == 200
    assert len(resp2.json()["households"]) == 1
    assert resp2.json()["active_household"]["id"] == body["active_household"]["id"]

    # Update display_name via PATCH /api/v1/auth/me
    patch_resp = await client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"display_name": "  Chef Megha  "},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "Chef Megha"

    # Subsequent /auth/me preserves updated display_name
    resp3 = await client.get("/api/v1/auth/me", headers=headers)
    assert resp3.status_code == 200
    assert resp3.json()["user"]["display_name"] == "Chef Megha"


async def test_rejects_missing_or_malformed_auth_header(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert_error_envelope(resp, 401, "UNAUTHORIZED")

    resp_bad = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert_error_envelope(resp_bad, 401, "UNAUTHORIZED")


async def test_rejects_invalid_signature(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    rogue_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad_token = jwks_provider.make_token(private_key_override=rogue_key)
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert_error_envelope(resp, 401, "UNAUTHORIZED")


async def test_rejects_expired_token(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    expired_token = jwks_provider.make_token(exp_offset=-60)
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert_error_envelope(resp, 401, "UNAUTHORIZED")


async def test_rejects_alg_none_and_hs256(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    now = int(time.time())
    payload = {
        "sub": "user_clerk_001",
        "iss": jwks_provider.issuer,
        "azp": "https://app.example.test",
        "exp": now + 300,
    }
    hdr = _b64json({"alg": "none", "typ": "JWT", "kid": "kid-1"})
    alg_none_token = f"{hdr}.{_b64json(payload)}."
    resp_none = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {alg_none_token}"},
    )
    assert_error_envelope(resp_none, 401, "UNAUTHORIZED")

    hs256_token = jwt.encode(
        payload,
        "shared-secret-not-allowed-1234567890",
        algorithm="HS256",
        headers={"kid": "kid-1"},
    )
    resp_hs = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {hs256_token}"},
    )
    assert_error_envelope(resp_hs, 401, "UNAUTHORIZED")


async def test_rejects_wrong_issuer_wrong_azp_inactive_sts_and_unknown_kid(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    wrong_iss = jwks_provider.make_token(iss="https://evil.example.test")
    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {wrong_iss}"}),
        401,
        "UNAUTHORIZED",
    )

    wrong_azp = jwks_provider.make_token(azp="https://untrusted.example.test")
    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {wrong_azp}"}),
        401,
        "UNAUTHORIZED",
    )

    inactive_sts = jwks_provider.make_token(sts="revoked")
    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {inactive_sts}"}),
        401,
        "UNAUTHORIZED",
    )

    unknown_kid = jwks_provider.make_token(
        kid="kid-unknown",
        private_key_override=jwks_provider.keys["kid-1"][0],
    )
    assert_error_envelope(
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {unknown_kid}"}),
        401,
        "UNAUTHORIZED",
    )


async def test_kid_key_rotation_refreshes_jwks_and_respects_cooldown(
    temp_db_url: str,
) -> None:
    provider = EphemeralJWKSProvider()
    settings = Settings(
        DATABASE_URL=temp_db_url,
        CLERK_ISSUER=provider.issuer,
        CLERK_JWKS_URL=provider.jwks_url,
        CLERK_AUTHORIZED_PARTIES=["https://app.example.test"],
        JWKS_CACHE_TTL_SECONDS=3600,
        JWKS_REFRESH_COOLDOWN_SECONDS=0,  # allow immediate rotation test
        JWT_LEEWAY_SECONDS=10,
    )
    jwks_http_client = httpx.AsyncClient(transport=provider.transport())
    app = create_app(settings_override=settings, jwks_http_client=jwks_http_client)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac,
    ):
        token_v1 = provider.make_token(sub="user_rotate", kid="kid-1")
        resp1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_v1}"})
        assert resp1.status_code == 200
        assert provider.fetch_count == 1

        # Rotate key on JWKS endpoint to kid-2
        provider.add_key("kid-2")
        provider.remove_key("kid-1")
        token_v2 = provider.make_token(sub="user_rotate", kid="kid-2")
        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_v2}"})
        assert resp2.status_code == 200
        assert provider.fetch_count == 2

    await jwks_http_client.aclose()
