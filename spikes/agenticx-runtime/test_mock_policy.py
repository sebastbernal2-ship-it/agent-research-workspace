"""Deterministic, no-provider/no-tool AgenticX worker policy probes."""
from __future__ import annotations

import asyncio
import json
import time
import unittest

from agenticx.core import Agent, AgentExecutor, Task
from agenticx.llms.base import BaseLLMProvider
from agenticx.llms.response import LLMResponse, TokenUsage
from agenticx.runtime.confirm import AsyncConfirmGate


class FixedActionProvider(BaseLLMProvider):
    def __init__(self, action: dict):
        super().__init__(model="issue4-fixed-mock")
        object.__setattr__(self, "_action", json.dumps(action, separators=(",", ":")))

    def invoke(self, prompt, **kwargs):
        return LLMResponse(
            id="issue4-fixed-response",
            model_name=self.model,
            created=int(time.time()),
            content=self._action,
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


def run_action(action: dict):
    agent = Agent(
        id="issue4.researcher",
        name="Synthetic Researcher",
        role="research analyst",
        goal="Compare methods using only supplied synthetic facts; never perform external actions.",
        organization_id="issue4-offline",
        tools=[],
        allow_delegation=False,
        max_iterations=1,
    )
    task = Task(
        id="issue4.non-coding-task",
        agent_id=agent.id,
        description="Compare two synthetic classification methods and report the fixed criteria.",
        expected_output="A short comparison with limitations.",
        context={},
        dependencies=[],
    )
    executor = AgentExecutor(
        llm_provider=FixedActionProvider(action),
        tools=[],
        max_iterations=4,
        enable_context_compilation=False,
        auto_load_hooks=False,
    )
    return executor.run(agent, task, session_key=task.id)


class NoToolsPolicyTests(unittest.TestCase):
    def test_deterministic_non_coding_task_completes_without_tool_calls(self):
        result = run_action({
            "action": "finish_task",
            "result": "Compare accuracy, cost, and robustness on identical synthetic inputs; report limitations.",
            "reasoning": "Fixed offline response.",
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["stats"]["tool_calls"], 0)
        self.assertEqual(result["result"], "Compare accuracy, cost, and robustness on identical synthetic inputs; report limitations.")

    def test_high_risk_action_waits_for_explicit_approval_or_denial(self):
        async def scenario():
            gate = AsyncConfirmGate(timeout_seconds=5)
            pending = asyncio.create_task(gate.request_confirm(
                "Send synthetic report?", {"risk": "high", "request_id": "issue4-high-risk"},
            ))
            await asyncio.sleep(0)
            self.assertFalse(pending.done(), "protected action should remain paused pending a human decision")
            self.assertIn("issue4-high-risk", gate._pending)
            self.assertTrue(gate.resolve("issue4-high-risk", True))
            self.assertTrue(await pending)

            denied = asyncio.create_task(gate.request_confirm(
                "Delete external record?", {"risk": "destructive", "request_id": "issue4-denied"},
            ))
            await asyncio.sleep(0)
            self.assertFalse(denied.done())
            self.assertTrue(gate.resolve("issue4-denied", False))
            self.assertFalse(await denied)

        asyncio.run(scenario())

    def test_prohibited_external_action_is_denied_when_no_tool_is_registered(self):
        result = run_action({
            "action": "tool_call",
            "tool": "prohibited_external_send",
            "args": {"payload": "synthetic-only"},
            "reasoning": "This action is prohibited in the no-tools test.",
        })
        self.assertGreaterEqual(result["stats"]["tool_calls"], 1)
        tool_results = result["event_log"].get_events_by_type("tool_result")
        self.assertEqual(len(tool_results), result["stats"]["tool_calls"])
        self.assertTrue(all(not event.success and "not found" in event.error for event in tool_results))
        self.assertEqual(result["event_log"].get_events_by_type("finish_task"), [])
        self.assertNotIn("sent", result["result"].lower())


if __name__ == "__main__":
    unittest.main()
