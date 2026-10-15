from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    AsyncOpenAI,
    RateLimitError,
)

from tool_loop.models import ModelReply, ModelRequest, ProviderError, ProviderTransientError, Usage
from tool_loop.providers import FixtureProvider, OpenAIProvider


class OutputItem:
    def __init__(self, **data: Any) -> None:
        self.data = data
        self.dump_arguments: list[bool] = []

    def model_dump(self, *, exclude_none: bool) -> dict[str, Any]:
        self.dump_arguments.append(exclude_none)
        return copy.deepcopy({key: value for key, value in self.data.items() if value is not None})


class Client:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.responses = self
        self.requests: list[dict[str, Any]] = []
        self.options: dict[str, Any] = {}
        self.closed = False

    def with_options(self, **options: Any) -> Client:
        self.options.update(options)
        return self

    async def create(self, **request: Any) -> Any:
        self.requests.append(copy.deepcopy(request))
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self) -> None:
        self.closed = True


def response(*items: OutputItem, **overrides: Any) -> SimpleNamespace:
    data = {
        "status": "completed",
        "output": list(items),
        "output_text": "",
        "usage": SimpleNamespace(
            input_tokens=100,
            output_tokens=30,
            input_tokens_details=SimpleNamespace(cached_tokens=40),
        ),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def request() -> ModelRequest:
    return ModelRequest(
        items=({"role": "user", "content": "Diagnose checkout"},),
        tools=({"type": "function", "name": "inspect", "parameters": {"type": "object"}},),
        max_output_tokens=256,
    )


@pytest.mark.asyncio
async def test_responses_adapter_preserves_reasoning_and_full_output_for_replay() -> None:
    reasoning = OutputItem(
        type="reasoning", id="rs_1", summary=[], encrypted_content="opaque", status=None
    )
    call = OutputItem(
        type="function_call", id="fc_1", call_id="call_1", name="inspect", arguments="{}"
    )
    message = OutputItem(type="message", id="msg_1", role="assistant", content=[])
    client = Client(response(reasoning, call, message))
    provider = OpenAIProvider("test-model", client=client)

    reply = await provider.generate(request())

    assert client.options == {"max_retries": 0}
    assert client.requests[0] == {
        "model": "test-model",
        "input": list(request().items),
        "tools": list(request().tools),
        "max_output_tokens": 256,
        "parallel_tool_calls": True,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    assert reply.usage == Usage(input_tokens=100, output_tokens=30, cached_input_tokens=40)
    assert [(call.call_id, call.name, call.arguments) for call in reply.calls] == [
        ("call_1", "inspect", "{}")
    ]
    assert reply.output_items == tuple(
        item.model_dump(exclude_none=True) for item in (reasoning, call, message)
    )
    assert reply.output_items[0]["encrypted_content"] == "opaque"
    assert all(item.dump_arguments[0] is True for item in (reasoning, call, message))

    result = {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'}
    replay = ModelRequest(
        items=request().items + reply.output_items + (result,),
        tools=request().tools,
        max_output_tokens=256,
    )
    await provider.generate(replay)
    assert client.requests[1]["input"] == list(replay.items)


@pytest.mark.asyncio
async def test_real_sdk_wire_contract_uses_mock_transport_without_network() -> None:
    received: list[dict[str, Any]] = []

    def handle(http_request: httpx.Request) -> httpx.Response:
        received.append(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_fixture",
                "object": "response",
                "created_at": 1768486200,
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "id": "rs_fixture",
                        "type": "reasoning",
                        "summary": [],
                        "encrypted_content": "opaque",
                    },
                    {
                        "id": "fc_fixture",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_fixture",
                        "name": "inspect",
                        "arguments": "{}",
                    },
                ],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 20,
                    "total_tokens": 70,
                    "input_tokens_details": {"cached_tokens": 10},
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        async with AsyncOpenAI(api_key="synthetic-test-key", http_client=http_client) as client:
            reply = await OpenAIProvider("test-model", client=client).generate(request())
    assert reply.calls[0].call_id == "call_fixture"
    assert reply.output_items[0]["encrypted_content"] == "opaque"
    assert reply.usage == Usage(50, 20, 10)
    assert received[0]["store"] is False
    assert received[0]["include"] == ["reasoning.encrypted_content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["incomplete", "failed", "cancelled", "queued", "in_progress", None]
)
async def test_partial_responses_never_become_executable_calls(status: str | None) -> None:
    call = OutputItem(type="function_call", call_id="call_1", name="inspect", arguments="{}")
    provider = OpenAIProvider("test-model", client=Client(response(call, status=status)))
    with pytest.raises(ProviderError, match="did not complete"):
        await provider.generate(request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "inspect",
            "arguments": "{}",
            "status": "incomplete",
        },
        {"type": "function_call", "call_id": "call_1", "name": "inspect", "arguments": {}},
        {"type": "function_call", "call_id": "", "name": "inspect", "arguments": "{}"},
        {"type": "function_call", "call_id": "call_1", "name": "", "arguments": "{}"},
        {"type": "function_call", "call_id": "call_1", "name": "inspect"},
    ],
)
async def test_malformed_function_call_protocol_fails_closed(item: dict[str, Any]) -> None:
    provider = OpenAIProvider("test-model", client=Client(response(OutputItem(**item))))
    with pytest.raises(ProviderError, match="violated the model protocol"):
        await provider.generate(request())


