from anthropic import APIError, AsyncAnthropic, AsyncAnthropicBedrock

from loupe.llm.base import LLMError, LLMRequest, LLMResponse


class AnthropicProvider:
    system = "anthropic"

    def __init__(self, api_key: str, model: str, workspace_id: str | None = None):
        if not api_key:
            raise LLMError("LOUPE_ANTHROPIC_API_KEY is required for the anthropic provider")
        self.model = model
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self._client = AsyncAnthropic(api_key=api_key, default_headers=headers)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        return await _messages_call(self._client, self.model, req)


class BedrockProvider:
    """Same wire format as Anthropic direct, credentials from the standard AWS chain."""

    system = "aws.bedrock"

    def __init__(self, model: str, region: str):
        self.model = model
        self._client = AsyncAnthropicBedrock(aws_region=region)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        return await _messages_call(self._client, self.model, req)


async def _messages_call(client: AsyncAnthropic | AsyncAnthropicBedrock, model: str, req: LLMRequest) -> LLMResponse:
    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=req.max_tokens,
            system=req.system,
            messages=[{"role": "user", "content": req.user}],
        )
    except APIError as exc:
        raise LLMError(f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:200]}") from exc
    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    return LLMResponse(
        text=text,
        model=msg.model,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        finish_reason=msg.stop_reason,
    )


