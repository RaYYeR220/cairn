"""The write-ahead intent ledger.

An intent is committed before its side effect runs, so a worker that dies mid-step leaves
behind an unambiguous record of what it was about to do.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

_FIELD_SEPARATOR = "\x1f"


def canonical_params(params: dict[str, Any]) -> str:
    """Serialise parameters so that logically equal parameters produce equal text."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_key(run_id: UUID, step_no: int, effector: str, params: dict[str, Any]) -> str:
    """Derive the key that collapses a replayed step onto its original side effect."""
    material = _FIELD_SEPARATOR.join(
        [str(run_id), str(step_no), effector, canonical_params(params)]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class LeaseLost(Exception):
    """Raised when a worker tries to write a result for a step it no longer owns."""


@dataclass(frozen=True)
class Intent:
    idem_key: str
    run_id: UUID
    step_no: int
    effector: str
    params: dict[str, Any]


@dataclass(frozen=True)
class Claim:
    """Ownership of one step, fenced by the lease epoch it was granted under."""

    intent: Intent
    epoch: int
    worker: str


class Ledger:
    def __init__(self, conn: psycopg.Connection, tenant_id: UUID) -> None:
        self._conn = conn
        self._tenant_id = tenant_id

    def open_run(self, title: str) -> UUID:
        row = self._conn.execute(
            "INSERT INTO agent_run (tenant_id, title) VALUES (%s, %s) RETURNING run_id",
            (self._tenant_id, title),
        ).fetchone()
        return row[0]

    def record_intent(
        self, run_id: UUID, step_no: int, effector: str, params: dict[str, Any]
    ) -> Intent:
        key = idempotency_key(run_id, step_no, effector, params)
        self._conn.execute(
            """
            INSERT INTO step_intent (tenant_id, idem_key, run_id, step_no, effector, params)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, idem_key) DO NOTHING
            """,
            (self._tenant_id, key, run_id, step_no, effector, Jsonb(params)),
        )
        return Intent(
            idem_key=key, run_id=run_id, step_no=step_no, effector=effector, params=params
        )

    def claim_next(self, run_id: UUID, worker: str, lease_seconds: int) -> Claim | None:
        """Take ownership of the lowest-numbered step that has no result and no live lease."""
        row = self._conn.execute(
            """
            UPDATE step_intent
               SET lease_owner      = %(worker)s,
                   lease_expires_at = now() + (%(lease)s::FLOAT8 * INTERVAL '1 second'),
                   lease_epoch      = lease_epoch + 1,
                   attempts         = attempts + 1
             WHERE (tenant_id, idem_key) IN (
                     SELECT i.tenant_id, i.idem_key
                       FROM step_intent AS i
                       LEFT JOIN step_result AS r
                              ON r.tenant_id = i.tenant_id AND r.idem_key = i.idem_key
                      WHERE i.tenant_id = %(tenant)s
                        AND i.run_id = %(run)s
                        AND r.idem_key IS NULL
                        AND (i.lease_owner IS NULL OR i.lease_expires_at < now())
                      ORDER BY i.step_no
                      LIMIT 1
                   )
         RETURNING idem_key, run_id, step_no, effector, params, lease_epoch
            """,
            {
                "worker": worker,
                "lease": float(lease_seconds),
                "tenant": self._tenant_id,
                "run": run_id,
            },
        ).fetchone()
        if row is None:
            return None
        idem_key, claimed_run, step_no, effector, params, epoch = row
        return Claim(
            intent=Intent(
                idem_key=idem_key,
                run_id=claimed_run,
                step_no=step_no,
                effector=effector,
                params=params,
            ),
            epoch=epoch,
            worker=worker,
        )

    def record_result(
        self,
        claim: Claim,
        outcome: str,
        observed_state: dict[str, Any] | None = None,
    ) -> None:
        """Write the outcome of a step, but only if this worker still owns it.

        Ownership is fenced by the lease epoch rather than by the expiry time: a worker whose
        lease lapsed without anyone taking over is still the rightful owner, while a worker
        that was superseded is locked out even if its own clock disagrees.
        """
        result = self._conn.execute(
            """
            INSERT INTO step_result (tenant_id, idem_key, outcome, observed_state, lease_epoch)
            SELECT %(tenant)s, %(key)s, %(outcome)s, %(state)s, %(epoch)s
             WHERE EXISTS (
                     SELECT 1 FROM step_intent
                      WHERE tenant_id = %(tenant)s
                        AND idem_key = %(key)s
                        AND lease_epoch = %(epoch)s
                   )
            """,
            {
                "tenant": self._tenant_id,
                "key": claim.intent.idem_key,
                "outcome": outcome,
                "state": Jsonb(observed_state) if observed_state is not None else None,
                "epoch": claim.epoch,
            },
        )
        if result.rowcount == 0:
            raise LeaseLost(
                f"step {claim.intent.step_no} of run {claim.intent.run_id} was taken over "
                f"by another worker while {claim.worker} was executing it"
            )

    def count_intents(self, run_id: UUID) -> int:
        row = self._conn.execute(
            "SELECT count(*) FROM step_intent WHERE tenant_id = %s AND run_id = %s",
            (self._tenant_id, run_id),
        ).fetchone()
        return row[0]
