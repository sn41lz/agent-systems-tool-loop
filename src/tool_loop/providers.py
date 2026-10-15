"""Small model protocol adapters. Scheduling and retries belong to the runtime."""

from __future__ import annotations

import copy
from typing import Any, cast

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI, RateLimitError
from openai.types.responses import ResponseInputParam, ToolParam

from .models import (
    ModelReply,
    ModelRequest,
    ProviderError,
    ProviderTransientError,
    ToolCall,
    Usage,
)


class FixtureProvider:
    """Replay a finite script; no inference, networking, or real token accounting.

    A new instance is required per run. Requests are snapshots so callers cannot
    accidentally rewrite the recorded transcript through mutable dictionaries.
    """

    provider_name = "fixture"
    usage_label = "synthetic fixture tokens; not measured model usage"
    model = "scripted-fixture"

    def __init__(self, script: list[ModelReply]) -> None:
        self._script = tuple(copy.deepcopy(script))
        self.requests: list[ModelRequest] = []
        self._cursor = 0

    async def generate(self, request: ModelRequest) -> ModelReply:
        self.requests.append(copy.deepcopy(request))
        if self._cursor >= len(self._script):
            raise ProviderError("Fixture script exhausted before the run completed.")
        result = copy.deepcopy(self._script[self._cursor])
        self._cursor += 1
        return result


class OpenAIProvider:
    """Raw, stateless Responses API with one visible runtime retry policy.

    Every output item is retained, including encrypted reasoning, because the
    next model request must replay it alongside function results. This adapter
    never logs prompts, arguments, outputs, credentials, or SDK exception bodies.
    A supplied client remains owned by its caller; internally created clients
    can be closed with ``aclose`` or the async context manager.
    """

    provider_name = "openai"
    usage_label = "OpenAI response usage"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be nonempty")
        self.model = model
        self._owns_client = client is None
        try:
            self._client = (
                AsyncOpenAI(api_key=api_key, max_retries=0)
                if client is None
                else client.with_options(max_retries=0)
            )
        except Exception:
            # SDK configuration errors can echo a malformed URL or credential.
            raise ProviderError("OpenAI client configuration is invalid.") from None

    async def generate(self, request: ModelRequest) -> ModelReply:
        try:
            response = await self._client.responses.create(
                model=self.model,
                input=cast(ResponseInputParam, list(request.items)),
                tools=cast(list[ToolParam], list(request.tools)),
                max_output_tokens=request.max_output_tokens,
                parallel_tool_calls=True,
                store=False,
                include=["reasoning.encrypted_content"],
            )
        except RateLimitError:
            raise ProviderTransientError("OpenAI rate limit exceeded.") from None
        except APIConnectionError:
            raise ProviderTransientError("OpenAI connection failed.") from None
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderTransientError("OpenAI service temporarily unavailable.") from None
            raise ProviderError(f"OpenAI request rejected (HTTP {exc.status_code}).") from None
        except APIError:
            raise ProviderError("OpenAI returned an invalid API response.") from None

        if getattr(response, "status", None) != "completed":
            # Never execute a partial call even if its JSON looks complete.
            raise ProviderError("OpenAI response did not complete; no tools were executed.")
        try:
            items = tuple(item.model_dump(exclude_none=True) for item in response.output)
            calls: list[ToolCall] = []
            for item in items:
                if item.get("type") != "function_call":
                    continue
                if item.get("status", "completed") != "completed":
                    raise ValueError("incomplete function call")
                call_id, name, arguments = item["call_id"], item["name"], item["arguments"]
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or not isinstance(name, str)
                    or not name
                    or not isinstance(arguments, str)
                ):
                    raise ValueError("invalid function call fields")
                calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
            usage = response.usage
            details = getattr(usage, "input_tokens_details", None)
            measured = Usage(
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                cached_input_tokens=getattr(details, "cached_tokens", 0),
            )
            text = response.output_text
            if not isinstance(text, str):
                raise ValueError("invalid output text")
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ProviderError("OpenAI response violated the model protocol.") from None
        return ModelReply(text=text, calls=tuple(calls), usage=measured, output_items=items)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def __aenter__(self) -> OpenAIProvider:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()
