from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ProviderClient(ABC):
    @abstractmethod
    async def stream(
        self, *, messages: list[dict], model: str, tools: list[dict]
    ) -> AsyncIterator[str]:
        raise NotImplementedError
