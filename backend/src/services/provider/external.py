from collections.abc import AsyncIterator

import httpx

from src.core.config import settings
from src.services.provider.base import ProviderClient
from src.services.system_settings import resolve_api_key_for_provider


class ExternalProviderClient(ProviderClient):
    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name

    async def stream(
        self, *, messages: list[dict], model: str, tools: list[dict]
    ) -> AsyncIterator[str]:
        _ = tools
        text = await self._complete(messages=messages, model=model)
        for token in text.split(" "):
            yield f"{token} "

    async def _complete(self, *, messages: list[dict], model: str) -> str:
        if self.provider_name == "openai":
            return await self._openai_chat(messages=messages, model=model)
        if self.provider_name == "openrouter":
            return await self._openrouter_chat(messages=messages, model=model)
        if self.provider_name == "routerai":
            return await self._routerai_chat(messages=messages, model=model)
        if self.provider_name == "anthropic":
            return await self._anthropic_chat(messages=messages, model=model)
        if self.provider_name == "gemini":
            return await self._gemini_chat(messages=messages, model=model)
        raise RuntimeError(f"Unsupported provider: {self.provider_name}")

    async def _openai_chat(self, *, messages: list[dict], model: str) -> str:
        openai_key = resolve_api_key_for_provider("openai") or settings.openai_api_key
        if not openai_key:
            raise RuntimeError("OpenAI key is not configured")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}"},
                json={"model": model, "messages": messages},
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def _openrouter_chat(self, *, messages: list[dict], model: str) -> str:
        openrouter_key = resolve_api_key_for_provider("openrouter") or settings.openrouter_api_key
        if not openrouter_key:
            raise RuntimeError("OpenRouter key is not configured")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {openrouter_key}"},
                json={"model": model, "messages": messages},
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def _routerai_chat(self, *, messages: list[dict], model: str) -> str:
        from src.services.system_settings import resolve_platform_api_key

        routerai_key = resolve_platform_api_key("routerai")
        if not routerai_key:
            raise RuntimeError("RouterAI key is not configured")
        base = (settings.openai_base_url or "https://routerai.ru/api/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {routerai_key}"},
                json={"model": model, "messages": messages},
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def _anthropic_chat(self, *, messages: list[dict], model: str) -> str:
        anthropic_key = resolve_api_key_for_provider("anthropic") or settings.anthropic_api_key
        if not anthropic_key:
            raise RuntimeError("Anthropic key is not configured")
        user_text = "\n".join(m["content"] for m in messages if m.get("role") == "user")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": user_text}],
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"]

    async def _gemini_chat(self, *, messages: list[dict], model: str) -> str:
        gemini_key = resolve_api_key_for_provider("gemini") or settings.gemini_api_key
        if not gemini_key:
            raise RuntimeError("Gemini key is not configured")
        user_text = "\n".join(m["content"] for m in messages if m.get("role") == "user")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": gemini_key},
                json={"contents": [{"parts": [{"text": user_text}]}]},
            )
            response.raise_for_status()
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
