import os
import uuid

import psycopg
import pytest

from cairn.embedding import DeterministicEmbedder
from cairn.gate import IntegrityGate
from cairn.ledger import Ledger
from cairn.memory import MemoryStore
from cairn.schema import apply_schema

ADMIN_URL = os.environ.get(
    "CAIRN_ADMIN_URL", "postgresql://root@localhost:26257/defaultdb?sslmode=disable"
)


def _database_url(name: str) -> str:
    return ADMIN_URL.replace("/defaultdb", f"/{name}")


@pytest.fixture(scope="session")
def test_database() -> str:
    """A throwaway database on the lab cluster, dropped when the session ends."""
    name = f"cairn_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(_database_url(name), autocommit=True) as conn:
            apply_schema(conn)
        yield _database_url(name)
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {name} CASCADE")


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def connect(test_database: str):
    """Open independent connections, so tests can model separate workers."""
    opened = []

    def _connect() -> psycopg.Connection:
        c = psycopg.connect(test_database, autocommit=True)
        opened.append(c)
        return c

    yield _connect
    for c in opened:
        c.close()


@pytest.fixture
def ledger(connect, tenant) -> Ledger:
    return Ledger(connect(), tenant_id=tenant)


@pytest.fixture
def other_worker(connect, tenant) -> Ledger:
    """A second worker on its own connection, sharing the tenant."""
    return Ledger(connect(), tenant_id=tenant)


@pytest.fixture
def embedder() -> DeterministicEmbedder:
    return DeterministicEmbedder(dimensions=1024)


@pytest.fixture
def store(connect, tenant, embedder) -> MemoryStore:
    return MemoryStore(connect(), tenant_id=tenant, embedder=embedder, gate=IntegrityGate())


@pytest.fixture
def store_with_broken_classifier(connect, tenant, embedder) -> MemoryStore:
    def explode(_content: str) -> float:
        raise RuntimeError("bedrock timed out")

    return MemoryStore(
        connect(),
        tenant_id=tenant,
        embedder=embedder,
        gate=IntegrityGate(classifier=explode),
    )


@pytest.fixture
def store_with_generous_classifier(connect, tenant, embedder) -> MemoryStore:
    return MemoryStore(
        connect(),
        tenant_id=tenant,
        embedder=embedder,
        gate=IntegrityGate(classifier=lambda _content: 0.0),
    )
