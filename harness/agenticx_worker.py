"""One-request AgenticX worker process used by the local bridge spike.

This file must be run with the pinned AgenticX spike virtualenv. It has no
tools and uses only a deterministic provider supplied by the integration
harness; it never reads provider credentials.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import time

from agenticx.core import Agent, AgentExecutor, Task
from agenticx.llms.base import BaseLLMProvider
from agenticx.llms.response import LLMResponse, TokenUsage


class FixedProvider(BaseLLMProvider):
    def __init__(self, action_json: str):
        super().__init__(model="harness-deterministic-mock")
        object.__setattr__(self, "_action_json", action_json)

    def invoke(self, prompt, **kwargs):
        return LLMResponse(
            id="harness-response",
            model_name=self.model,
            created=int(time.time()),
            content=self._action_json,
            choices=[],
            token_usage=TokenUsage(),
            cost=0,
        )

    async def ainvoke(self, prompt, **kwargs):
        return self.invoke(prompt, **kwargs)

    def stream(self, prompt, **kwargs):
        yield self.invoke(prompt, **kwargs).content

    async def astream(self, prompt, **kwargs):
        yield (await self.ainvoke(prompt, **kwargs)).content


def main() -> int:
    request = json.loads(sys.stdin.readline())
    task_id = request["taskId"]
    description = request["description"]
    result_text = request["scriptedResult"]
    if not all(isinstance(value, str) and value for value in (task_id, description, result_text)):
        raise ValueError("taskId, description, and scriptedResult must be nonempty strings")
    if max(map(len, (task_id, description, result_text))) > 10000:
        raise ValueError("worker request exceeds local harness limit")

    action = json.dumps({
        "action": "finish_task",
        "result": result_text,
        "reasoning": "Fixed deterministic test response; no model call.",
    })
    provider = FixedProvider(action)
    executor = AgentExecutor(
        llm_provider=provider,
        tools=[],
        max_iterations=4,
        enable_context_compilation=False,
        auto_load_hooks=False,
    )
    agent = Agent(
        id="harness.agenticx.worker",
        name="Harness AgenticX Worker",
        role="bounded task worker",
        goal="Return the supplied deterministic test result.",
        organization_id="agent-research-workspace",
        tools=[],
        allow_delegation=False,
        max_iterations=1,
    )
    task = Task(
        id=task_id,
        agent_id=agent.id,
        description=description,
        expected_output="A deterministic text result.",
        context={},
        dependencies=[],
    )

    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(sys.stderr):
        result = executor.run(agent, task, session_key=task_id)
    output = {"success": bool(result.get("success")), "result": result.get("result")}
    sys.stdout.write(json.dumps(output, separators=(",", ":")) + "\n")
    return 0 if output["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
