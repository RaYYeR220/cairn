"""CloudWatch Logs as the agent's telemetry source.

The agent treats whatever it reads here as *evidence, never instructions*. That is the whole point
of routing telemetry through a real, world-writable log: anyone who can append to the log group can
put a line in front of the agent, which is exactly the attack the integrity gate defends against.

`publish` is how the demo injects an incident (including the poisoned line); `read` is the agent's
observe step. In production only `read` runs — the logs are written by the workload, not by us.
"""

from __future__ import annotations

import time

DEFAULT_LOG_GROUP = "/cairn/checkout-api"


def _client(region: str | None = None):
    import boto3
    import os

    region = region or os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("logs", region_name=region)


class CloudWatchTelemetry:
    def __init__(self, log_group: str = DEFAULT_LOG_GROUP, client=None) -> None:
        self._logs = client or _client()
        self.log_group = log_group

    def ensure(self) -> None:
        try:
            self._logs.create_log_group(logGroupName=self.log_group)
        except self._logs.exceptions.ResourceAlreadyExistsException:
            pass
        try:
            self._logs.put_retention_policy(logGroupName=self.log_group, retentionInDays=1)
        except Exception:
            pass

    def publish(self, signals: list[str], stream: str, at_ms: int) -> None:
        """Append signals to a log stream. `at_ms` is passed in because scripts cannot read a clock."""
        self.ensure()
        try:
            self._logs.create_log_stream(logGroupName=self.log_group, logStreamName=stream)
        except self._logs.exceptions.ResourceAlreadyExistsException:
            pass
        events = [{"timestamp": at_ms + i, "message": s} for i, s in enumerate(signals)]
        self._logs.put_log_events(logGroupName=self.log_group, logStreamName=stream, logEvents=events)

    def read(self, stream: str, expect: int = 0, timeout_s: float = 20.0) -> list[str]:
        """Read a stream's messages back, polling until they are queryable (CloudWatch lags a few s)."""
        deadline = time.monotonic() + timeout_s
        while True:
            resp = self._logs.get_log_events(
                logGroupName=self.log_group, logStreamName=stream, startFromHead=True)
            messages = [e["message"] for e in resp.get("events", [])]
            if len(messages) >= expect or time.monotonic() > deadline:
                return messages
            time.sleep(1.5)

    def delete(self) -> None:
        try:
            self._logs.delete_log_group(logGroupName=self.log_group)
        except Exception:
            pass
