"""Temporal workflows on the time-skipping test server, with fake activities: busy/requeued loops, the delivery
receipt wait (and its 10-minute timeout), the 5-minute 'updated SMS' timer and call-end flush."""
import asyncio
import uuid
from datetime import timedelta

import pytest

temporalio = pytest.importorskip("temporalio")

from temporalio import activity  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from apps.control.flows import (  # noqa: E402
    TASK_QUEUE,
    BigDocTaskWorkflow,
    DeliveryWorkflow,
    DocumentTaskWorkflow,
    SessionUpdatesWorkflow,
)


class Fakes:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.finalized = []

    @activity.defn(name="run_task")
    async def run_task(self, task_id: str) -> str:
        return self.outcomes.pop(0)

    @activity.defn(name="run_big_task")
    async def run_big_task(self, task_id: str) -> str:
        return self.outcomes.pop(0)

    @activity.defn(name="send_delivery")
    async def send_delivery(self, delivery_id: str) -> str:
        return "submitted"

    @activity.defn(name="finalize_updates")
    async def finalize_updates(self, session_id: str) -> int:
        self.finalized.append(session_id)
        return 1


@pytest.fixture(scope="module")
def env():
    try:
        e = asyncio.run(asyncio.wait_for(WorkflowEnvironment.start_time_skipping(), 120))
    except Exception as ex:  # offline: the test server binary is downloaded on first use
        pytest.skip(f"Temporal test server unavailable: {type(ex).__name__}")
    yield e
    asyncio.run(e.shutdown())


def run(env, fakes, body):
    async def go():
        async with Worker(env.client, task_queue=TASK_QUEUE,
                          workflows=[DocumentTaskWorkflow, BigDocTaskWorkflow, DeliveryWorkflow,
                                     SessionUpdatesWorkflow],
                          activities=[fakes.run_task, fakes.run_big_task, fakes.send_delivery,
                                      fakes.finalize_updates]):
            return await body(env.client)
    return asyncio.run(go())


def test_task_waits_while_the_document_is_busy_then_commits(env):
    fakes = Fakes(["busy", "requeued", "busy", "committed"])

    async def body(client):
        return await client.execute_workflow(DocumentTaskWorkflow.run, "task_1", id=f"t-{uuid.uuid4()}",
                                             task_queue=TASK_QUEUE)
    assert run(env, fakes, body) == "committed" and fakes.outcomes == []


def test_delivery_waits_for_the_receipt_and_times_out_without_one(env):
    async def delivered(client):
        h = await client.start_workflow(DeliveryWorkflow.run, "dlv_1", id=f"d-{uuid.uuid4()}", task_queue=TASK_QUEUE)
        await h.signal(DeliveryWorkflow.receipt_received, "delivered")
        return await h.result()
    assert run(env, Fakes([]), delivered) == "delivered"

    async def silent(client):  # no receipt ever: the 10-minute wait is skipped through
        return await client.execute_workflow(DeliveryWorkflow.run, "dlv_2", id=f"d-{uuid.uuid4()}",
                                             task_queue=TASK_QUEUE)
    assert run(env, Fakes([]), silent) == "no_receipt"


def test_update_sms_after_five_quiet_minutes_and_at_call_end(env):
    fakes = Fakes([])

    async def body(client):
        h = await client.start_workflow(SessionUpdatesWorkflow.run, "sess_1", id=f"u-{uuid.uuid4()}",
                                        task_queue=TASK_QUEUE, start_signal="version_committed")
        await env.sleep(timedelta(minutes=6))          # quiet for 5 min -> one update
        await h.signal(SessionUpdatesWorkflow.version_committed)
        await env.sleep(timedelta(minutes=1))          # a newer version, then the call ends before 5 min
        await h.signal(SessionUpdatesWorkflow.call_ended)
        return await h.result()
    assert run(env, fakes, body) == 2 and fakes.finalized == ["sess_1", "sess_1"]


def test_a_long_document_runs_in_its_own_workflow_and_waits_its_turn_too(env):
    fakes = Fakes(["busy", "requeued", "committed"])

    async def body(client):
        return await client.execute_workflow(BigDocTaskWorkflow.run, "task_big", id=f"big-{uuid.uuid4()}",
                                             task_queue=TASK_QUEUE)
    assert run(env, fakes, body) == "committed" and fakes.outcomes == []
