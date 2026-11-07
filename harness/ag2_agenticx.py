"""AG2 LLM-client adapter that delegates a test turn to AgenticX subprocess."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from ag2 import Context
from ag2.config import LLMClient, ModelConfig
from ag2.events import BaseEvent, ModelMessage, ModelResponse

from .agenticx_bridge import AgenticXSubprocessWorker


class AgenticXTestModelConfig(ModelConfig):
    """Deterministic integration config; intentionally not a provider client."""

    def __init__(self, worker: AgenticXSubprocessWorker, scripted_result: str, *, task_id: str):
        self.worker = worker
        self.scripted_result = scripted_result
        self.task_id = task_id

    @property
    def provider(self):
        return "agenticx-subprocess-test"

    @property
    def model(self) -> str:
        return "agenticx-v0.6.5-deterministic"

    def copy(self):
        return self

    def create(self) -> LLMClient:
        return AgenticXTestModelClient(self)

    def create_files_client(self):
        raise NotImplementedError("The AgenticX test bridge has no Files API")


class AgenticXTestModelClient(LLMClient):
    def __init__(self, config: AgenticXTestModelConfig):
        self.config = config

    async def __call__(
        self,
        messages: Sequence[BaseEvent],
        context: Context,
        **kwargs: Any,
    ) -> ModelResponse:
        rendered = "\n".join(
            str(getattr(message, "content", message))[:4000]
            for message in messages
        )[:8000]
        worker_result = await asyncio.to_thread(
            self.config.worker.run_task,
            task_id=self.config.task_id,
            description="Respond to this AG2 turn through the AgenticX AgentExecutor.\n" + rendered,
            scripted_result=self.config.scripted_result,
        )
        response = ModelMessage(worker_result["result"], metadata={"worker": "agenticx-subprocess"})
        await context.send(response)
        return ModelResponse(response)
