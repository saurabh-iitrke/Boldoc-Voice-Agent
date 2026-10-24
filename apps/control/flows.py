"""Durable workflows (Temporal) for the thinker and delivery. The orchestrator stays the single source of truth;
Temporal adds what a process can't: retries with backoff across crashes, durable timers, signals, and a history
of every task and SMS (Temporal UI, SSH tunnel to 127.0.0.1:8233 on the box).

  DocumentTask(task_id)     run the task; wait while another task is writing the same document; retry the
                            activity if the worker dies mid-run (heartbeats); ends at committed / failed.
  BigDocTask(task_id)       the same for a long document (T3): Hermes writes for minutes and may wait for the
                            caller's answers, so its one activity may take an hour; a retried attempt reattaches
                            to the same Hermes run (its id is stored on the task) and the draft is in Postgres.
  Delivery(delivery_id)     send the SMS at most once, then wait (up to 10 min) for Plivo's receipt signal.
  SessionUpdates(session)   durable timer: 5 min after the last new version (or at call end), send the
                            "updated" SMS for documents that changed since the caller last got the link.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Optional

from temporalio import activity, workflow
from temporalio.client import Client, WorkflowHandle
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from loguru import logger

TASK_QUEUE = "boldoc"
UPDATE_QUIET = timedelta(minutes=5)


@workflow.defn(name="DocumentTask")
class DocumentTaskWorkflow:
    @workflow.run
    async def run(self, task_id: str) -> str:
        for _ in range(300):
            outcome = await workflow.execute_activity(
                "run_task", task_id, start_to_close_timeout=timedelta(minutes=4),
                heartbeat_timeout=timedelta(seconds=20),  # beats every 10 s; a killed worker is retried fast
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0,
                                         maximum_attempts=4))
            if outcome == "busy":
                await asyncio.sleep(1.0)
            elif outcome == "requeued":
                await asyncio.sleep(2.0)
            else:
                return outcome  # committed | failed | skipped
        return "gave_up"


@workflow.defn(name="BigDocTask")
class BigDocTaskWorkflow:
    @workflow.run
    async def run(self, task_id: str) -> str:
        for _ in range(300):
            outcome = await workflow.execute_activity(
                "run_big_task", task_id, start_to_close_timeout=timedelta(minutes=60),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=5), backoff_coefficient=2.0,
                                         maximum_attempts=3))
            if outcome == "busy":
                await asyncio.sleep(1.0)
            elif outcome == "requeued":
                await asyncio.sleep(2.0)
            else:
                return outcome
        return "gave_up"


@workflow.defn(name="Delivery")
class DeliveryWorkflow:
    def __init__(self) -> None:
        self.receipt: Optional[str] = None

    @workflow.signal
    def receipt_received(self, status: str) -> None:
        self.receipt = status

    @workflow.run
    async def run(self, delivery_id: str) -> str:
        status = await workflow.execute_activity(
            "send_delivery", delivery_id, start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=1))  # an SMS is never sent twice by a retry
        if status != "submitted":
            return status
        try:
            await workflow.wait_condition(lambda: self.receipt is not None, timeout=timedelta(minutes=10))
        except asyncio.TimeoutError:
            return "no_receipt"
        return self.receipt or "unknown"


@workflow.defn(name="SessionUpdates")
class SessionUpdatesWorkflow:
    def __init__(self) -> None:
        self.dirty = False
        self.ended = False

    @workflow.signal
    def version_committed(self) -> None:
        self.dirty = True

    @workflow.signal
    def call_ended(self) -> None:
        self.ended = True

    @workflow.run
    async def run(self, session_id: str) -> int:
        sent = 0
        opts = {"start_to_close_timeout": timedelta(seconds=30),
                "retry_policy": RetryPolicy(maximum_attempts=3)}
        while not self.ended:
            await workflow.wait_condition(lambda: self.dirty or self.ended)
            if self.ended:
                break
            self.dirty = False
            try:  # quiet for 5 minutes after the last version -> tell the caller the link has a new version
                await workflow.wait_condition(lambda: self.dirty or self.ended, timeout=UPDATE_QUIET)
            except asyncio.TimeoutError:
                sent += await workflow.execute_activity("finalize_updates", session_id, **opts)
        sent += await workflow.execute_activity("finalize_updates", session_id, **opts)
        return sent


class Activities:
    """Activities run inside the control-plane process, next to the single-writer orchestrator."""

    def __init__(self, bg):
        self.bg = bg

    @activity.defn(name="run_task")
    async def run_task(self, task_id: str) -> str:
        info = activity.info()
        return await self.bg.run_task(task_id, f"temporal:{info.workflow_id}:{info.attempt}",
                                      beat=activity.heartbeat)

    @activity.defn(name="run_big_task")
    async def run_big_task(self, task_id: str) -> str:
        info = activity.info()
        return await self.bg.run_task(task_id, f"temporal:{info.workflow_id}:{info.attempt}",
                                      beat=activity.heartbeat)

    @activity.defn(name="send_delivery")
    async def send_delivery(self, delivery_id: str) -> str:
        return await self.bg.send_delivery(delivery_id)

    @activity.defn(name="finalize_updates")
    async def finalize_updates(self, session_id: str) -> int:
        if session_id not in self.bg.orch.s.sessions:
            return 0
        ids = self.bg.orch.finalize_updates(session_id)
        await self.bg.store.flush()
        if ids:
            self.bg.wake()
        return len(ids)


class TemporalFlows:
    def __init__(self, client: Client):
        self.client = client
        self.worker: Optional[Worker] = None
        self._run: Optional[asyncio.Task] = None

    @classmethod
    async def connect(cls, address: str, namespace: str = "default") -> TemporalFlows:
        return cls(await asyncio.wait_for(Client.connect(address, namespace=namespace), 5.0))

    def start_worker(self, bg) -> None:
        acts = Activities(bg)
        self.worker = Worker(self.client, task_queue=TASK_QUEUE,
                             workflows=[DocumentTaskWorkflow, BigDocTaskWorkflow, DeliveryWorkflow,
                                        SessionUpdatesWorkflow],
                             activities=[acts.run_task, acts.run_big_task, acts.send_delivery,
                                         acts.finalize_updates])
        self._run = asyncio.create_task(self.worker.run())

    async def stop(self) -> None:
        if self.worker is not None:
            await self.worker.shutdown()

    async def _start(self, wf, arg: str, wid: str) -> bool:
        try:
            await self.client.start_workflow(wf, arg, id=wid, task_queue=TASK_QUEUE,
                                             id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE)
            return True
        except WorkflowAlreadyStartedError:
            return True  # already running: nothing to do
        except Exception as e:  # Temporal down: the reconciler retries on its next pass
            logger.warning(f"could not start {wid}: {type(e).__name__}")
            return False

    async def start_task(self, task_id: str, wid: str, big: bool = False) -> bool:
        return await self._start(BigDocTaskWorkflow.run if big else DocumentTaskWorkflow.run, task_id, wid)

    async def start_delivery(self, delivery_id: str, wid: str) -> bool:
        return await self._start(DeliveryWorkflow.run, delivery_id, wid)

    async def version_committed(self, session_id: str) -> None:
        """Start (or signal) the session's update timer."""
        try:
            await self.client.start_workflow(SessionUpdatesWorkflow.run, session_id, id=f"updates-{session_id}",
                                             task_queue=TASK_QUEUE, start_signal="version_committed",
                                             id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE)
        except Exception as e:
            logger.warning(f"update timer for {session_id[-8:]} not started: {type(e).__name__}")

    async def signal(self, wid: str, signal: str, *args) -> None:
        # no such workflow (e.g. the call ended before any document): nothing to tell
        with contextlib.suppress(Exception):
            handle: WorkflowHandle = self.client.get_workflow_handle(wid)
            await handle.signal(signal, *args)
