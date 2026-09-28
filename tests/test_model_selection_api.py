from tests.conftest import auth_tokens


def test_provider_catalog_exposes_priced_openai_models(client, monkeypatch) -> None:
    from src.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    headers = auth_tokens(client, "model-catalog@airuntime.dev")

    response = client.get("/api/v1/providers", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"]["openai"] is True
    assert body["credits_per_rub"] == 100
    assert [row["id"] for row in body["models"]["openai"]] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    # Catalog prices are quoted with the markup already applied (Epic A3).
    assert body["models"]["openai"][0]["output_credits_per_million"] == 330_000


def test_chat_rejects_model_slug_outside_public_catalog(client, monkeypatch) -> None:
    from src.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    headers = auth_tokens(client, "model-reject@airuntime.dev")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"type": "website", "name": "Model Guard", "description": ""},
    ).json()
    chat = client.post(f"/api/v1/projects/{project['id']}/chats", headers=headers).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/chats/{chat['id']}/stream",
        headers=headers,
        json={
            "content": "Сделай лендинг",
            "provider": "openai",
            "model": "arbitrary-client-model",
        },
    )

    assert response.status_code == 400
    assert "not available" in response.json()["detail"]
