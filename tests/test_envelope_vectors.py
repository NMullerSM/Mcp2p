"""Sobre v1 (F1.1, Ola 3): vectores fijos, codificacion canonica y manipulacion.

Los vectores de `tests/vectors/envelope_v1.json` congelan el formato: si un cambio de codigo los rompe, es un cambio de
formato del sobre (y de interoperabilidad P2P), no un detalle.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.adapters.identity.envelope_crypto import (
    b64url,
    b64url_decode,
    raw_public_key,
    sign_header,
    verify_envelope,
)
from src.core.domain.envelope import (
    HEADER_FIELDS,
    EnvelopeError,
    build_header,
    canonical_json,
    content_hash,
    header_of,
    signing_bytes,
)
from src.core.domain.identity import (
    MAX_AGENT_TRUST,
    Principal,
    TrustLevel,
    cap_agent_trust,
    min_trust,
    parse_principal_uri,
)

VECTORS = json.loads((Path(__file__).parent / "vectors" / "envelope_v1.json").read_text(encoding="utf-8"))
SEED = bytes.fromhex(VECTORS["private_key_seed_hex"])
SK = ed25519.Ed25519PrivateKey.from_private_bytes(SEED)
PUB = VECTORS["public_key_b64url"]


def test_vector_key_matches_seed() -> None:
    assert b64url(raw_public_key(SK.public_key())) == PUB


@pytest.mark.parametrize("vec", VECTORS["valid"], ids=[v["name"] for v in VECTORS["valid"]])
def test_valid_vectors_canonical_bytes_signature_and_verification(vec) -> None:
    env = vec["envelope"]
    header = header_of(env)
    assert canonical_json(header).decode("utf-8") == vec["canonical_header"]
    assert b64url(signing_bytes(header)) == vec["signing_input_b64"]
    assert signing_bytes(header).startswith(b"MCP2P-ENV/1\n")
    # Ed25519 es determinista (RFC 8032): misma clave + mismos bytes = misma firma
    assert sign_header(SK, header)["sig"] == env["sig"]
    assert header["content_hash"] == content_hash(vec["content"])
    verdict = verify_envelope(env, vec["content"], PUB)
    assert verdict.ok and verdict.code == "OK"
    # el orden de las claves en memoria no cambia los bytes canonicos
    shuffled = dict(reversed(list(header.items())))
    assert canonical_json(shuffled) == canonical_json(header)


@pytest.mark.parametrize("vec", VECTORS["invalid"], ids=[v["name"] for v in VECTORS["invalid"]])
def test_negative_vectors_fail_with_their_code(vec) -> None:
    verdict = verify_envelope(vec["envelope"], vec["content"], vec.get("public_key", PUB), now_ms=vec.get("now_ms"))
    assert verdict.ok is False
    assert verdict.code == vec["expected"]


def _base():
    return VECTORS["valid"][1]


@pytest.mark.parametrize("field", [f for f in HEADER_FIELDS])
def test_changing_any_header_field_breaks_the_signature(field: str) -> None:
    env = copy.deepcopy(_base()["envelope"])
    original = env[field]
    if isinstance(original, bool) or original is None:
        env[field] = {"x": 1} if field == "device_cert" else "otro-id"
    elif isinstance(original, int):
        env[field] = original + 1
    elif field in ("from", "from_device", "to"):
        env[field] = "pane:otro-panel"
    elif field == "trust":
        env[field] = "system"
    elif field == "kind":
        env[field] = "note"
    elif field == "content_hash":
        env[field] = "sha256:" + "0" * 64
    else:
        env[field] = original + "x"
    verdict = verify_envelope(env, _base()["content"], PUB)
    assert verdict.ok is False, field


def test_every_single_bit_of_the_content_is_covered() -> None:
    vec = VECTORS["valid"][0]
    raw = vec["content"].encode("utf-8")
    for idx in range(len(raw)):
        for bit in (0x01, 0x40):
            mutated = bytearray(raw)
            mutated[idx] ^= bit
            try:
                text = mutated.decode("utf-8")
            except UnicodeDecodeError:
                continue
            assert verify_envelope(vec["envelope"], text, PUB).code == "CONTENT_HASH_MISMATCH"


def test_every_single_bit_of_the_signature_is_covered() -> None:
    env = copy.deepcopy(VECTORS["valid"][0]["envelope"])
    raw = b64url_decode(env["sig"]["value"])
    for idx in range(len(raw)):
        mutated = bytearray(raw)
        mutated[idx] ^= 0x80
        env["sig"]["value"] = b64url(bytes(mutated))
        assert verify_envelope(env, VECTORS["valid"][0]["content"], PUB).ok is False


def test_canonical_json_rejects_floats_nan_and_non_nfc() -> None:
    for bad in ({"a": 1.0}, {"a": float("nan")}, {"a": "é"}, {1: "x"}, {"a": 2**60}):
        with pytest.raises(EnvelopeError):
            canonical_json(bad)
    assert canonical_json({"b": 1, "a": [True, None, "ñ"]}) == '{"a":[true,null,"ñ"],"b":1}'.encode("utf-8")


def test_build_header_validates_schema() -> None:
    kwargs = dict(
        msg_id="m1", kind="note", from_uri="pane:a", from_device="device:d", to_uri="pane:b", project_uuid="p",
        chain_id="c", hop=0, parent_msg_id=None, content="hola", created_at=10, expires_at=20,
        trust="pane_authenticated",
    )
    header = build_header(**kwargs)
    assert set(header) == set(HEADER_FIELDS)
    for bad in ({"kind": "keyboard"}, {"trust": "root"}, {"expires_at": 5}, {"from_uri": "sin-dos-puntos"},
                {"hop": -1}, {"to_uri": "robot:x"}):
        with pytest.raises(EnvelopeError):
            build_header(**{**kwargs, **bad})


def test_principals_and_no_escalation_rules() -> None:
    p = Principal.pane("pane-7a1b2c3d-1-0badc0de", project_uuid="proj")
    assert p.uri == "pane:pane-7a1b2c3d-1-0badc0de"
    assert parse_principal_uri(p.uri).id == p.id
    for bad in ("", "x y", "a" * 300, "é"):
        with pytest.raises(ValueError):
            Principal.pane(bad)
    with pytest.raises(ValueError):
        parse_principal_uri("root:x")
    # un agente nunca sube de pane_authenticated, ni aunque pida system o human_confirmed
    assert MAX_AGENT_TRUST is TrustLevel.PANE_AUTHENTICATED
    for asked in TrustLevel:
        assert cap_agent_trust(asked) in (TrustLevel.UNVERIFIED, TrustLevel.PANE_AUTHENTICATED)
    assert cap_agent_trust("system", chain_trust="unverified") is TrustLevel.UNVERIFIED
    assert min_trust("system", "pane_authenticated") is TrustLevel.PANE_AUTHENTICATED
