"""Connection helpers.

CockroachDB Cloud presents certificates from a public CA. `sslmode=verify-full` therefore needs a
trust store, and the one shipped with certifi is present wherever this package is installed - more
portable than relying on the OS store, which libpq cannot always locate on Windows. This helper
injects certifi's bundle as `sslrootcert` when a verifying URL does not already name one.
"""

from __future__ import annotations

import os
import time

import psycopg


def _with_ca(url: str) -> str:
    if "sslmode=verify" not in url or "sslrootcert=" in url:
        return url
    try:
        import certifi
    except ImportError:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sslrootcert={certifi.where()}"


def connect(
    url: str | None = None, autocommit: bool = True, retries: int = 5, **kwargs
) -> psycopg.Connection:
    """Open a connection, resolving the CA bundle for a verifying TLS URL.

    A serverless CockroachDB Cloud cluster resets the first connection or two while it wakes a
    SQL pod, surfacing as a transient `OperationalError`. Retrying with a short backoff turns a
    cold start into a non-event - the same resilience an agent needs from its memory layer.
    """
    url = _with_ca(url or os.environ["CAIRN_DATABASE_URL"])
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return psycopg.connect(url, autocommit=autocommit, **kwargs)
        except psycopg.OperationalError as exc:
            last = exc
            time.sleep(min(1.0 + attempt, 4.0))
    raise last  # type: ignore[misc]
