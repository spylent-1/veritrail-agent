import json

import httpx
import pytest
from openai import AsyncOpenAI

from repopilot.config import Limits, ModelSettings
from repopilot.deepseek import DeepSeekProvider
from repopilot.errors import RepoPilotError
from repopilot.models import Message


async def test_sdk_request_contract_and_bounded_retry():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) < 3:
            return httpx.Response(429, json={"error": {"message": "retry"}})
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "created": 0,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )

    client = AsyncOpenAI(
        api_key="fixture",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    provider = DeepSeekProvider(ModelSettings(), "fixture", client=client)
    try:
        turn = await provider.complete([Message(role="user", content="hello")], [], Limits())
        assert turn.content == "ok" and turn.usage.input_tokens == 12
        assert len(requests) == 3
        assert turn.usage_complete is False
        assert requests[0]["thinking"] == {"type": "disabled"}
        assert requests[0]["model"] == "deepseek-flash"
    finally:
        await provider.close()


async def test_nonretryable_error_does_not_expose_body():
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(401, json={"error": {"message": "secret-body"}})

    client = AsyncOpenAI(
        api_key="fixture",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    provider = DeepSeekProvider(ModelSettings(), "fixture", client=client)
    try:
        with pytest.raises(RepoPilotError) as error:
            await provider.complete([Message(role="user", content="hello")], [], Limits())
        assert "401" in str(error.value) and "secret-body" not in str(error.value)
        assert len(attempts) == 1
    finally:
        await provider.close()
