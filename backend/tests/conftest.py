import base64
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.routing import _DefaultLifespan

from alembic import command
from app.core.config import Settings
from app.main import create_app


def _b64uint(val: int) -> str:
    raw = val.to_bytes((val.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64uint(pub_numbers.n),
        "e": _b64uint(pub_numbers.e),
    }
    return private_key, jwk


class EphemeralJWKSProvider:
    def __init__(self, issuer: str = "https://clerk.example.test") -> None:
        self.issuer = issuer
        self.jwks_url = f"{issuer}/.well-known/jwks.json"
        self.keys: dict[str, tuple[rsa.RSAPrivateKey, dict[str, Any]]] = {}
        self.fetch_count = 0
        self.add_key("kid-1")

    def add_key(self, kid: str) -> None:
        self.keys[kid] = generate_rsa_keypair(kid)

    def remove_key(self, kid: str) -> None:
        self.keys.pop(kid, None)

    def make_token(
        self,
        *,
        sub: str = "user_clerk_123",
        kid: str = "kid-1",
        iss: str | None = None,
        azp: str | None = "https://app.example.test",
        exp_offset: int = 300,
        nbf_offset: int = -5,
        sts: str | None = "active",
        email: str | None = "megha@example.com",
        name: str | None = "Megha",
        extra_claims: dict[str, Any] | None = None,
        private_key_override: rsa.RSAPrivateKey | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": sub,
            "iss": iss if iss is not None else self.issuer,
            "exp": now + exp_offset,
            "nbf": now + nbf_offset,
        }
        if azp is not None:
            payload["azp"] = azp
        if sts is not None:
            payload["sts"] = sts
        if email is not None:
            payload["email"] = email
        if name is not None:
            payload["name"] = name
        if extra_claims:
            payload.update(extra_claims)

        key = private_key_override or self.keys[kid][0]
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == self.jwks_url:
                self.fetch_count += 1
                return httpx.Response(
                    200,
                    json={"keys": [jwk for _, jwk in self.keys.values()]},
                )
            return httpx.Response(404, json={"error": "not found"})

        return httpx.MockTransport(handler)


@pytest.fixture
def jwks_provider() -> EphemeralJWKSProvider:
    return EphemeralJWKSProvider()


@pytest.fixture
def temp_db_url(tmp_path: Path) -> str:
    db_file = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic_cfg, "head")
    return url


@pytest.fixture
async def client(
    temp_db_url: str,
    jwks_provider: EphemeralJWKSProvider,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        DATABASE_URL=temp_db_url,
        CLERK_ISSUER=jwks_provider.issuer,
        CLERK_JWKS_URL=jwks_provider.jwks_url,
        CLERK_AUTHORIZED_PARTIES=["https://app.example.test"],
        JWKS_CACHE_TTL_SECONDS=3600,
        JWKS_REFRESH_COOLDOWN_SECONDS=30,
        JWT_LEEWAY_SECONDS=10,
    )
    jwks_http_client = httpx.AsyncClient(transport=jwks_provider.transport())
    app = create_app(settings_override=settings, jwks_http_client=jwks_http_client)

    lifespan_ctx = app.router.lifespan_context(app)
    assert not isinstance(lifespan_ctx, _DefaultLifespan)
    async with (
        lifespan_ctx,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as async_client,
    ):
        yield async_client
    await jwks_http_client.aclose()


def assert_error_envelope(
    response: httpx.Response,
    expected_status: int,
    expected_code: str,
) -> dict[str, Any]:
    assert response.status_code == expected_status
    body = response.json()
    assert "error" in body
    err = body["error"]
    assert err["code"] == expected_code
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["request_id"], str) and err["request_id"].startswith("req_")
    return json.loads(response.text)
