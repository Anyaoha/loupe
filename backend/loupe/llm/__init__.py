from loupe.config import Settings
from loupe.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse
from loupe.llm.mock_provider import MockProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockProvider()
    from loupe.llm.anthropic_provider import AnthropicProvider, BedrockProvider

    if settings.llm_provider == "anthropic":
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
        return AnthropicProvider(api_key=key, model=settings.anthropic_model, workspace_id=settings.anthropic_workspace_id)
    if settings.llm_provider == "bedrock":
        return BedrockProvider(model=settings.bedrock_model, region=settings.aws_region)
    raise ValueError(f"unknown llm provider: {settings.llm_provider}")


__all__ = ["build_provider", "LLMProvider", "LLMRequest", "LLMResponse", "LLMError", "MockProvider"]
