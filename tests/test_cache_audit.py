"""Deterministic cache, audit trail, and version marker tests."""

from __future__ import annotations

import json
from typing import Any

from extractor.audit import AttemptRecord, RunTrace, extract_usage
from extractor.cache import CacheKey, ResultCache, canonical_fingerprint, sha256_text


def test_cache_key_is_order_stable() -> None:
    a = CacheKey("s", "x", "m", "b", "p", "o")
    b = CacheKey("s", "x", "m", "b", "p", "o")
    assert a.as_string() == b.as_string()
    c = CacheKey("s2", "x", "m", "b", "p", "o")
    assert c.as_string() != a.as_string()


def test_any_key_component_change_changes_key() -> None:
    base = CacheKey("s", "x", "m", "b", "p", "o")
    variants = [
        CacheKey("s2", "x", "m", "b", "p", "o"),
        CacheKey("s", "x2", "m", "b", "p", "o"),
        CacheKey("s", "x", "m2", "b", "p", "o"),
        CacheKey("s", "x", "m", "b2", "p", "o"),
        CacheKey("s", "x", "m", "b", "p2", "o"),
        CacheKey("s", "x", "m", "b", "p", "o2"),
    ]
    for v in variants:
        assert v.as_string() != base.as_string()


def test_canonical_fingerprint_sorts_keys() -> None:
    assert canonical_fingerprint({"a": 1, "b": 2}) == canonical_fingerprint({"b": 2, "a": 1})


def test_sha256_text_is_hex_and_stable() -> None:
    h = sha256_text("hello")
    assert len(h) == 64
    assert h == sha256_text("hello")


def test_result_cache_roundtrip_and_ttl(tmp_path: Any) -> None:
    cache = ResultCache(tmp_path, ttl_hours=1.0)
    cache.put("k", {"data": {"x": 1}})
    assert cache.get("k") == {"data": {"x": 1}}

    expired = ResultCache(tmp_path, ttl_hours=0.0)
    assert expired.get("k") is None


def test_result_cache_survives_corrupt_file(tmp_path: Any) -> None:
    cache = ResultCache(tmp_path, ttl_hours=1.0)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert cache.get("bad") is None


def _trace() -> RunTrace:
    return RunTrace(
        run_id="run-x",
        source_kind="html",
        source_origin="http://x",
        source_sha256="0" * 64,
        schema_name="product",
        schema_sha256="1" * 64,
        model="test-model",
        backend="instructor",
        attempts=[
            AttemptRecord(index=1, model="m", backend="instructor", ok=False,
                          validation_errors=["price: missing"]),
            AttemptRecord(index=2, model="m", backend="instructor", ok=True),
        ],
        outcome="success",
    )


def test_run_trace_jsonl_roundtrip(tmp_path: Any) -> None:
    trace = _trace()
    path = trace.to_jsonl(tmp_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "run"
    assert rows[1]["type"] == "attempt"
    assert rows[1]["validation_errors"] == ["price: missing"]
    assert rows[-1]["type"] == "outcome"

    restored = RunTrace.from_jsonl(path)
    assert restored.run_id == "run-x"
    assert restored.attempt_count == 2
    assert restored.attempts[0].validation_errors == ["price: missing"]
    assert restored.summary().startswith("run")


def test_extract_usage_tolerates_weird_shapes() -> None:
    class U:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15

    class Resp:
        usage = U()
        _hidden_params = {"response_cost": 0.001}

    usage, cost = extract_usage(Resp())
    assert usage["total_tokens"] == 15
    assert cost == 0.001

    usage2, cost2 = extract_usage(object())
    assert usage2 == {}
    assert cost2 is None
