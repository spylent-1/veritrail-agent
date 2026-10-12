"""The only SDK-specific boundary. SDK automatic retries are deliberately disabled."""

import asyncio
from typing import cast

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, omit
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from repopilot.config import Limits, ModelSettings
from repopilot.errors import RepoPilotError
from repopilot.models import JsonObject, Message, ModelTurn, ToolCall, Usage, validate_messages


class DeepSeekProvider:
    def __init__(
        self, settings: ModelSettings, key: str, *, client: AsyncOpenAI | None = None
    ) -> None:
        self.settings = settings
        self.client = client or AsyncOpenAI(
            api_key=key, base_url=settings.base_url, max_retries=0, timeout=60
        )

    async def complete(
        self,
        messages: list[Message],
        tools: list[JsonObject],
        limits: Limits,
    ) -> ModelTurn:
        validate_messages(messages)
        for attempt in range(3):
            try:
                response = await self.client.chat.completions.create(
                    model=self.settings.model,
                    messages=cast(list[ChatCompletionMessageParam], [m.wire() for m in messages]),
                    tools=cast(list[ChatCompletionToolUnionParam], tools) if tools else omit,
                    max_tokens=limits.output_tokens,
                    timeout=limits.model_timeout,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                if not response.choices:
                    raise RepoPilotError("EMPTY_RESPONSE", "Provider returned no completion.")
                choice = response.choices[0]
                message = choice.message
                if message.refusal:
                    raise RepoPilotError("MODEL_REFUSAL", "The model refused the request.")
                calls: list[ToolCall] = []
                for call in message.tool_calls or []:
                    if call.type != "function":
                        raise RepoPilotError(
                            "UNSUPPORTED_CALL", "Only function tools are supported."
                        )
                    calls.append(
                        ToolCall(
                            id=call.id, name=call.function.name, arguments=call.function.arguments
                        )
                    )
                return ModelTurn(
                    usage_complete=attempt == 0,
                    content=message.content or "",
                    tool_calls=calls,
                    finish_reason=choice.finish_reason,
                    usage=Usage(
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                    if response.usage
                    else None,
                )
            except (APIConnectionError, APIStatusError) as exc:
                retryable = isinstance(exc, APIConnectionError) or (
                    isinstance(exc, APIStatusError)
                    and (exc.status_code == 429 or exc.status_code >= 500)
                )
                if not retryable or attempt == 2:
                    status = (
                        str(exc.status_code) if isinstance(exc, APIStatusError) else "connection"
                    )
                    raise RepoPilotError(
                        "PROVIDER_ERROR", f"Model request failed ({status})."
                    ) from None
                await asyncio.sleep(0.25 * 2**attempt)
        raise AssertionError("unreachable")

    async def close(self) -> None:
        await self.client.close()
