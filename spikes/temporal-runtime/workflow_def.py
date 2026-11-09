"""Deterministic Temporal workflow definition; canonical reads/writes live in activities."""

from datetime import timedelta

from temporalio import workflow

POLL_SECONDS = 0.25


@workflow.defn
class CanonicalApprovalWorkflow:
    """Temporal orchestrates retries/timers; the project event store owns approval truth."""

    @workflow.run
    async def run(self, ids: dict[str, str]) -> str:
        while True:
            answer = await workflow.execute_activity(
                "read_canonical_answer",
                ids,
                start_to_close_timeout=timedelta(seconds=10),
            )
            if answer is not None:
                return await workflow.execute_activity(
                    "finalize_canonical_run",
                    answer,
                    start_to_close_timeout=timedelta(seconds=10),
                )
            await workflow.sleep(POLL_SECONDS)
