from uuid import uuid4

import httpx
from fastapi import Depends

from app.core.tenancy import HouseholdContext, get_household_context
from tests.conftest import EphemeralJWKSProvider, assert_error_envelope


async def test_household_crud_and_cross_household_isolation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token_a = jwks_provider.make_token(sub="user_alice", email="alice@example.com", name="Alice")
    token_b = jwks_provider.make_token(sub="user_bob", email="bob@example.com", name="Bob")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Bootstrap Alice and Bob
    me_a = await client.get("/api/v1/auth/me", headers=headers_a)
    assert me_a.status_code == 200
    alice_default_hh = me_a.json()["active_household"]["id"]

    me_b = await client.get("/api/v1/auth/me", headers=headers_b)
    assert me_b.status_code == 200
    bob_default_hh = me_b.json()["active_household"]["id"]
    assert alice_default_hh != bob_default_hh

    # Alice creates a second household
    create_resp = await client.post(
        "/api/v1/households",
        headers=headers_a,
        json={"name": "Weekend Villa", "currency_code": "inr"},
    )
    assert create_resp.status_code == 201
    villa = create_resp.json()
    assert villa["name"] == "Weekend Villa"
    assert villa["currency_code"] == "INR"
    assert villa["role"] == "owner"

    # Alice lists her 2 households
    list_a = await client.get("/api/v1/households", headers=headers_a)
    assert list_a.status_code == 200
    assert len(list_a.json()["items"]) == 2

    # Alice can fetch her household by path param
    get_villa = await client.get(f"/api/v1/households/{villa['id']}", headers=headers_a)
    assert get_villa.status_code == 200
    assert get_villa.json()["id"] == villa["id"]

    # Bob CANNOT access Alice's household via path param -> 403
    bob_cross_path = await client.get(f"/api/v1/households/{villa['id']}", headers=headers_b)
    assert_error_envelope(bob_cross_path, 403, "FORBIDDEN")

    # Bob CANNOT access non-existent household via path param -> 403
    bob_random_path = await client.get(f"/api/v1/households/{uuid4()}", headers=headers_b)
    assert_error_envelope(bob_random_path, 403, "FORBIDDEN")

    # Bob CANNOT pass Alice's household in X-Household-Id on /auth/me -> 403
    bob_cross_header = await client.get(
        "/api/v1/auth/me",
        headers={**headers_b, "X-Household-Id": alice_default_hh},
    )
    assert_error_envelope(bob_cross_header, 403, "FORBIDDEN")

    # Invalid UUID in X-Household-Id -> 400
    invalid_header = await client.get(
        "/api/v1/auth/me",
        headers={**headers_a, "X-Household-Id": "not-a-uuid"},
    )
    assert_error_envelope(invalid_header, 400, "VALIDATION_ERROR")


async def test_household_context_dependency_rules(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    # Attach a temporary scoped route on the test app to exercise get_household_context
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/api/v1/_test/scoped")
    async def _scoped_route(
        ctx: HouseholdContext = Depends(get_household_context),
    ) -> dict[str, str]:
        return {"household_id": str(ctx.household.id), "role": ctx.role}

    token_u = jwks_provider.make_token(sub="user_scoped", email="scoped@example.com", name="Scoped")
    headers_u = {"Authorization": f"Bearer {token_u}"}

    # Before provisioning any household, scoped route returns 403
    resp_no_hh = await client.get("/api/v1/_test/scoped", headers=headers_u)
    assert_error_envelope(resp_no_hh, 403, "FORBIDDEN")

    # Bootstrap default household (now 1 household)
    me_resp = await client.get("/api/v1/auth/me", headers=headers_u)
    hh1 = me_resp.json()["active_household"]["id"]

    # With 1 household and no X-Household-Id, scoped route resolves it
    resp_one_hh = await client.get("/api/v1/_test/scoped", headers=headers_u)
    assert resp_one_hh.status_code == 200
    assert resp_one_hh.json()["household_id"] == hh1

    # Create 2nd household -> now user has 2 households
    hh2_resp = await client.post(
        "/api/v1/households",
        headers=headers_u,
        json={"name": "Second Kitchen"},
    )
    hh2 = hh2_resp.json()["id"]

    # With 2 households and omitted X-Household-Id -> 400
    resp_ambiguous = await client.get("/api/v1/_test/scoped", headers=headers_u)
    assert_error_envelope(resp_ambiguous, 400, "VALIDATION_ERROR")

    # With X-Household-Id specifying hh2 -> 200
    resp_selected = await client.get(
        "/api/v1/_test/scoped",
        headers={**headers_u, "X-Household-Id": hh2},
    )
    assert resp_selected.status_code == 200
    assert resp_selected.json()["household_id"] == hh2
