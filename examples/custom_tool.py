"""Run after `make setup`: .venv/bin/python examples/custom_tool.py"""

import asyncio

from tool_loop.models import ModelReply, Tool, ToolCall
from tool_loop.providers import FixtureProvider
from tool_loop.runtime import Agent
from tool_loop.telemetry import Telemetry


async def compare_release(args):
    return {"release": args["release"], "healthy": args["release"] == "stable"}


async def main():
    model = FixtureProvider(
        [
            ModelReply(calls=(ToolCall("health", "compare_release", '{"release":"stable"}'),)),
            ModelReply(
                text="The scripted protocol completed. Inspect the actual tool result below."
            ),
        ]
    )
    tool = Tool(
        name="compare_release",
        description="Inspect a synthetic release health flag.",
        parameters={
            "type": "object",
            "properties": {"release": {"type": "string", "enum": ["stable", "canary"]}},
            "required": ["release"],
            "additionalProperties": False,
        },
        handler=compare_release,
        parallel_safe=True,
    )
    with Telemetry() as telemetry:
        result = await Agent(model, [tool], telemetry=telemetry).run("Check the stable release")
    print(result.status, result.tool_results[0].value)


if __name__ == "__main__":
    asyncio.run(main())
