"""AWS-backed transport for the simulation worker.

Implements the ``TaskQueue`` and ``TaskReporter`` contracts from
``worker_transport`` against Amazon SQS and AWS Step Functions. boto3 is
imported lazily inside the constructors so that importing this module -- or
running the worker against the in-memory transport -- never requires boto3 and
never reaches the network. Nothing here executes work; it only moves messages
and callbacks.
"""

from __future__ import annotations

import json
from typing import Any

from .worker_transport import WorkItem


class SqsTaskQueue:
    """A FIFO SQS queue exposed through the ``TaskQueue`` contract."""

    def __init__(self, queue_url: str, *, region_name: str | None = None, client: Any = None) -> None:
        if not queue_url:
            raise ValueError("queue_url is required")
        self.queue_url = queue_url
        if client is not None:
            self._sqs = client
        else:
            import boto3  # lazy: only needed for the real AWS path

            self._sqs = boto3.client("sqs", region_name=region_name)

    def receive(self, max_items: int, wait_seconds: int) -> list[WorkItem]:
        resp = self._sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max(1, min(10, int(max_items))),
            WaitTimeSeconds=max(0, min(20, int(wait_seconds))),
            # Surface the FIFO sequencing attributes; harmless for standard queues.
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        items: list[WorkItem] = []
        for message in resp.get("Messages", []) or []:
            try:
                body = json.loads(message.get("Body") or "{}")
            except json.JSONDecodeError:
                # A body we cannot parse is undeletable here; leave it to redrive
                # and the DLQ rather than silently dropping it.
                continue
            items.append(WorkItem.from_message(body, receipt=message.get("ReceiptHandle")))
        return items

    def delete(self, item: WorkItem) -> None:
        if not item.receipt:
            return
        self._sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=item.receipt)


class SqsTaskProducer:
    """Enqueues dispatch messages onto a FIFO SQS queue (``TaskProducer``).

    ``group_id`` maps to ``MessageGroupId`` (FIFO ordering per run) and
    ``dedup_id`` to ``MessageDeduplicationId`` (content de-dup), so a resend of
    the same step is collapsed by SQS before a worker ever sees it.
    """

    def __init__(self, queue_url: str, *, region_name: str | None = None, client: Any = None) -> None:
        if not queue_url:
            raise ValueError("queue_url is required")
        self.queue_url = queue_url
        if client is not None:
            self._sqs = client
        else:
            import boto3  # lazy: only needed for the real AWS path

            self._sqs = boto3.client("sqs", region_name=region_name)

    def send(self, body: dict[str, Any], *, group_id: str, dedup_id: str) -> None:
        self._sqs.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(body, default=str),
            MessageGroupId=group_id or "default",
            MessageDeduplicationId=(dedup_id or group_id or "default")[:128],
        )


class StepFunctionsReporter:
    """Reports task outcomes to Step Functions via the task-token callbacks."""

    def __init__(self, *, region_name: str | None = None, client: Any = None) -> None:
        if client is not None:
            self._sfn = client
        else:
            import boto3  # lazy

            self._sfn = boto3.client("stepfunctions", region_name=region_name)

    def report_success(self, task_token: str, output: dict[str, Any]) -> None:
        self._sfn.send_task_success(taskToken=task_token, output=json.dumps(output))

    def report_failure(self, task_token: str, error: str, cause: str) -> None:
        # Step Functions caps error (256) and cause (32768) lengths; trim defensively.
        self._sfn.send_task_failure(
            taskToken=task_token,
            error=error[:256],
            cause=cause[:32768],
        )

    def heartbeat(self, task_token: str) -> None:
        self._sfn.send_task_heartbeat(taskToken=task_token)
