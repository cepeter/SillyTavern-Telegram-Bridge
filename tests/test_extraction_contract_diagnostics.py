"""Extraction diagnostics and repair requests must expose contracts, never content."""

import json
import logging

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from test_durable_memory_workers import add
from test_memory_completion_safety import session_db as session_db

from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim


def events(caplog, name):
    return [r.diagnostic_fields for r in caplog.records if getattr(r, "diagnostic_fields", {}).get("event") == name]


@pytest.mark.parametrize(
    "raw,code,root,state_present,state_type",
    [
        ('{"blocks":[],"PRIVATE_CANARY":"secret"}', "scene_state_missing", "object", False, "missing"),
        ('{"state":[],"blocks":[]}', "scene_state_type", "object", True, "array"),
        ('{"state":{"PRIVATE_CANARY":"secret"},"blocks":[]}', "scene_state_invalid", "object", True, "object"),
        ("[]", "scene_root_type", "array", False, "missing"),
        ('{"state":{},"blocks":{}}', "scene_blocks_type", "object", True, "object"),
    ],
)
def test_scene_contract_diagnostics_and_precise_canonical_repair(
    session_db, caplog, raw, code, root, state_present, state_type
):
    settings, db, session = session_db
    add(db, "PRIVATE_SOURCE_CANARY")
    calls = []
    caplog.set_level(logging.INFO, logger="bridge.events")

    def generate(*args, **kwargs):
        calls.append((args[2], kwargs["settings"]))
        return raw if len(calls) == 1 else '{"state":{},"blocks":[]}'

    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("scene",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "complete"
    assert len(calls) == 2
    observed = events(caplog, "scene.contract_parsed")
    assert observed, "scene parser must emit safe contract diagnostics"
    assert observed[0]["rejection_code"] == code
    assert observed[0]["root_type"] == root
    assert observed[0]["state_present"] is state_present
    assert observed[0]["state_type"] == state_type
    instruction = calls[1][0][0]["content"]
    assert code in instruction and '{"state":{},"blocks":[]}' in instruction
    assert calls[0][0][-1] == calls[1][0][-1]
    assert calls[0][1]["max_tokens"] == calls[1][1]["max_tokens"] == 1000
    assert "PRIVATE_CANARY" not in instruction
    assert "PRIVATE" not in repr(observed)


@pytest.mark.parametrize(
    "payload,npc_code,simulation_code,npc_valid,simulation_valid",
    [
        ({"simulation": {}}, "npc_root_missing", "accepted", False, True),
        ({"npcs": {}, "simulation": []}, "npc_root_type", "simulation_root_type", False, False),
        ({"npcs": []}, "accepted", "simulation_root_missing", True, False),
        ({"npcs": [], "simulation": {"actor": []}}, "accepted", "simulation_invalid", True, False),
        (
            {
                "npcs": [
                    {
                        "name": "PRIVATE_NPC_CANARY",
                        "operations": [
                            {
                                "field": "role",
                                "op": "set",
                                "value": "PRIVATE_VALUE_CANARY",
                                "mode": "PRIVATE_MODE_CANARY",
                            }
                        ],
                    }
                ],
                "simulation": {},
            },
            "npc_mode_invalid",
            "accepted",
            False,
            True,
        ),
    ],
)
def test_tracker_independent_contracts_failed_repair_preserves_coverage(
    session_db, monkeypatch, caplog, payload, npc_code, simulation_code, npc_valid, simulation_valid
):
    from bridge import npc_extraction

    settings, db, session = session_db
    monkeypatch.setattr(npc_extraction, "persona_name", lambda *a, **k: "User")
    add(db, "PRIVATE_SOURCE_CANARY")
    calls = []
    caplog.set_level(logging.INFO, logger="bridge.events")

    def generate(*args, **kwargs):
        calls.append((args[2], kwargs["settings"]))
        return json.dumps(payload) if len(calls) == 1 else '{"npcs":{},"simulation":[]}'

    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("npc",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "invalid_npc_output"
    assert len(calls) == 2
    observed = events(caplog, "tracker.repair_start")
    assert observed[0].get("npc_valid") is npc_valid
    assert observed[0].get("simulation_valid") is simulation_valid
    assert observed[0]["npc_rejection_code"] == npc_code
    assert observed[0]["simulation_rejection_code"] == simulation_code
    prompt = calls[1][0][0]["content"]
    assert npc_code in prompt and simulation_code in prompt
    if not npc_valid:
        assert '"mode":"fixed"' in prompt and '"mode":"mutable"' in prompt
        assert calls[0][0][-1] == calls[1][0][-1]
    assert calls[0][1]["max_tokens"] == calls[1][1]["max_tokens"] == 1400
    assert "PRIVATE_MODE_CANARY" not in prompt
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='npc'").fetchone() == (0,)
    assert "PRIVATE" not in repr(observed)
    assert events(caplog, "tracker.repair_finish")[0]["accepted"] is False


@pytest.mark.parametrize(
    "raw,npc_code,sim_code,npcs_present,npcs_type,sim_present,sim_type",
    [
        ('{"simulation":{}}', "npc_root_missing", "accepted", False, "missing", True, "object"),
        ('{"npcs":[],"simulation":[]}', "accepted", "simulation_root_type", True, "array", True, "array"),
        ('{"npcs":null}', "npc_root_type", "simulation_root_missing", True, "null", False, "missing"),
        ('"PRIVATE_CANARY"', "npc_root_type", "simulation_root_type", False, "missing", False, "missing"),
        ("broken PRIVATE_CANARY", "malformed_json", "malformed_json", None, None, None, None),
    ],
)
def test_parser_root_fields_are_fixed_and_content_free(
    raw, npc_code, sim_code, npcs_present, npcs_type, sim_present, sim_type, caplog
):
    from bridge.npc_extraction import _parse_payload
    from bridge.simulation_extraction import parse_simulation_payload

    caplog.set_level(logging.INFO, logger="bridge.events")
    _, npc_valid = _parse_payload(raw, primary_name="Primary", user_name="User")
    _, sim_valid = parse_simulation_payload(raw)
    npc = events(caplog, "npc.contract_parsed")[0]
    sim = events(caplog, "simulation.contract_parsed")[0]
    assert npc["accepted"] is npc_valid and sim["accepted"] is sim_valid
    assert npc["rejection_code"] == npc_code and sim["rejection_code"] == sim_code
    for record in (npc, sim):
        assert record.get("npcs_present") is npcs_present
        assert record.get("npcs_type") == npcs_type
        assert record.get("simulation_present") is sim_present
        assert record.get("simulation_type") == sim_type
    assert "PRIVATE" not in caplog.text and "PRIVATE" not in repr((npc, sim))


@pytest.mark.parametrize("kind", ["npc", "simulation"])
@pytest.mark.parametrize(
    "raw,root_fields",
    [
        (None, {"root_type": "unparsed"}),
        (
            42,
            {
                "root_type": "number",
                "npcs_present": False,
                "npcs_type": "missing",
                "simulation_present": False,
                "simulation_type": "missing",
            },
        ),
    ],
)
def test_tracker_parsers_handle_non_string_raw_without_diagnostic_failure(kind, raw, root_fields, caplog):
    from bridge.npc_extraction import _parse_payload
    from bridge.simulation_extraction import parse_simulation_payload

    caplog.set_level(logging.INFO, logger="bridge.events")
    diagnostics = {}
    if kind == "npc":
        result = _parse_payload(raw, primary_name="Primary", user_name="User", diagnostics=diagnostics)
        assert result == ([], False)
    else:
        result = parse_simulation_payload(raw, diagnostics=diagnostics)
        assert result == ({}, False)
    code = "malformed_json" if raw is None else kind + "_root_type"
    expected_fields = {**root_fields, "rejection_code": code}
    assert diagnostics == expected_fields
    assert events(caplog, kind + ".contract_parsed") == [
        {**expected_fields, "accepted": False, "event": kind + ".contract_parsed"}
    ]
