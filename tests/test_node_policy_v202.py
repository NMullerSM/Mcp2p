"""Pruebas puras del dominio de política de nodos y cuarentena (WP-7)."""

from __future__ import annotations

import pytest

from src.core.domain.node_policy import (
    DEFAULT_POLICY,
    MAX_NODE_DISPLAY_NAME_CHARS,
    QUARANTINE_TTL_MS,
    IntakeDecision,
    NodePolicy,
    NodeRelation,
    decide_intake,
    format_fingerprint_short,
    sanitize_node_name,
)


@pytest.mark.parametrize(
    ("policy", "chain_approved", "pending", "expected"),
    [
        (NodePolicy.BLOCKED, False, 0, IntakeDecision("drop", "NODE_BLOCKED")),
        (NodePolicy.BLOCKED, True, 0, IntakeDecision("drop", "NODE_BLOCKED")),
        (NodePolicy.BLOCKED, True, 3, IntakeDecision("drop", "NODE_BLOCKED")),
        (NodePolicy.DIRECT, False, 0, IntakeDecision("deliver", None)),
        (NodePolicy.DIRECT, True, 2, IntakeDecision("deliver", None)),
        (NodePolicy.ASK_EACH, False, 0, IntakeDecision("park", "UNTRUSTED_NODE")),
        (NodePolicy.ASK_EACH, False, 4, IntakeDecision("park", "UNTRUSTED_NODE")),
        (NodePolicy.ASK_EACH, True, 0, IntakeDecision("deliver", None)),
        (NodePolicy.ASK_EACH, True, 2, IntakeDecision("deliver", None)),
        (NodePolicy.ASK_ONCE, False, 0, IntakeDecision("park", "UNTRUSTED_NODE")),
        (NodePolicy.ASK_ONCE, True, 0, IntakeDecision("park", "UNTRUSTED_NODE")),
        (NodePolicy.ASK_ONCE, False, 1, IntakeDecision("park", "UNTRUSTED_NODE_QUEUED")),
        (NodePolicy.ASK_ONCE, True, 5, IntakeDecision("park", "UNTRUSTED_NODE_QUEUED")),
    ],
)
def test_decide_intake_matrix(
    policy: NodePolicy,
    chain_approved: bool,
    pending: int,
    expected: IntakeDecision,
) -> None:
    assert (
        decide_intake(
            policy=policy,
            chain_approved=chain_approved,
            pending_from_node=pending,
        )
        == expected
    )


def test_defaults_and_constants() -> None:
    assert DEFAULT_POLICY[NodeRelation.SAME_OWNER] is NodePolicy.DIRECT
    assert DEFAULT_POLICY[NodeRelation.FOREIGN] is NodePolicy.ASK_EACH
    assert QUARANTINE_TTL_MS == 24 * 60 * 60 * 1000


def test_sanitize_node_name_and_fingerprint_short() -> None:
    dirty = "  Portátil\x00\x1b[31m\u202e\n  del   trabajo  " + ("x" * 60)
    clean = sanitize_node_name(dirty)
    assert "\x00" not in clean and "\x1b" not in clean and "\u202e" not in clean
    assert len(clean) == MAX_NODE_DISPLAY_NAME_CHARS
    assert sanitize_node_name(None) == ""
    assert format_fingerprint_short("a1b2c3d4e5f60708") == "A1B2-C3D4-…-E5F6-0708"
    assert format_fingerprint_short("00112233445566778899aabbccddeeff") == "0011-2233-…-CCDD-EEFF"