@pytest.mark.asyncio
async def test_response_text_and_absent_usage_are_supported() -> None:
    provider = OpenAIProvider(
        "test-model", client=Client(response(output_text="A complete answer", usage=None))
    )
    reply = await provider.generate(request())
    assert reply.text == "A complete answer"
    assert reply.usage == Usage()
    assert reply.calls == ()


def sdk_error(status: int) -> APIStatusError:
    transport_request = httpx.Request("POST", "https://example.invalid/v1/responses")
    transport_response = httpx.Response(status, request=transport_request)
    error_type = RateLimitError if status == 429 else APIStatusError
    return error_type(
        "SECRET request payload / credential",
        response=transport_response,
        body={"secret": "payload"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_sdk_errors_are_sanitized_and_not_retried_inside_adapter(
    status: int,
) -> None:
    client = Client(error=sdk_error(status))
    provider = OpenAIProvider("test-model", client=client)
    with pytest.raises(ProviderTransientError) as error:
        await provider.generate(request())
    assert "SECRET" not in str(error.value)
    assert "payload" not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_connection_error_is_transient() -> None:
    client = Client(
        error=APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    )
    with pytest.raises(ProviderTransientError, match="connection failed"):
        await OpenAIProvider("test-model", client=client).generate(request())
    assert len(client.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_fatal_sdk_errors_are_sanitized(status: int) -> None:
    provider = OpenAIProvider("test-model", client=Client(error=sdk_error(status)))
    with pytest.raises(ProviderError, match=f"HTTP {status}") as error:
        await provider.generate(request())
    assert not isinstance(error.value, ProviderTransientError)
    assert "SECRET" not in str(error.value)


@pytest.mark.asyncio
async def test_sdk_response_validation_failure_is_sanitized() -> None:
    transport_response = httpx.Response(
        200, request=httpx.Request("POST", "https://example.invalid")
    )
    invalid = APIResponseValidationError(response=transport_response, body={"secret": "payload"})
    provider = OpenAIProvider("test-model", client=Client(error=invalid))
    with pytest.raises(ProviderError, match="invalid API response") as error:
        await provider.generate(request())
    assert "payload" not in str(error.value)


@pytest.mark.asyncio
async def test_only_internally_created_clients_are_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    owned = Client()
    construction: dict[str, Any] = {}

    def build(**options: Any) -> Client:
        construction.update(options)
        return owned

    monkeypatch.setattr("tool_loop.providers.AsyncOpenAI", build)
    async with OpenAIProvider("test-model", api_key="test-only"):
        assert owned.closed is False
    assert construction == {"api_key": "test-only", "max_retries": 0}
    assert owned.closed is True

    injected = Client()
    async with OpenAIProvider("test-model", client=injected):
        pass
    assert injected.closed is False


@pytest.mark.asyncio
async def test_fixture_is_finite_and_snapshots_requests_and_replies() -> None:
    original = ModelReply(text="synthetic", output_items=({"type": "reasoning", "summary": []},))
    provider = FixtureProvider([original])
    original.output_items[0]["summary"].append("outside mutation")
    model_request = request()
    reply = await provider.generate(model_request)
    model_request.items[0]["content"] = "changed after request"
    assert provider.requests[0].items[0]["content"] == "Diagnose checkout"
    assert reply.output_items[0]["summary"] == []
    assert "synthetic" in provider.usage_label
    with pytest.raises(ProviderError, match="script exhausted"):
        await provider.generate(model_request)


def test_client_constructor_sanitizes_invalid_environment_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-never-expose")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid:sensitive-port-marker")
    with pytest.raises(ProviderError, match="OpenAI client configuration is invalid") as error:
        OpenAIProvider("test-model")
    assert "sensitive-port-marker" not in str(error.value)
    assert "synthetic-key-never-expose" not in str(error.value)
    assert error.value.__suppress_context__ is True
