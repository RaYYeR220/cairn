"""Turning a taint report into compensating actions.

Quarantining a lie tells you a decision was wrong; compensation tells you how to undo what the
decision already did. Each executed step is inverted by an effector-specific rule. A step that
was only planned needs nothing - it never ran.

Where the exact target of a compensation depends on state that existed before the incident (the
desired count a service had before we scaled it), the forward step is expected to have captured
it as `prior_state`. If it did not, the action is flagged so a human, or a live-state lookup,
resolves the target rather than guessing.
"""

from __future__ import annotations

from typing import Any, Callable

from .memory import TaintReport


def _invert_update_service(params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    revert = {"service": params["service"]}
    prior = params.get("prior_state", {})
    if "desired_count" in prior:
        revert["desired_count"] = prior["desired_count"]
        return revert, True
    return revert, False  # target must be resolved against live state


def _invert_rollback_deployment(params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    revert = {"service": params["service"]}
    prior = params.get("prior_state", {})
    if "task_definition" in prior:
        revert["task_definition"] = prior["task_definition"]
        return revert, True
    return revert, False


#: effector -> (inverse effector, param-inverter)
INVERSES: dict[str, tuple[str, Callable[[dict], tuple[dict, bool]]]] = {
    "ecs.update_service": ("ecs.update_service", _invert_update_service),
    "ecs.rollback_deployment": ("ecs.update_service", _invert_rollback_deployment),
}


def compensating_actions(report: TaintReport) -> list[dict[str, Any]]:
    """One compensating action per executed step under the revoked fact, most recent first."""
    actions: list[dict[str, Any]] = []
    for step in sorted(report.executed_steps, key=lambda s: s["step_no"], reverse=True):
        effector = step["effector"]
        if effector not in INVERSES:
            actions.append(
                {
                    "effector": effector,
                    "reverts_idem_key": step["idem_key"],
                    "params": {},
                    "resolved": False,
                    "note": "no inverse rule; compensation must be decided by an operator",
                }
            )
            continue
        inverse_effector, invert = INVERSES[effector]
        params, resolved = invert(step["params"])
        actions.append(
            {
                "effector": inverse_effector,
                "reverts_idem_key": step["idem_key"],
                "params": params,
                "resolved": resolved,
            }
        )
    return actions
