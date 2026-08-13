"""S3 as the agent's artifact store.

After an incident the agent writes a durable record - the postmortem it will remember, and the full
run for audit - to S3. Object keys are content-addressed by run so a replay overwrites rather than
duplicates.
"""

from __future__ import annotations

import json
import os


def _client(region: str | None = None):
    import boto3

    region = region or os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("s3", region_name=region)


class S3Artifacts:
    def __init__(self, bucket: str, client=None) -> None:
        self._s3 = client or _client()
        self.bucket = bucket

    def ensure(self) -> None:
        try:
            self._s3.create_bucket(Bucket=self.bucket)  # us-east-1 needs no LocationConstraint
        except self._s3.exceptions.BucketAlreadyOwnedByYou:
            pass
        except self._s3.exceptions.BucketAlreadyExists:
            pass

    def put_postmortem(self, run_id: str, text: str) -> str:
        key = f"runs/{run_id}/postmortem.txt"
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode("utf-8"),
                            ContentType="text/plain")
        return f"s3://{self.bucket}/{key}"

    def put_run(self, run_id: str, run: dict) -> str:
        key = f"runs/{run_id}/run.json"
        self._s3.put_object(Bucket=self.bucket, Key=key,
                            Body=json.dumps(run, indent=2, default=str).encode("utf-8"),
                            ContentType="application/json")
        return f"s3://{self.bucket}/{key}"

    def get_text(self, uri: str) -> str:
        key = uri.split(f"{self.bucket}/", 1)[1]
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
