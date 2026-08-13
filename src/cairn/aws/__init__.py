"""Live AWS integrations: CloudWatch Logs as the untrusted telemetry source, S3 for artifacts.

These are real service calls, kept behind small classes so the reproducible demo can run without
them. `boto3` is an optional dependency (`pip install -e '.[aws]'`).
"""
