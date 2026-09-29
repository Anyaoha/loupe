from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMRequest:
    system: str
    user: str
    max_tokens: int = 1200


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str | None = None


class LLMProvider(Protocol):
    system: str  # gen_ai.system value, e.g. "anthropic", "aws.bedrock", "mock"
    model: str

    async def complete(self, req: LLMRequest) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass
